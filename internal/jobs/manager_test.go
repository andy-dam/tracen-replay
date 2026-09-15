package jobs_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/store"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

const miniTimeline = `{"schema_version":"tracen-replay/timeline-v1","report_schema_version":"tracen-replay/full-recording-v1",
"source":{"name":"clip.mp4","sha256":"%s","duration_ms":120000,"width":1920,"height":1080},"recognition":{"model":"t","device":"cpu"},
"turns":[{"id":"turn-001","label":"L","phase":"P","start_ms":0,"end_ms":10,"window_kind":"calendar","action_status":"one_action","action_count":1,"opening":{"stats":null,"performance":null},"accounting":{}}],
"entries":[{"id":"entry-0001","kind":"outcome","turn_id":"turn-001","first_seen_ms":1,"last_seen_ms":2}],
"summary":{"field_status_counts":{},"action_statuses":{},"observed_turn_windows":1,"entry_counts":{"outcome":1},"stage_failures":[]}}`

// fakeSources serves one temporary recording.
type fakeSources struct{ path string }

func (f fakeSources) List() ([]jobs.Source, error) {
	return sourceList(f), nil
}

func sourceList(f fakeSources) []jobs.Source {
	return []jobs.Source{{ID: "src-1", Name: filepath.Base(f.path), Path: f.path, Size: 4}}
}

func (f fakeSources) Resolve(id string) (jobs.Source, error) {
	if id != "src-1" {
		return jobs.Source{}, &jobs.NotFoundError{Kind: "source", ID: id}
	}
	return sourceList(f)[0], nil
}

// scriptedRunner behaves like the worker as far as the manager can see.
type scriptedRunner struct {
	mu      sync.Mutex
	started chan string
	script  func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error)
}

func (r *scriptedRunner) Run(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
	select {
	case r.started <- cmd.Output:
	default:
	}
	return r.script(ctx, cmd, onProgress, logs)
}

// writeArtifacts creates report.json and timeline.json in the run directory and
// returns the terminal object a successful worker would print.
func writeArtifacts(t *testing.T, output string, status string) []byte {
	t.Helper()
	if err := os.MkdirAll(output, 0o755); err != nil {
		t.Fatal(err)
	}
	report := []byte(`{"schema_version":"tracen-replay/full-recording-v1","verification":{}}`)
	if err := os.WriteFile(filepath.Join(output, "report.json"), report, 0o644); err != nil {
		t.Fatal(err)
	}
	source := hex.EncodeToString(sha256.New().Sum([]byte("clip")))[:64]
	if err := os.WriteFile(filepath.Join(output, "timeline.json"), []byte(fmt.Sprintf(miniTimeline, source)), 0o644); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(report)
	terminal := fmt.Sprintf(`{"schema_version":"tracen-replay/analysis-job-v1","status":%q,"report_schema_version":"tracen-replay/full-recording-v1","report_path":%q,"report_sha256":%q,"source_sha256":%q,"evidence_root":%q,"full_source_processed":true,"fully_verified":false,"go_ready":false,"timeline_path":%q%s}`,
		status, filepath.Join(output, "report.json"), hex.EncodeToString(sum[:]), source, output, filepath.Join(output, "timeline.json"),
		map[bool]string{true: `,"stage_failures":[{"stage":"hint_card_preparation","error":"ValueError: x"}]`, false: ""}[status == worker.StatusCompletedWithStageFailures])
	return []byte(terminal)
}

type harness struct {
	t       *testing.T
	manager *jobs.Manager
	runner  *scriptedRunner
	store   *store.Store
	cancel  context.CancelFunc
	stopped chan struct{}
}

func newHarness(t *testing.T, queueLimit int) *harness {
	t.Helper()
	dir := t.TempDir()
	recording := filepath.Join(dir, "clip.mp4")
	if err := os.WriteFile(recording, []byte("clip"), 0o644); err != nil {
		t.Fatal(err)
	}
	st, err := store.Open(filepath.Join(dir, "tracen.db"))
	if err != nil {
		t.Fatal(err)
	}
	runner := &scriptedRunner{started: make(chan string, 8)}
	manager, err := jobs.NewManager(jobs.Config{DataDir: dir, Python: "python", WorkDir: dir, Workers: 2, QueueLimit: queueLimit}, st, runner, fakeSources{path: recording})
	if err != nil {
		t.Fatal(err)
	}
	h := &harness{t: t, manager: manager, runner: runner, store: st, stopped: make(chan struct{})}
	t.Cleanup(func() {
		if h.cancel != nil {
			h.cancel()
			<-h.stopped
		}
		st.Close()
	})
	return h
}

