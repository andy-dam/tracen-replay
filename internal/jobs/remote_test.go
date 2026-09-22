package jobs_test

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/objectstore"
	"github.com/andy-dam/tracen-replay/internal/queue"
	"github.com/andy-dam/tracen-replay/internal/store"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// keyUpload serves one recording held in an object store as user u1's
// upload "src-1", and takes back what an analysis learns about it.
type keyUpload struct {
	key     string
	updated *jobs.Recording
}

func (f *keyUpload) GetRecording(ctx context.Context, id string) (jobs.Recording, error) {
	if id != "src-1" {
		return jobs.Recording{}, &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	if f.updated != nil {
		return *f.updated, nil
	}
	return jobs.Recording{ID: "src-1", UserID: "u1", Name: "clip.mp4", Path: f.key, Size: 4}, nil
}

func (f *keyUpload) UpdateRecording(ctx context.Context, r jobs.Recording) error {
	f.updated = &r
	return nil
}

// remoteHarness is the API process and the worker process of the shared
// queue mode, sharing one database, one queue and one object store.
type remoteHarness struct {
	t       *testing.T
	api     *jobs.Manager
	worker  *jobs.Manager
	runner  *scriptedRunner
	store   *store.Store
	objects *objectstore.Local
	queue   *queue.Memory
	scratch string
	upload  *keyUpload
}

func newRemoteHarness(t *testing.T) *remoteHarness {
	t.Helper()
	dir := t.TempDir()
	st, err := store.Open(filepath.Join(dir, "tracen.db"))
	if err != nil {
		t.Fatal(err)
	}
	objects, err := objectstore.NewLocal(filepath.Join(dir, "objects"))
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if err := objects.Put(ctx, "originals/u1/src-1.mp4", strings.NewReader("clip"), 4, "video/mp4"); err != nil {
		t.Fatal(err)
	}
	q := queue.NewMemory()
	recordings := &keyUpload{key: "originals/u1/src-1.mp4"}
	api, err := jobs.NewManager(jobs.Config{Workers: 1, QueueLimit: 4, Recordings: recordings, Queue: q, Objects: objects, Poll: 20 * time.Millisecond}, st, &scriptedRunner{})
	if err != nil {
		t.Fatal(err)
	}
	runner := &scriptedRunner{started: make(chan string, 8)}
	scratch := filepath.Join(dir, "scratch")
	worker, err := jobs.NewManager(jobs.Config{Python: "python", WorkDir: dir, Workers: 1, QueueLimit: 4, Recordings: recordings,
		Queue: q, Objects: objects, Scratch: scratch, Poll: 20 * time.Millisecond,
		KeepCopy: func(ctx context.Context, src, dst string) error {
			data, err := os.ReadFile(src)
			if err != nil {
				return err
			}
			return os.WriteFile(dst, append([]byte("720p:"), data...), 0o644)
		}}, st, runner)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	return &remoteHarness{t: t, api: api, worker: worker, runner: runner, store: st, objects: objects, queue: q, scratch: scratch, upload: recordings}
}

func (h *remoteHarness) waitStatus(id string, want jobs.Status) jobs.Job {
	h.t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		job, err := h.api.Get(context.Background(), id)
		if err != nil {
			h.t.Fatal(err)
		}
		if job.Status == want {
			return job
		}
		time.Sleep(20 * time.Millisecond)
	}
	job, _ := h.api.Get(context.Background(), id)
	h.t.Fatalf("job %s is %s, wanted %s", id, job.Status, want)
	return job
}

