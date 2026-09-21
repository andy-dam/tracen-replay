package jobs_test

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// resumableScript behaves like the analyzer across a pause: the first start
// leaves a file in the run directory and waits to be stopped, the second
// finds that file and finishes.
func resumableScript(t *testing.T, found *bool) func(context.Context, worker.Command, func(worker.Progress), io.Writer) (int, []byte, error) {
	return func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		kept := filepath.Join(cmd.Output, "neural", "frame-1.json")
		if _, err := os.Stat(kept); err == nil {
			*found = true
			io.WriteString(logs, "second start\n")
			return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
		}
		if err := os.MkdirAll(filepath.Dir(kept), 0o755); err != nil {
			t.Error(err)
		}
		os.WriteFile(kept, []byte("{}"), 0o644)
		io.WriteString(logs, "first start\n")
		<-ctx.Done()
		return 1, nil, nil
	}
}

// A running job that is paused keeps its run directory, and resumed it runs
// on that directory again and finishes as the same job.
func TestPausedJobResumesOnItsOwnFiles(t *testing.T) {
	h := newHarness(t, 4)
	found := false
	h.runner.script = resumableScript(t, &found)
	h.start()
	ctx := context.Background()
	job, err := h.manager.Submit(ctx, "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	h.waitStatus(job.ID, jobs.Running)
	<-h.runner.started
	time.Sleep(50 * time.Millisecond)
	paused, err := h.manager.Pause(ctx, job.ID)
	if err != nil || paused.Status != jobs.Paused {
		t.Fatalf("pause: %+v %v", paused, err)
	}
	if paused.PausedAt.IsZero() || !paused.FinishedAt.IsZero() || paused.Error != nil || !paused.PausedUntil.IsZero() {
		t.Fatalf("paused record: %+v", paused)
	}
	if _, err := os.Stat(filepath.Join(paused.OutputDir, "neural", "frame-1.json")); err != nil {
		t.Fatalf("the run directory was not kept: %v", err)
	}
	if _, err := h.manager.Pause(ctx, job.ID); err == nil {
		t.Fatal("a paused job was paused again")
	}
	resumed, err := h.manager.Resume(ctx, job.ID)
	if err != nil || resumed.Status != jobs.Queued || !resumed.PausedAt.IsZero() {
		t.Fatalf("resume: %+v %v", resumed, err)
	}
	done := h.waitStatus(job.ID, jobs.Succeeded)
	if !found {
		t.Fatal("the second start did not see the first start's files")
	}
	if done.ReportID == "" || done.FinishedAt.IsZero() {
		t.Fatalf("finished record: %+v", done)
	}
	log, err := os.ReadFile(done.LogPath)
	if err != nil || string(log) != "first start\nsecond start\n" {
		t.Fatalf("log: %q %v", log, err)
	}
}

// movingClock is a clock a test moves by hand.
type movingClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *movingClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *movingClock) Add(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
}