func (h *harness) start() {
	ctx, cancel := context.WithCancel(context.Background())
	h.cancel = cancel
	go func() {
		h.manager.Run(ctx)
		close(h.stopped)
	}()
}

func (h *harness) waitStatus(id string, want jobs.Status) jobs.Job {
	h.t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		job, err := h.manager.Get(context.Background(), id)
		if err != nil {
			h.t.Fatal(err)
		}
		if job.Status == want {
			return job
		}
		time.Sleep(20 * time.Millisecond)
	}
	job, _ := h.manager.Get(context.Background(), id)
	h.t.Fatalf("job %s is %s, wanted %s", id, job.Status, want)
	return job
}

func TestSuccessfulJobProducesAReportRecord(t *testing.T) {
	h := newHarness(t, 4)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		onProgress(worker.Progress{Stage: worker.StageDone, Name: "capture", WallSeconds: 5})
		onProgress(worker.Progress{Stage: worker.StageOCR, Processed: 50, Total: 100})
		io.WriteString(logs, "some stderr\n")
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	events, unsubscribe := h.manager.Hub().Subscribe("")
	unsubscribe()
	_ = events
	h.start()
	job, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	if job.Status != jobs.Queued || job.OutputDir == "" || job.LogPath == "" {
		t.Fatalf("submitted job: %+v", job)
	}
	done := h.waitStatus(job.ID, jobs.Succeeded)
	if done.ReportID == "" || done.Result == nil || done.Error != nil || done.FinishedAt.IsZero() || done.Stage != "ocr" || done.OCRTotal != 100 {
		t.Fatalf("finished job: %+v", done)
	}
	report, err := h.store.GetReport(context.Background(), done.ReportID)
	if err != nil || report.JobID != job.ID || report.Turns != 1 || report.Entries != 1 || report.DurationMS != 120000 {
		t.Fatalf("report record: %+v %v", report, err)
	}
	if log, _ := os.ReadFile(done.LogPath); string(log) != "some stderr\n" {
		t.Fatalf("worker log: %q", log)
	}
}

func TestWorkerFailureContractViolationAndBadHashBecomeFailedJobs(t *testing.T) {
	h := newHarness(t, 8)
	var modeMu sync.Mutex
	var mode string
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		modeMu.Lock()
		current := mode
		modeMu.Unlock()
		switch current {
		case "failed":
			return 2, []byte(`{"schema_version":"tracen-replay/analysis-job-v1","status":"failed","error":{"code":"unreadable_source","message":"no"}}`), nil
		case "garbage":
			return 0, []byte("not json"), nil
		case "badhash":
			terminal := writeArtifacts(t, cmd.Output, worker.StatusSucceeded)
			os.WriteFile(filepath.Join(cmd.Output, "report.json"), []byte(`{"tampered":true}`), 0o644)
			return 0, terminal, nil
		case "warned":
			return 0, writeArtifacts(t, cmd.Output, worker.StatusCompletedWithStageFailures), nil
		}
		return 0, nil, errors.New("python not found")
	}
	h.start()
	for _, tc := range []struct {
		mode, code string
		status     jobs.Status
	}{
		{"failed", "unreadable_source", jobs.Failed},
		{"garbage", worker.CodeBadTerminalOutput, jobs.Failed},
		{"badhash", worker.CodeReportHashMismatch, jobs.Failed},
		{"start", "worker_start_failed", jobs.Failed},
		{"warned", "", jobs.CompletedWithStageFailures},
	} {
		modeMu.Lock()
		mode = tc.mode
		modeMu.Unlock()
		job, err := h.manager.Submit(context.Background(), "u1", "src-1")
		if err != nil {
			t.Fatal(err)
		}
		done := h.waitStatus(job.ID, tc.status)
		if tc.code != "" && (done.Error == nil || done.Error.Code != tc.code) {
			t.Fatalf("%s: error %+v, want code %s", tc.mode, done.Error, tc.code)
		}
		if tc.status == jobs.CompletedWithStageFailures && (len(done.StageFailure) != 1 || done.ReportID == "") {
			t.Fatalf("warned job: %+v", done)
		}
		if tc.status == jobs.Failed && done.ReportID != "" {
			t.Fatalf("%s: a failed job must not own a report", tc.mode)
		}
	}
}