// The API process queues a job; the worker process fetches the recording,
// runs, and uploads the report, the timeline and the log under the job's
// key prefix; the report names them by key and the scratch is gone.
func TestRemoteJobRunsFromTheQueueAndUploadsItsOutputs(t *testing.T) {
	h := newRemoteHarness(t)
	var sourceSeen string
	var command worker.Command
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		data, _ := os.ReadFile(cmd.Source)
		sourceSeen, command = string(data), cmd
		onProgress(worker.Progress{Stage: worker.StageOCR, Processed: 50, Total: 100})
		io.WriteString(logs, "remote stderr\n")
		// The frames, crops and caches of a run: stored nowhere, and gone
		// with the scratch directory a moment later.
		if err := os.MkdirAll(filepath.Join(cmd.Output, "neural"), 0o755); err != nil {
			t.Error(err)
		}
		os.WriteFile(filepath.Join(cmd.Output, "neural", "frame-1.json"), []byte("{}"), 0o644)
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stopped := make(chan struct{})
	go func() { h.worker.Work(ctx); close(stopped) }()
	job, err := h.api.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	if job.OutputDir != "jobs/"+job.ID+"/run" || job.LogPath != "jobs/"+job.ID+"/worker.log" || h.queue.Len() != 1 {
		t.Fatalf("submitted: %+v queue %d", job, h.queue.Len())
	}
	done := h.waitStatus(job.ID, jobs.Succeeded)
	cancel()
	<-stopped
	if sourceSeen != "clip" {
		t.Fatalf("the worker read %q as the recording", sourceSeen)
	}
	if done.ReportID == "" || done.HeartbeatAt.IsZero() || done.OCRTotal != 100 {
		t.Fatalf("finished: %+v", done)
	}
	report, err := h.store.GetReport(context.Background(), done.ReportID)
	if err != nil {
		t.Fatal(err)
	}
	if report.ReportPath != job.OutputDir+"/report.json" || report.TimelinePath != job.OutputDir+"/timeline.json" ||
		report.EvidenceRoot != job.OutputDir {
		t.Fatalf("report keys: %+v", report)
	}
	for _, key := range []string{report.ReportPath, report.TimelinePath, job.LogPath} {
		if _, err := h.objects.Stat(context.Background(), key); err != nil {
			t.Fatalf("%s not uploaded: %v", key, err)
		}
	}
	// Only the run's own files are stored, and the worker asked for no
	// pruning: the scratch directory goes whole.
	if _, err := h.objects.Stat(context.Background(), job.OutputDir+"/neural/frame-1.json"); err == nil {
		t.Fatal("the run's working files were uploaded")
	}
	if command.PruneFrames || command.PruneWorkingData || !command.NoViewer {
		t.Fatalf("the worker's command: %+v", command)
	}
	reader, _ := h.objects.Open(context.Background(), job.LogPath)
	log, _ := io.ReadAll(reader)
	reader.Close()
	if string(log) != "remote stderr\n" {
		t.Fatalf("uploaded log: %q", log)
	}
	if entries, _ := os.ReadDir(h.scratch); len(entries) != 0 {
		t.Fatalf("scratch not cleaned: %v", entries)
	}
	if h.queue.Len() != 0 {
		t.Fatalf("message not deleted: %d left", h.queue.Len())
	}
	// The recording learned its hash from the analysis and got its playback
	// copy; the report plays from the copy.
	if h.upload.updated == nil || h.upload.updated.SHA256 != done.Result.SourceSHA256 || h.upload.updated.SHA256 == "" {
		t.Fatalf("recording after the analysis: %+v", h.upload.updated)
	}
	if h.upload.updated.KeptPath != "kept/u1/src-1.mp4" || report.SourcePath != "kept/u1/src-1.mp4" {
		t.Fatalf("kept copy: recording %+v report source %s", h.upload.updated, report.SourcePath)
	}
	kept, _ := h.objects.Open(context.Background(), "kept/u1/src-1.mp4")
	copied, _ := io.ReadAll(kept)
	kept.Close()
	if string(copied) != "720p:clip" {
		t.Fatalf("kept copy content: %q", copied)
	}
	if done.Stage != "ocr" {
		t.Fatalf("the final stage is the analyzer's last, got %q", done.Stage)
	}
}

// A cancellation from the API process reaches the worker through the
// record: the run stops and the job ends cancelled.
func TestRemoteCancelReachesTheWorker(t *testing.T) {
	h := newRemoteHarness(t)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		<-ctx.Done()
		return 1, nil, nil
	}
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
	requested, err := h.api.Cancel(context.Background(), job.ID)
	if err != nil || !requested.CancelRequested || requested.Status != jobs.Running {
		t.Fatalf("cancel request: %+v %v", requested, err)
	}
	done := h.waitStatus(job.ID, jobs.Cancelled)
	cancel()
	<-stopped
	if done.Error == nil || done.Error.Code != "cancelled" {
		t.Fatalf("cancelled job: %+v", done)
	}
}