// A job left paused past the lifetime is cancelled with its own code, its
// working files are deleted, and it cannot be resumed; before that it says
// until when its progress is kept.
func TestPausedJobExpires(t *testing.T) {
	clock := &movingClock{now: time.Date(2026, 9, 21, 12, 0, 0, 0, time.UTC)}
	h := newHarness(t, 4, func(c *jobs.Config) { c.Clock, c.PausedLifetime = clock.Now, 24*time.Hour })
	ctx := context.Background()
	job, err := h.manager.Submit(ctx, "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	paused, err := h.manager.Pause(ctx, job.ID)
	if err != nil || paused.Status != jobs.Paused {
		t.Fatalf("pause of a queued job: %+v %v", paused, err)
	}
	if want := clock.Now().Add(24 * time.Hour); !paused.PausedUntil.Equal(want) {
		t.Fatalf("paused until %v, want %v", paused.PausedUntil, want)
	}
	if err := os.MkdirAll(filepath.Join(paused.OutputDir, "frames"), 0o755); err != nil {
		t.Fatal(err)
	}
	clock.Add(23 * time.Hour)
	if n := h.manager.ExpirePaused(ctx); n != 0 {
		t.Fatalf("expired %d jobs an hour early", n)
	}
	clock.Add(time.Hour)
	if n := h.manager.ExpirePaused(ctx); n != 1 {
		t.Fatalf("expired %d jobs, want 1", n)
	}
	expired, _ := h.manager.Get(ctx, job.ID)
	if expired.Status != jobs.Cancelled || expired.Error == nil || expired.Error.Code != jobs.CodePauseExpired {
		t.Fatalf("expired record: %+v", expired)
	}
	if _, err := os.Stat(paused.OutputDir); !os.IsNotExist(err) {
		t.Fatalf("the run directory is still there: %v", err)
	}
	var te *jobs.TransitionError
	if _, err := h.manager.Resume(ctx, job.ID); !errors.As(err, &te) {
		t.Fatalf("resume of an expired job: %v", err)
	}
}

// With no lifetime a paused job is kept however long it waits, and
// cancelling it deletes its files.
func TestPausedJobIsKeptForGoodByDefault(t *testing.T) {
	clock := &movingClock{now: time.Date(2026, 9, 21, 12, 0, 0, 0, time.UTC)}
	h := newHarness(t, 4, func(c *jobs.Config) { c.Clock = clock.Now })
	ctx := context.Background()
	job, _ := h.manager.Submit(ctx, "u1", "src-1")
	paused, err := h.manager.Pause(ctx, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	os.MkdirAll(filepath.Join(paused.OutputDir, "frames"), 0o755)
	clock.Add(365 * 24 * time.Hour)
	if n := h.manager.ExpirePaused(ctx); n != 0 {
		t.Fatalf("expired %d jobs with no lifetime set", n)
	}
	if kept, _ := h.manager.Get(ctx, job.ID); kept.Status != jobs.Paused || !kept.PausedUntil.IsZero() {
		t.Fatalf("kept record: %+v", kept)
	}
	// The lifetime can be set while the service runs.
	h.manager.SetPausedLifetime(24 * time.Hour)
	if kept, _ := h.manager.Get(ctx, job.ID); kept.PausedUntil.IsZero() {
		t.Fatal("no deadline after the lifetime was set")
	}
	h.manager.SetPausedLifetime(0)
	cancelled, err := h.manager.Cancel(ctx, job.ID)
	if err != nil || cancelled.Status != jobs.Cancelled || cancelled.Error.Code != "cancelled" {
		t.Fatalf("cancel of a paused job: %+v %v", cancelled, err)
	}
	if _, err := os.Stat(paused.OutputDir); !os.IsNotExist(err) {
		t.Fatalf("the run directory is still there: %v", err)
	}
}

// A paused job holds a place among the user's active jobs.
func TestPausedJobCountsAsActive(t *testing.T) {
	h := newHarness(t, 4, func(c *jobs.Config) { c.MaxActivePerUser = 1 })
	ctx := context.Background()
	job, _ := h.manager.Submit(ctx, "u1", "src-1")
	if _, err := h.manager.Pause(ctx, job.ID); err != nil {
		t.Fatal(err)
	}
	var limit *jobs.LimitError
	if _, err := h.manager.Submit(ctx, "u1", "src-1"); !errors.As(err, &limit) || limit.Code != "too_many_jobs" {
		t.Fatalf("second submission: %v", err)
	}
}

// With a shared queue the pause is asked for through the record, the worker
// keeps the job's scratch directory, and the resumed job runs on it without
// fetching the recording again; once it has finished the directory is gone.
func TestRemotePauseKeepsScratchForTheResume(t *testing.T) {
	h := newRemoteHarness(t)
	found := false
	h.runner.script = resumableScript(t, &found)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stopped := make(chan struct{})
	go func() { h.worker.Work(ctx); close(stopped) }()
	job, err := h.api.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	h.waitStatus(job.ID, jobs.Running)
	<-h.runner.started
	time.Sleep(50 * time.Millisecond)
	requested, err := h.api.Pause(context.Background(), job.ID)
	if err != nil || !requested.PauseRequested || requested.Status != jobs.Running {
		t.Fatalf("pause request: %+v %v", requested, err)
	}
	paused := h.waitStatus(job.ID, jobs.Paused)
	if paused.PauseRequested || paused.Error != nil {
		t.Fatalf("paused record: %+v", paused)
	}
	dir := filepath.Join(h.scratch, job.ID)
	if _, err := os.Stat(filepath.Join(dir, "run", "neural", "frame-1.json")); err != nil {
		t.Fatalf("scratch not kept: %v", err)
	}
	// The recording is gone from the store: a resume that fetched it again would fail.
	if err := h.objects.Delete(context.Background(), "originals/u1/src-1.mp4"); err != nil {
		t.Fatal(err)
	}
	if _, err := h.api.Resume(context.Background(), job.ID); err != nil {
		t.Fatal(err)
	}
	h.waitStatus(job.ID, jobs.Succeeded)
	cancel()
	<-stopped
	if !found {
		t.Fatal("the resumed run did not see the kept files")
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("scratch not cleaned after the job finished: %v", err)
	}
}

// A worker that starts deletes the scratch directories of jobs that will
// not run again and leaves a paused job's alone.
func TestWorkerSweepsStaleScratch(t *testing.T) {
	h := newRemoteHarness(t)
	ctx := context.Background()
	kept, _ := h.api.Submit(ctx, "u1", "src-1")
	if _, err := h.api.Pause(ctx, kept.ID); err != nil {
		t.Fatal(err)
	}
	gone, _ := h.api.Submit(ctx, "u1", "src-1")
	if _, err := h.api.Cancel(ctx, gone.ID); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{kept.ID, gone.ID} {
		if err := os.MkdirAll(filepath.Join(h.scratch, id, "run"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	idle, err := jobs.NewManager(jobs.Config{Python: "python", WorkDir: h.scratch, Workers: 1, QueueLimit: 4, Recordings: h.upload,
		Queue: h.queue, Objects: h.objects, Scratch: h.scratch, Poll: 5 * time.Millisecond, ExitWhenIdle: true}, h.store, h.runner)
	if err != nil {
		t.Fatal(err)
	}
	if err := idle.Work(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(h.scratch, kept.ID)); err != nil {
		t.Fatalf("a paused job's scratch was deleted: %v", err)
	}
	if _, err := os.Stat(filepath.Join(h.scratch, gone.ID)); !os.IsNotExist(err) {
		t.Fatalf("a cancelled job's scratch is still there: %v", err)
	}
}

// Cancelling a paused job from the API process, which cannot see the
// workers' scratch space, reaches the worker through the queue: it deletes
// the job's scratch directory and runs nothing.
func TestRemoteCancelOfAPausedJobCleansItsScratch(t *testing.T) {
	h := newRemoteHarness(t)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		<-ctx.Done()
		return 1, nil, nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stopped := make(chan struct{})
	go func() { h.worker.Work(ctx); close(stopped) }()
	job, _ := h.api.Submit(context.Background(), "u1", "src-1")
	h.waitStatus(job.ID, jobs.Running)
	<-h.runner.started
	if _, err := h.api.Pause(context.Background(), job.ID); err != nil {
		t.Fatal(err)
	}
	h.waitStatus(job.ID, jobs.Paused)
	dir := filepath.Join(h.scratch, job.ID)
	if _, err := os.Stat(dir); err != nil {
		t.Fatalf("scratch not kept: %v", err)
	}
	if _, err := h.api.Cancel(context.Background(), job.ID); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(dir); os.IsNotExist(err) {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	<-stopped
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("scratch still there after the cancel: %v", err)
	}
	if final, _ := h.api.Get(context.Background(), job.ID); final.Status != jobs.Cancelled {
		t.Fatalf("job is %s", final.Status)
	}
}