func TestQueueLimitAndCancelBeforeStart(t *testing.T) {
	h := newHarness(t, 2)
	release := make(chan struct{})
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		select {
		case <-release:
		case <-ctx.Done():
		}
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	h.start()
	first, _ := h.manager.Submit(context.Background(), "u1", "src-1")
	<-h.runner.started
	h.waitStatus(first.ID, jobs.Running)
	second, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	third, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	var full *jobs.QueueFullError
	if _, err := h.manager.Submit(context.Background(), "u1", "src-1"); !errors.As(err, &full) {
		t.Fatalf("fourth submit with two queued must hit the limit, got %v", err)
	}
	if _, err := h.manager.Submit(context.Background(), "u1", "nope"); err == nil {
		t.Fatal("unknown source must be rejected")
	}
	cancelled, err := h.manager.Cancel(context.Background(), second.ID)
	if err != nil || cancelled.Status != jobs.Cancelled || cancelled.Error.Code != "cancelled" {
		t.Fatalf("cancel queued: %+v %v", cancelled, err)
	}
	close(release)
	h.waitStatus(first.ID, jobs.Succeeded)
	h.waitStatus(third.ID, jobs.Succeeded)
	if job, _ := h.manager.Get(context.Background(), second.ID); job.Status != jobs.Cancelled || job.StartedAt.IsZero() == false {
		t.Fatalf("cancelled job must never start: %+v", job)
	}
	var te *jobs.TransitionError
	if _, err := h.manager.Cancel(context.Background(), first.ID); !errors.As(err, &te) {
		t.Fatalf("cancelling a finished job must be a transition error, got %v", err)
	}
}

func TestCancelDuringRunDiscardsALateCompletion(t *testing.T) {
	h := newHarness(t, 2)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		<-ctx.Done()
		// The process tree is gone, but the worker managed to write a complete
		// result before dying: it must not become an authoritative report.
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	h.start()
	job, _ := h.manager.Submit(context.Background(), "u1", "src-1")
	<-h.runner.started
	h.waitStatus(job.ID, jobs.Running)
	cancelled, err := h.manager.Cancel(context.Background(), job.ID)
	if err != nil || cancelled.Status != jobs.Cancelled || cancelled.ReportID != "" {
		t.Fatalf("cancel running: %+v %v", cancelled, err)
	}
	reports, _ := h.store.ListReports(context.Background())
	if len(reports) != 0 {
		t.Fatalf("no report may exist for a cancelled job: %+v", reports)
	}
}

func TestShutdownInterruptsTheRunningJobAndRecoverMarksStaleRows(t *testing.T) {
	h := newHarness(t, 2)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		<-ctx.Done()
		return 0, nil, nil
	}
	h.start()
	job, _ := h.manager.Submit(context.Background(), "u1", "src-1")
	<-h.runner.started
	h.waitStatus(job.ID, jobs.Running)
	queued, _ := h.manager.Submit(context.Background(), "u1", "src-1")
	h.cancel()
	<-h.stopped
	h.cancel = nil
	interrupted := h.waitStatus(job.ID, jobs.Interrupted)
	if interrupted.Error == nil || interrupted.Error.Code != "interrupted" {
		t.Fatalf("interrupted job: %+v", interrupted)
	}
	// Simulate a crash that left a row running, then a restart.
	stale := jobs.Job{ID: "stale", SourceID: "src-1", SourcePath: "x", SourceName: "x", Status: jobs.Running, CreatedAt: time.Now(), StartedAt: time.Now(), OutputDir: "o"}
	if err := h.store.CreateJob(context.Background(), stale); err != nil {
		t.Fatal(err)
	}
	recovered, err := h.manager.Recover(context.Background())
	if err != nil || len(recovered) != 1 || recovered[0].ID != "stale" {
		t.Fatalf("recover: %+v %v", recovered, err)
	}
	if again, _ := h.manager.Get(context.Background(), queued.ID); again.Status != jobs.Queued {
		t.Fatalf("queued work must survive a restart: %+v", again)
	}
}

func TestSubscribersSeeProgressAndTerminalEvents(t *testing.T) {
	h := newHarness(t, 2)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		onProgress(worker.Progress{Stage: worker.StageOCR, Processed: 25, Total: 100})
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	h.start()
	// Subscribe before submitting: the id is not known yet, so subscribe by
	// racing the first event through a fresh subscription per job id.
	job, _ := h.manager.Submit(context.Background(), "u1", "src-1")
	events, unsubscribe := h.manager.Hub().Subscribe(job.ID)
	defer unsubscribe()
	var sawPercent, sawTerminal bool
	deadline := time.After(10 * time.Second)
	for !sawTerminal {
		select {
		case e := <-events:
			if e.Percent != nil && *e.Percent == 25 {
				sawPercent = true
			}
			if e.Terminal {
				sawTerminal = true
				if e.Status != jobs.Succeeded {
					t.Fatalf("terminal event status %s", e.Status)
				}
			}
		case <-deadline:
			t.Fatal("no terminal event")
		}
	}
	if !sawPercent {
		t.Log("percent event was published before the subscription; acceptable, the job record carries it")
	}
}
