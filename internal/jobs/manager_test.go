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
	"strings"
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

// oneUpload serves one temporary recording as user u1's upload "src-1".
type oneUpload struct{ path string }

func (f oneUpload) GetRecording(ctx context.Context, id string) (jobs.Recording, error) {
	if id != "src-1" {
		return jobs.Recording{}, &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	return jobs.Recording{ID: "src-1", UserID: "u1", Name: filepath.Base(f.path), Path: f.path, Size: 4}, nil
}

// scriptedRunner behaves like the worker as far as the manager can see.
type scriptedRunner struct {
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

func newHarness(t *testing.T, queueLimit int, options ...func(*jobs.Config)) *harness {
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
	cfg := jobs.Config{DataDir: dir, Python: "python", WorkDir: dir, Workers: 2, QueueLimit: queueLimit, Recordings: oneUpload{path: recording}}
	for _, option := range options {
		option(&cfg)
	}
	manager, err := jobs.NewManager(cfg, st, runner)
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

// The analyzer names receipt_inspection again after assemble and names a few
// stages that are not steps; the recorded stage never goes back for them.
func TestRecordedStageOnlyMovesForward(t *testing.T) {
	h := newHarness(t, 4)
	named := []string{"capture", "currency_refinement", "currency_refinement_complete", "not_a_step",
		"receipt_inspection", "assemble", "receipt_inspection", "training_gain_recovery"}
	listening := make(chan struct{})
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		<-listening
		for _, name := range named {
			onProgress(worker.Progress{Stage: worker.StageDone, Name: name})
		}
		// A later step reads frames too; its count is not the OCR stage's.
		onProgress(worker.Progress{Stage: worker.StageOCR, Processed: 1, Total: 2})
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	h.start()
	job, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	events, unsubscribe := h.manager.Hub().Subscribe(job.ID)
	defer unsubscribe()
	close(listening)
	done := h.waitStatus(job.ID, jobs.Succeeded)
	// Every progress line published the stage the record held after it.
	var got []string
	for len(got) < len(named) {
		select {
		case event := <-events:
			if event.Status == jobs.Running && event.Stage != "" {
				got = append(got, event.Stage)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("only %d stage events arrived: %v", len(got), got)
		}
	}
	want := []string{"capture", "currency_refinement", "currency_refinement_complete", "currency_refinement_complete",
		"receipt_inspection", "assemble", "assemble", "assemble"}
	if strings.Join(got, " ") != strings.Join(want, " ") {
		t.Fatalf("recorded stages %v, wanted %v", got, want)
	}
	if done.Stage != "assemble" || done.OCRTotal != 0 {
		t.Fatalf("finished job: stage %q, ocr total %d", done.Stage, done.OCRTotal)
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

func TestParallelRunsUpToTheLimitAtOnceInQueueOrder(t *testing.T) {
	blocking := func(release chan struct{}) func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		return func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
			select {
			case <-release:
			case <-ctx.Done():
			}
			return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
		}
	}
	// Two slots: the first two jobs run together, the third waits for a slot.
	h := newHarness(t, 4, func(c *jobs.Config) { c.Parallel = 2 })
	release := make(chan struct{})
	h.runner.script = blocking(release)
	h.start()
	var ids []string
	for range 3 {
		job, err := h.manager.Submit(context.Background(), "u1", "src-1")
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, job.ID)
	}
	<-h.runner.started
	<-h.runner.started
	h.waitStatus(ids[0], jobs.Running)
	h.waitStatus(ids[1], jobs.Running)
	time.Sleep(150 * time.Millisecond)
	if job, _ := h.manager.Get(context.Background(), ids[2]); job.Status != jobs.Queued {
		t.Fatalf("the third job must wait for a slot, got %s", job.Status)
	}
	close(release)
	for _, id := range ids {
		h.waitStatus(id, jobs.Succeeded)
	}
	// The default is one at a time.
	single := newHarness(t, 4)
	hold := make(chan struct{})
	single.runner.script = blocking(hold)
	single.start()
	first, _ := single.manager.Submit(context.Background(), "u1", "src-1")
	second, _ := single.manager.Submit(context.Background(), "u1", "src-1")
	<-single.runner.started
	single.waitStatus(first.ID, jobs.Running)
	time.Sleep(150 * time.Millisecond)
	if job, _ := single.manager.Get(context.Background(), second.ID); job.Status != jobs.Queued {
		t.Fatalf("with one slot the second job must wait, got %s", job.Status)
	}
	close(hold)
	single.waitStatus(second.ID, jobs.Succeeded)
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
	if err := os.MkdirAll(filepath.Join(job.OutputDir, "neural"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(job.OutputDir, "report-partial.json"), []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	cancelled, err := h.manager.Cancel(context.Background(), job.ID)
	if err != nil || cancelled.Status != jobs.Cancelled || cancelled.ReportID != "" {
		t.Fatalf("cancel running: %+v %v", cancelled, err)
	}
	reports, _ := h.store.ListReports(context.Background())
	if len(reports) != 0 {
		t.Fatalf("no report may exist for a cancelled job: %+v", reports)
	}
	// The working directories go with the cancellation; the files stay.
	if _, err := os.Stat(filepath.Join(job.OutputDir, "neural")); !os.IsNotExist(err) {
		t.Fatalf("working directory kept after the cancel: %v", err)
	}
	if _, err := os.Stat(filepath.Join(job.OutputDir, "report-partial.json")); err != nil {
		t.Fatalf("top-level file lost with the cancel: %v", err)
	}
}

// An interrupted record from before pausing existed can be resumed, and
// cancelled, which deletes its run directory.
func TestInterruptedRecordsCanBeResumedOrCancelled(t *testing.T) {
	h := newHarness(t, 4)
	ctx := context.Background()
	start := time.Now().Add(-time.Hour)
	// The data directory: where Submit puts a job's run directory.
	probe, _ := h.manager.Submit(ctx, "u1", "src-1")
	h.manager.Cancel(ctx, probe.ID)
	for _, id := range []string{"old-1", "old-2"} {
		dir := filepath.Join(filepath.Dir(filepath.Dir(probe.OutputDir)), id, "run")
		if err := os.MkdirAll(filepath.Join(dir, "neural"), 0o755); err != nil {
			t.Fatal(err)
		}
		old := jobs.Job{ID: id, UserID: "u1", SourceID: "src-1", SourcePath: "x", SourceName: "x", Status: jobs.Interrupted,
			CreatedAt: start, StartedAt: start, FinishedAt: start.Add(30 * time.Minute), OutputDir: dir,
			Error: &jobs.Failure{Code: "interrupted", Message: "old"}}
		if err := h.store.CreateJob(ctx, old); err != nil {
			t.Fatal(err)
		}
	}
	resumed, err := h.manager.Resume(ctx, "old-1")
	if err != nil || resumed.Status != jobs.Queued || resumed.RanSeconds != 1800 || !resumed.FinishedAt.IsZero() || resumed.Error != nil {
		t.Fatalf("resume: %+v %v", resumed, err)
	}
	cancelled, err := h.manager.Cancel(ctx, "old-2")
	if err != nil || cancelled.Status != jobs.Cancelled {
		t.Fatalf("cancel: %+v %v", cancelled, err)
	}
	if _, err := os.Stat(cancelled.OutputDir); !os.IsNotExist(err) {
		t.Fatalf("run directory kept after the cancel: %v", err)
	}
}

// A shutdown pauses the running job with its files kept, and so does a
// restart over a row a crash left running; both can be resumed.
func TestShutdownPausesTheRunningJobAndRecoverPausesStaleRows(t *testing.T) {
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
	if err := os.MkdirAll(filepath.Join(job.OutputDir, "neural"), 0o755); err != nil {
		t.Fatal(err)
	}
	h.cancel()
	<-h.stopped
	h.cancel = nil
	paused := h.waitStatus(job.ID, jobs.Paused)
	if paused.Error == nil || paused.Error.Code != "interrupted" || !paused.FinishedAt.IsZero() {
		t.Fatalf("paused job: %+v", paused)
	}
	if _, err := os.Stat(filepath.Join(job.OutputDir, "neural")); err != nil {
		t.Fatalf("the run directory was not kept: %v", err)
	}
	// Simulate a crash that left a row running, then a restart.
	stale := jobs.Job{ID: "stale", SourceID: "src-1", SourcePath: "x", SourceName: "x", Status: jobs.Running, CreatedAt: time.Now(), StartedAt: time.Now(), OutputDir: "o"}
	if err := h.store.CreateJob(context.Background(), stale); err != nil {
		t.Fatal(err)
	}
	recovered, err := h.manager.Recover(context.Background())
	if err != nil || len(recovered) != 1 || recovered[0].ID != "stale" || recovered[0].Status != jobs.Paused {
		t.Fatalf("recover: %+v %v", recovered, err)
	}
	if resumed, err := h.manager.Resume(context.Background(), "stale"); err != nil || resumed.Status != jobs.Queued || resumed.Error != nil {
		t.Fatalf("resume of a job paused by a restart: %+v %v", resumed, err)
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

// The planner decides how many readers an analysis starts, and is told how
// many other analyses are already running, so a lone analysis takes the
// whole share.
func TestReadersArePlannedAgainstTheAnalysesAlreadyRunning(t *testing.T) {
	var mu sync.Mutex
	var asked []int
	started := make(chan struct{}, 2)
	release := make(chan struct{})
	h := newHarness(t, 4, func(c *jobs.Config) {
		c.Parallel = 2
		c.Workers, c.DenseWorkers = 1, 1
		c.Readers = func(others int) (int, int) {
			mu.Lock()
			asked = append(asked, others)
			mu.Unlock()
			return 6 - 3*others, max(1, 5-3*others)
		}
	})
	var seen []worker.Command
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		mu.Lock()
		seen = append(seen, cmd)
		mu.Unlock()
		started <- struct{}{}
		<-release
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	h.start()
	ctx := context.Background()
	first, _ := h.manager.Submit(ctx, "u1", "src-1")
	<-started
	h.waitStatus(first.ID, jobs.Running)
	second, _ := h.manager.Submit(ctx, "u1", "src-1")
	<-started
	h.waitStatus(second.ID, jobs.Running)
	close(release)
	h.waitStatus(first.ID, jobs.Succeeded)
	h.waitStatus(second.ID, jobs.Succeeded)
	mu.Lock()
	defer mu.Unlock()
	if len(asked) != 2 || asked[0] != 0 || asked[1] != 1 {
		t.Fatalf("the planner was asked with %v, want [0 1]", asked)
	}
	if len(seen) != 2 || seen[0].Workers != 6 || seen[0].DenseWorkers != 5 || seen[1].Workers != 3 || seen[1].DenseWorkers != 2 {
		t.Fatalf("commands: %+v", seen)
	}
}

// Without a planner the fixed counts stand.
func TestReadersFallBackToTheFixedCounts(t *testing.T) {
	h := newHarness(t, 2, func(c *jobs.Config) { c.Workers, c.DenseWorkers = 3, 2 })
	var got worker.Command
	done := make(chan struct{})
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		got = cmd
		close(done)
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	h.start()
	job, _ := h.manager.Submit(context.Background(), "u1", "src-1")
	<-done
	h.waitStatus(job.ID, jobs.Succeeded)
	if got.Workers != 3 || got.DenseWorkers != 2 {
		t.Fatalf("command: %+v", got)
	}
}
