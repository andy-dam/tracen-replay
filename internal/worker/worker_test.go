package worker

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testdata(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "testdata", "worker", name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	return data
}

func TestArgvBuildsTheDocumentedCommandWithoutAShell(t *testing.T) {
	argv, err := Command{Python: "py", WorkDir: "w", Source: "in/a b.mp4", Output: "out/run-1", ModelDir: "models", Workers: 4, PruneFrames: true}.Argv()
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"py", "-X", "utf8", "-m", "tracen_replay.analysis_job", filepath.Clean("in/a b.mp4"), "--output", filepath.Clean("out/run-1"),
		"--workers", "4", "--model-dir", "models", "--prune-frames"}
	if strings.Join(argv, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("argv = %q, want %q", argv, want)
	}
	if _, err := (Command{Python: "py", WorkDir: "w", Source: "s", Output: "o", Workers: 0}).Argv(); err == nil {
		t.Fatal("zero workers must be rejected")
	}
	argv, _ = Command{Python: "py", WorkDir: "w", Source: "s", Output: "o", Workers: 2, FPS: 2.5, DenseWorkers: 3}.Argv()
	if joined := strings.Join(argv, " "); !strings.Contains(joined, "--fps 2.5") || !strings.Contains(joined, "--dense-workers 3") {
		t.Fatalf("fps or dense workers missing from %q", argv)
	}
	if _, err := (Command{Python: "py", WorkDir: "w", Source: "s", Output: "o", Workers: 2, DenseWorkers: -1}).Argv(); err == nil {
		t.Fatal("negative dense workers must be rejected")
	}
	if _, err := (Command{Python: "py", WorkDir: "w", Source: "s", Output: "o", Workers: 2, OCRDevice: "tpu"}).Argv(); err == nil {
		t.Fatal("unknown ocr device must be rejected")
	}
	if env := (Command{OCRDevice: "cpu"}).Env(); len(env) != 1 || env[0] != "TRACEN_REPLAY_OCR_DEVICE=cpu" {
		t.Fatalf("env: %v", env)
	}
	if env := (Command{}).Env(); env != nil {
		t.Fatalf("empty device must add nothing: %v", env)
	}
}

func TestParseProgressReadsARealFreshRunTranscript(t *testing.T) {
	scanner := bufio.NewScanner(strings.NewReader(string(testdata(t, "fresh-run.stderr.log"))))
	scanner.Buffer(make([]byte, 1<<20), 1<<20)
	var done, ocr, other, ignored int
	var lastPercent float64
	stages := map[string]bool{}
	for scanner.Scan() {
		p, ok := ParseProgress(scanner.Text())
		if !ok {
			ignored++
			continue
		}
		switch p.Stage {
		case StageDone:
			done++
			stages[p.Name] = true
			if p.WallSeconds <= 0 {
				t.Fatalf("stage_done without wall_s: %+v", p)
			}
		case StageOCR:
			ocr++
			pct, ok := p.Percent()
			if !ok || pct < lastPercent {
				t.Fatalf("ocr percent not monotonic: %v after %v", pct, lastPercent)
			}
			lastPercent = pct
		default:
			other++
			if _, ok := p.Percent(); ok {
				t.Fatalf("only the ocr line has a percentage: %+v", p)
			}
		}
	}
	if done < 10 || ocr < 10 || ignored < 5 {
		t.Fatalf("done=%d ocr=%d other=%d ignored=%d: the transcript should have stage lines, ocr lines and library warnings", done, ocr, other, ignored)
	}
	for _, name := range []string{"capture", "base_readings", "assemble", "save_report", "timeline_document"} {
		if !stages[name] {
			t.Fatalf("stage %q missing from the transcript", name)
		}
	}
	if lastPercent != 100 {
		t.Fatalf("final ocr percent %v, want 100", lastPercent)
	}
}

func TestParseProgressHandlesFailuresAndNoise(t *testing.T) {
	p, ok := ParseProgress(`{"name": "occluded_receipt_recovery", "error": "PipelineError: Receipt OCR model changed", "stage": "stage_failed", "wall_s": 2256.1}`)
	if !ok || p.Stage != StageFailed || p.Label() != "occluded_receipt_recovery" || !strings.HasPrefix(p.Error, "PipelineError") {
		t.Fatalf("stage_failed parsed as %+v", p)
	}
	for _, line := range []string{"", "  ", "\x1b[33m[WARNING] RapidOCR text detection result is empty\x1b[0m", `{"no_stage": 1}`, `not json {`, `[1,2]`} {
		if _, ok := ParseProgress(line); ok {
			t.Fatalf("line %q must be ignored", line)
		}
	}
	if (Progress{Stage: StageOCR, Processed: 3, Total: 0}).Label() != "ocr" {
		t.Fatal("ocr label")
	}
}

func TestDecodeTerminalAcceptsTheRealSucceededObject(t *testing.T) {
	r, err := DecodeTerminal(testdata(t, "succeeded.stdout.json"))
	if err != nil {
		t.Fatal(err)
	}
	if r.Status != StatusSucceeded || !r.Completed() || r.ReportSchemaVersion != ReportSchemaVersion || r.TimelinePath == "" || r.FullyVerified {
		t.Fatalf("unexpected result %+v", r)
	}
	versioned := `{"schema_version":"tracen-replay/analysis-job-v1","status":"failed","error":{"code":"x","message":"m"},"worker_version":{"package":"0.1.0","code_digest":"abc"}}`
	if v, err := DecodeTerminal([]byte(versioned)); err != nil || v.WorkerVersion == nil || v.WorkerVersion.CodeDigest != "abc" {
		t.Fatalf("worker_version must be decoded when present: %+v %v", v, err)
	}
	if _, err := Interpret(ExitSuccess, testdata(t, "succeeded.stdout.json")); err != nil {
		t.Fatal(err)
	}
	var ce *ContractError
	if _, err := Interpret(ExitProducer, testdata(t, "succeeded.stdout.json")); !errors.As(err, &ce) || ce.Code != CodeStatusExitMismatch {
		t.Fatalf("success object with exit 1 must be %s, got %v", CodeStatusExitMismatch, err)
	}
}