// A job cancelled while queued is dropped when its message is taken, and a
// message that comes back for a job another worker left running settles
// the job as interrupted.
func TestRemoteWorkerSettlesStaleMessages(t *testing.T) {
	h := newRemoteHarness(t)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		t.Error("nothing should run")
		return 1, nil, nil
	}
	ctx := context.Background()
	cancelled, err := h.api.Submit(ctx, "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := h.api.Cancel(ctx, cancelled.ID); err != nil {
		t.Fatal(err)
	}
	// A job a dead worker left running: its message was taken once already.
	left, err := h.api.Submit(ctx, "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	left.Status = jobs.Running
	if err := h.store.UpdateJob(ctx, left); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	h.queue.SetClock(func() time.Time { return now })
	if m, ok, _ := h.queue.Receive(ctx, time.Millisecond); !ok || m.Body != cancelled.ID {
		t.Fatal("expected the cancelled job's message first")
	}
	if m, ok, _ := h.queue.Receive(ctx, time.Millisecond); !ok || m.Body != left.ID {
		t.Fatal("expected the left job's message")
	}
	now = now.Add(time.Second)
	workCtx, stop := context.WithCancel(ctx)
	stopped := make(chan struct{})
	go func() { h.worker.Work(workCtx); close(stopped) }()
	deadline := time.Now().Add(10 * time.Second)
	for h.queue.Len() != 0 && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	stop()
	<-stopped
	if h.queue.Len() != 0 {
		t.Fatalf("%d messages left", h.queue.Len())
	}
	if job, _ := h.api.Get(ctx, left.ID); job.Status != jobs.Paused || job.Error == nil || job.Error.Code != "interrupted" {
		t.Fatalf("left job: %+v", job)
	}
	if job, _ := h.api.Get(ctx, cancelled.ID); job.Status != jobs.Cancelled {
		t.Fatalf("cancelled job: %+v", job)
	}
}

// Follow publishes a worker's progress from the record, so an event stream
// in the API process sees the run and its end.
func TestFollowPublishesRecordChanges(t *testing.T) {
	h := newRemoteHarness(t)
	ctx := context.Background()
	job, err := h.api.Submit(ctx, "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	events, unsubscribe := h.api.Hub().Subscribe(job.ID)
	defer unsubscribe()
	followCtx, stopFollow := context.WithCancel(ctx)
	defer stopFollow()
	go h.api.Follow(followCtx)
	job.Status, job.Stage, job.OCRProcessed, job.OCRTotal = jobs.Running, "ocr", 10, 100
	job.HeartbeatAt = time.Now()
	if err := h.store.UpdateJob(ctx, job); err != nil {
		t.Fatal(err)
	}
	var got []jobs.Event
	for len(got) < 1 {
		select {
		case e := <-events:
			got = append(got, e)
		case <-time.After(10 * time.Second):
			t.Fatalf("no running event: %+v", got)
		}
	}
	if got[0].Status != jobs.Running || got[0].Percent == nil || *got[0].Percent != 10 {
		t.Fatalf("running event: %+v", got[0])
	}
	job.Status, job.FinishedAt = jobs.Succeeded, time.Now()
	if err := h.store.UpdateJob(ctx, job); err != nil {
		t.Fatal(err)
	}
	select {
	case e := <-events:
		if !e.Terminal || e.Status != jobs.Succeeded {
			t.Fatalf("terminal event: %+v", e)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("no terminal event")
	}
}

// A worker that exits when idle runs what is queued and then returns on
// its own once the queue has stayed empty.
func TestWorkExitsWhenIdle(t *testing.T) {
	h := newRemoteHarness(t)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	idle, err := jobs.NewManager(jobs.Config{Python: "python", WorkDir: t.TempDir(), Workers: 1, QueueLimit: 4, Recordings: h.upload,
		Queue: h.queue, Objects: h.objects, Scratch: h.scratch, Poll: 20 * time.Millisecond, ExitWhenIdle: true}, h.store, h.runner)
	if err != nil {
		t.Fatal(err)
	}
	job, err := h.api.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- idle.Work(context.Background()) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the worker did not exit on an empty queue")
	}
	if got, _ := h.api.Get(context.Background(), job.ID); got.Status != jobs.Succeeded {
		t.Fatalf("the queued job ran first: %+v", got)
	}
}

// A run longer than one renewal of its message's hold ends with the message
// deleted: each renewal changes the receipt, and the worker deletes with the
// latest one. With the first receipt the message stayed in the queue.
func TestFinishedJobMessageIsDeletedAfterRenewals(t *testing.T) {
	h := newRemoteHarness(t)
	runner := &scriptedRunner{started: make(chan string, 1)}
	runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		time.Sleep(200 * time.Millisecond)
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	renewing, err := jobs.NewManager(jobs.Config{Python: "python", WorkDir: h.scratch, Workers: 1, QueueLimit: 4, Recordings: h.upload,
		Queue: h.queue, Objects: h.objects, Scratch: h.scratch, Poll: 10 * time.Millisecond, HoldRenew: 20 * time.Millisecond}, h.store, runner)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stopped := make(chan struct{})
	go func() { renewing.Work(ctx); close(stopped) }()
	job, err := h.api.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	h.waitStatus(job.ID, jobs.Succeeded)
	deadline := time.Now().Add(5 * time.Second)
	for h.queue.Len() != 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	<-stopped
	if n := h.queue.Len(); n != 0 {
		t.Fatalf("%d message left in the queue after the job finished", n)
	}
}