func TestDecodeTerminalRejectsMalformedAndForeignObjects(t *testing.T) {
	cases := map[string]string{
		"":         CodeBadTerminalOutput,
		"not json": CodeBadTerminalOutput,
		`{"schema_version":"tracen-replay/analysis-job-v2","status":"succeeded"}`: CodeSchemaMismatch,
		`{"schema_version":"tracen-replay/analysis-job-v1","status":"weird"}`:     CodeBadTerminalOutput,
		`{"schema_version":"tracen-replay/analysis-job-v1","status":"failed"}`:    CodeBadTerminalOutput,
		`{"schema_version":"tracen-replay/analysis-job-v1","status":"succeeded","report_schema_version":"tracen-replay/full-recording-v1","report_path":"r","evidence_root":"e","report_sha256":"abc","source_sha256":"abc"}`:        CodeBadTerminalOutput,
		`{"schema_version":"tracen-replay/analysis-job-v1","status":"failed","error":{"code":"x","message":"m"}}` + "\n" + `{"schema_version":"tracen-replay/analysis-job-v1","status":"failed","error":{"code":"y","message":"m"}}`: CodeMultipleObjects,
	}
	for input, code := range cases {
		var ce *ContractError
		if _, err := DecodeTerminal([]byte(input)); !errors.As(err, &ce) || ce.Code != code {
			t.Errorf("input %q: got %v, want code %s", input, err, code)
		}
	}
}

func TestInterpretFailureEnvelopes(t *testing.T) {
	failed := `{"schema_version":"tracen-replay/analysis-job-v1","status":"failed","error":{"code":"source_mismatch","message":"The source file changed while the producer was running."}}`
	r, err := Interpret(ExitProducer, []byte(failed))
	if err != nil || r.Error == nil || r.Error.Code != "source_mismatch" || r.Completed() {
		t.Fatalf("failed envelope: %+v %v", r, err)
	}
	var ce *ContractError
	if _, err := Interpret(ExitSuccess, []byte(failed)); !errors.As(err, &ce) || ce.Code != CodeStatusExitMismatch {
		t.Fatalf("failed status with exit 0 must be %s, got %v", CodeStatusExitMismatch, err)
	}
	if _, err := Interpret(ExitInput, nil); !errors.As(err, &ce) || ce.Code != CodeBadTerminalOutput {
		t.Fatalf("exit 2 without output must be %s, got %v", CodeBadTerminalOutput, err)
	}
	warned := `{"schema_version":"tracen-replay/analysis-job-v1","status":"completed_with_stage_failures","report_schema_version":"tracen-replay/full-recording-v1","report_path":"r","evidence_root":"e","report_sha256":"` + strings.Repeat("a", 64) + `","source_sha256":"` + strings.Repeat("b", 64) + `","stage_failures":[{"stage":"hint_card_preparation","error":"ValueError: x"}]}`
	r, err = Interpret(ExitSuccess, []byte(warned))
	if err != nil || !r.Completed() || len(r.StageFailures) != 1 || r.StageFailures[0].Stage != "hint_card_preparation" {
		t.Fatalf("completed_with_stage_failures: %+v %v", r, err)
	}
}

func TestVerifyChecksTheReportHashAndTheTimelineSchema(t *testing.T) {
	dir := t.TempDir()
	report := filepath.Join(dir, "report.json")
	payload := []byte(`{"schema_version":"tracen-replay/full-recording-v1"}`)
	if err := os.WriteFile(report, payload, 0o644); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(payload)
	result := Result{SchemaVersion: SchemaVersion, Status: StatusSucceeded, ReportSchemaVersion: ReportSchemaVersion,
		ReportPath: report, EvidenceRoot: dir, ReportSHA256: hex.EncodeToString(sum[:]), SourceSHA256: strings.Repeat("c", 64)}
	var ce *ContractError
	if _, err := Verify(result); !errors.As(err, &ce) || ce.Code != CodeTimelineMissing {
		t.Fatalf("missing timeline must be %s, got %v", CodeTimelineMissing, err)
	}
	timeline := filepath.Join(dir, "timeline.json")
	if err := os.WriteFile(timeline, []byte(`{"schema_version":"tracen-replay/timeline-v0"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Verify(result); !errors.As(err, &ce) || ce.Code != CodeTimelineInvalid {
		t.Fatalf("foreign timeline schema must be %s, got %v", CodeTimelineInvalid, err)
	}
	if err := os.WriteFile(timeline, []byte(`{"schema_version":"tracen-replay/timeline-v1","turns":[]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	artifacts, err := Verify(result)
	if err != nil || artifacts.TimelinePath != timeline || artifacts.ReportSHA256 != result.ReportSHA256 {
		t.Fatalf("verify: %+v %v", artifacts, err)
	}
	result.ReportSHA256 = strings.Repeat("0", 64)
	if _, err := Verify(result); !errors.As(err, &ce) || ce.Code != CodeReportHashMismatch {
		t.Fatalf("hash mismatch must be %s, got %v", CodeReportHashMismatch, err)
	}
	result.ReportPath = filepath.Join(dir, "absent.json")
	if _, err := Verify(result); !errors.As(err, &ce) || ce.Code != CodeReportMissing {
		t.Fatalf("missing report must be %s, got %v", CodeReportMissing, err)
	}
}
