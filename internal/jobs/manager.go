package jobs

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/andy-dam/tracen-replay/internal/timeline"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// Config sets the paths and limits of the job manager.
type Config struct {
	// DataDir receives one directory per job (<DataDir>/jobs/<id>) with the
	// worker's run directory and log inside it.
	DataDir string
	// Python, WorkDir and ModelDir configure the worker command.
	Python   string
	WorkDir  string
	ModelDir string
	// Workers is the OCR worker count passed to the analyzer.
	Workers int
	// DenseWorkers is the dense re-read worker count; zero keeps the
	// analyzer's default.
	DenseWorkers int
	// OCRDevice is passed to the analyzer (auto, cpu, dml, cuda); empty keeps
	// its default.
	OCRDevice string
	// LearnedReader is the exported learned result-card reader passed to the
	// analyzer; empty leaves it off.
	LearnedReader string
	// QueueLimit bounds the number of queued jobs.
	QueueLimit int
	// Parallel is how many analyses run at once; less than one means one.
	// Each analysis holds a few GB and its OCR workers' share of the CPU, so
	// the number is set per machine.
	Parallel int
	// KeepWorkingData keeps the analyzer's OCR caches, crops and recovery
	// inputs in the job directory (about 1 GB per analysis). By default only
	// the report, the timeline, the viewer page and the log are kept.
	KeepWorkingData bool
	// Recordings resolves the uploaded recordings a job may analyze.
	Recordings Recordings
	// Clock and NewID are replaceable for tests.
	Clock func() time.Time
	NewID func() string
	// Logger receives lifecycle messages; nil uses slog.Default().
	Logger *slog.Logger
}

// QueueFullError is returned by Submit when the queue is at its limit.
type QueueFullError struct{ Limit int }

func (e *QueueFullError) Error() string {
	return fmt.Sprintf("queue is full (%d queued jobs)", e.Limit)
}

// TransitionError is returned when a request does not apply to the job's
// current state, for example cancelling a finished job.
type TransitionError struct {
	ID     string
	Status Status
	Action string
}

func (e *TransitionError) Error() string {
	return fmt.Sprintf("job %s is %s; cannot %s", e.ID, e.Status, e.Action)
}

// Manager runs one analyzer job at a time from a durable queue.
type Manager struct {
	cfg    Config
	store  Store
	runner Runner
	hub    *Hub
	log    *slog.Logger

	mu      sync.Mutex
	cancels map[string]context.CancelFunc
	done    map[string]chan struct{}
	wake    chan struct{}
}

// NewManager validates the configuration and prepares the manager. Call
// Recover before Run when the service starts.
func NewManager(cfg Config, store Store, runner Runner) (*Manager, error) {
	switch {
	case store == nil || runner == nil || cfg.Recordings == nil:
		return nil, errors.New("jobs: store, runner and recordings are required")
	case cfg.DataDir == "" || cfg.Python == "" || cfg.WorkDir == "":
		return nil, errors.New("jobs: data directory, python and working directory are required")
	case cfg.Workers < 1:
		return nil, errors.New("jobs: at least one OCR worker is required")
	case cfg.QueueLimit < 1:
		return nil, errors.New("jobs: the queue limit must be at least 1")
	}
	if cfg.Clock == nil {
		cfg.Clock = time.Now
	}
	if cfg.NewID == nil {
		cfg.NewID = randomID
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	return &Manager{cfg: cfg, store: store, runner: runner, hub: NewHub(), log: cfg.Logger,
		cancels: map[string]context.CancelFunc{}, done: map[string]chan struct{}{}, wake: make(chan struct{}, 1)}, nil
}

func randomID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}

// Hub exposes the event hub for subscribers.
func (m *Manager) Hub() *Hub { return m.hub }

// Get returns one job.
func (m *Manager) Get(ctx context.Context, id string) (Job, error) { return m.store.GetJob(ctx, id) }

// List returns the user's jobs (and unowned ones), newest first.
func (m *Manager) List(ctx context.Context, userID string) ([]Job, error) {
	return m.store.ListJobsForUser(ctx, userID)
}

// Recover marks jobs left running by a previous process as interrupted. It
// never re-runs them: an interrupted analysis is resubmitted explicitly.
func (m *Manager) Recover(ctx context.Context) ([]Job, error) {
	interrupted, err := m.store.MarkInterrupted(ctx, m.cfg.Clock())
	for _, job := range interrupted {
		m.log.Warn("job interrupted by restart", "job", job.ID)
	}
	return interrupted, err
}

// Submit queues an analysis for a user. The source is the server-issued id
// of an upload that belongs to the user.
func (m *Manager) Submit(ctx context.Context, userID, sourceID string) (Job, error) {
	source, err := m.resolve(ctx, userID, sourceID)
	if err != nil {
		return Job{}, err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	queued, err := m.store.CountByStatus(ctx, Queued)
	if err != nil {
		return Job{}, err
	}
	if queued >= m.cfg.QueueLimit {
		return Job{}, &QueueFullError{Limit: m.cfg.QueueLimit}
	}
	id := m.cfg.NewID()
	dir := filepath.Join(m.cfg.DataDir, "jobs", id)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return Job{}, err
	}
	job := Job{ID: id, UserID: userID, SourceID: source.ID, SourcePath: source.Path, SourceName: source.Name, Status: Queued,
		CreatedAt: m.cfg.Clock(), OutputDir: filepath.Join(dir, "run"), LogPath: filepath.Join(dir, "worker.log")}
	if err := m.store.CreateJob(ctx, job); err != nil {
		return Job{}, err
	}
	m.publish(job, nil)
	select {
	case m.wake <- struct{}{}:
	default:
	}
	return job, nil
}

// resolve finds the recording behind a source id among the user's own
// uploads. Another user's upload is not found.
func (m *Manager) resolve(ctx context.Context, userID, sourceID string) (Source, error) {
	recording, rerr := m.cfg.Recordings.GetRecording(ctx, sourceID)
	if rerr != nil || recording.UserID != userID {
		return Source{}, &NotFoundError{Kind: "source", ID: sourceID}
	}
	return Source{ID: recording.ID, Name: recording.Name, Path: recording.Path, Size: recording.Size}, nil
}

// Cancel stops a queued or running job. For a running job it kills the
// worker's process tree and waits, within a bound, for the record to reach
// its final state; the returned job carries whatever state was reached.
func (m *Manager) Cancel(ctx context.Context, id string) (Job, error) {
	m.mu.Lock()
	job, err := m.store.GetJob(ctx, id)
	if err != nil {
		m.mu.Unlock()
		return Job{}, err
	}
	switch job.Status {
	case Queued:
		job.Status = Cancelled
		job.FinishedAt = m.cfg.Clock()
		job.Error = &Failure{Code: "cancelled", Message: "cancelled before the analysis started"}
		err := m.store.UpdateJob(ctx, job)
		m.mu.Unlock()
		if err == nil {
			m.publish(job, nil)
		}
		return job, err
	case Running:
		cancel, done := m.cancels[id], m.done[id]
		m.mu.Unlock()
		if cancel == nil {
			return job, &TransitionError{ID: id, Status: job.Status, Action: "cancel: it is owned by another process"}
		}
		cancel()
		if done != nil {
			select {
			case <-done:
			case <-time.After(15 * time.Second):
			case <-ctx.Done():
			}
		}
		return m.store.GetJob(ctx, id)
	default:
		m.mu.Unlock()
		return job, &TransitionError{ID: id, Status: job.Status, Action: "cancel"}
	}
}

// Run executes queued jobs, up to Parallel at a time, until ctx is cancelled.
// Jobs start in queue order; a job in flight when ctx ends is recorded as
// interrupted after its process is gone.
func (m *Manager) Run(ctx context.Context) error {
	slots := make(chan struct{}, max(1, m.cfg.Parallel))
	var running sync.WaitGroup
	defer running.Wait()
	for {
		if ctx.Err() != nil {
			return nil
		}
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			return nil
		}
		job, ok, err := m.store.NextQueued(ctx)
		if err != nil {
			m.log.Error("queue read failed", "error", err)
			ok = false
		}
		if !ok {
			<-slots
			select {
			case <-ctx.Done():
				return nil
			case <-m.wake:
			case <-time.After(time.Second):
			}
			continue
		}
		// The job is claimed (marked running) before the loop looks at the
		// queue again, so two slots never pick the same job.
		claimed := make(chan struct{})
		running.Add(1)
		go func(id string) {
			defer running.Done()
			defer func() { <-slots }()
			m.runOne(ctx, id, claimed)
		}(job.ID)
		<-claimed
	}
}

func (m *Manager) runOne(ctx context.Context, id string, claimed chan<- struct{}) {
	m.mu.Lock()
	job, err := m.store.GetJob(ctx, id)
	if err != nil || job.Status != Queued {
		m.mu.Unlock()
		close(claimed)
		return
	}
	jobCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	m.cancels[id], m.done[id] = cancel, done
	job.Status = Running
	job.StartedAt = m.cfg.Clock()
	if err := m.store.UpdateJob(ctx, job); err != nil {
		m.mu.Unlock()
		close(claimed)
		cancel()
		m.log.Error("could not mark job running", "job", id, "error", err)
		return
	}
	m.mu.Unlock()
	close(claimed)
	m.publish(job, nil)
	m.log.Info("job started", "job", id, "source", job.SourceName)

	cmd := worker.Command{Python: m.cfg.Python, WorkDir: m.cfg.WorkDir, Source: job.SourcePath, Output: job.OutputDir,
		ModelDir: m.cfg.ModelDir, Workers: m.cfg.Workers, DenseWorkers: m.cfg.DenseWorkers, OCRDevice: m.cfg.OCRDevice,
		LearnedReader: m.cfg.LearnedReader, PruneFrames: true,
		PruneWorkingData: !m.cfg.KeepWorkingData, OwnerPID: os.Getpid()}
	logs, err := os.Create(job.LogPath)
	if err != nil {
		m.finish(ctx, id, func(j *Job) { j.Status = Failed; j.Error = &Failure{Code: "log_unwritable", Message: err.Error()} })
		cancel()
		return
	}
	// The latest progress is kept in memory and written through at a bounded
	// rate; the final record always carries the last line seen.
	var lastSave time.Time
	var latest struct {
		stage            string
		at               time.Time
		processed, total int
	}
	applyProgress := func(j *Job) {
		if latest.stage != "" {
			j.Stage, j.StageAt = latest.stage, latest.at
		}
		if latest.total > 0 {
			j.OCRProcessed, j.OCRTotal = latest.processed, latest.total
		}
	}
	onProgress := func(p worker.Progress) {
		m.mu.Lock()
		latest.stage, latest.at = p.Label(), m.cfg.Clock()
		var percent *float64
		if p.Stage == worker.StageOCR {
			latest.processed, latest.total = p.Processed, p.Total
			if v, ok := p.Percent(); ok {
				percent = &v
			}
		}
		current, err := m.store.GetJob(ctx, id)
		if err != nil {
			m.mu.Unlock()
			return
		}
		applyProgress(&current)
		now := m.cfg.Clock()
		if p.Stage != worker.StageOCR || now.Sub(lastSave) >= 2*time.Second || p.Processed == p.Total {
			if err := m.store.UpdateJob(ctx, current); err == nil {
				lastSave = now
			}
		}
		m.mu.Unlock()
		m.publish(current, percent)
	}
	exitCode, stdout, runErr := m.runner.Run(jobCtx, cmd, onProgress, logs)
	logs.Close()

	m.finish(ctx, id, func(j *Job) {
		applyProgress(j)
		switch {
		case jobCtx.Err() != nil && ctx.Err() == nil:
			j.Status = Cancelled
			j.Error = &Failure{Code: "cancelled", Message: "cancelled while the analysis was running; the worker process tree was stopped"}
		case ctx.Err() != nil:
			j.Status = Interrupted
			j.Error = &Failure{Code: "interrupted", Message: "the service stopped while this job was running; submit a new attempt"}
		case runErr != nil:
			j.Status = Failed
			j.Error = &Failure{Code: "worker_start_failed", Message: runErr.Error()}
		default:
			m.conclude(j, exitCode, stdout)
		}
	})
	cancel()
}

// conclude turns the worker's exit code and stdout into the job's final state.
func (m *Manager) conclude(j *Job, exitCode int, stdout []byte) {
	result, err := worker.Interpret(exitCode, stdout)
	if err != nil {
		j.Status = Failed
		j.Error = failureFrom(err, "worker_contract")
		return
	}
	if !result.Completed() {
		j.Status = Failed
		j.Error = &Failure{Code: result.Error.Code, Message: result.Error.Message}
		j.Result = &result
		return
	}
	artifacts, err := worker.Verify(result)
	if err != nil {
		j.Status = Failed
		j.Error = failureFrom(err, "artifact_verification")
		j.Result = &result
		return
	}
	doc, err := timeline.Load(artifacts.TimelinePath)
	if err != nil {
		j.Status = Failed
		j.Error = &Failure{Code: worker.CodeTimelineInvalid, Message: err.Error()}
		j.Result = &result
		return
	}
	name := j.SourceName
	if name == "" {
		name = doc.Source.Name
	}
	report := Report{ID: "job-" + j.ID, UserID: j.UserID, JobID: j.ID, Origin: "job", SourceName: name, SourceSHA256: result.SourceSHA256,
		ReportPath: artifacts.ReportPath, ReportSHA256: artifacts.ReportSHA256, TimelinePath: artifacts.TimelinePath,
		EvidenceRoot: result.EvidenceRoot, SourcePath: j.SourcePath, CreatedAt: m.cfg.Clock(), DurationMS: doc.Source.DurationMS,
		Turns: len(doc.Turns), Entries: len(doc.Entries)}
	if err := m.store.CreateReport(context.Background(), report); err != nil {
		j.Status = Failed
		j.Error = &Failure{Code: "report_record_failed", Message: err.Error()}
		j.Result = &result
		return
	}
	j.ReportID = report.ID
	j.Result = &result
	j.StageFailure = result.StageFailures
	if result.Status == worker.StatusCompletedWithStageFailures {
		j.Status = CompletedWithStageFailures
	} else {
		j.Status = Succeeded
	}
}

func failureFrom(err error, fallback string) *Failure {
	var ce *worker.ContractError
	if errors.As(err, &ce) {
		return &Failure{Code: ce.Code, Message: ce.Message}
	}
	return &Failure{Code: fallback, Message: err.Error()}
}

// finish applies the final transition under the lock and releases the
// cancellation bookkeeping.
func (m *Manager) finish(ctx context.Context, id string, apply func(*Job)) {
	m.mu.Lock()
	job, err := m.store.GetJob(context.WithoutCancel(ctx), id)
	if err == nil {
		apply(&job)
		job.FinishedAt = m.cfg.Clock()
		job.PID = 0
		if err := m.store.UpdateJob(context.WithoutCancel(ctx), job); err != nil {
			m.log.Error("could not record job outcome", "job", id, "error", err)
		}
	}
	if done := m.done[id]; done != nil {
		close(done)
	}
	delete(m.cancels, id)
	delete(m.done, id)
	m.mu.Unlock()
	if err == nil {
		m.publish(job, nil)
		m.log.Info("job finished", "job", id, "status", job.Status)
	}
}

func (m *Manager) publish(job Job, percent *float64) {
	m.hub.Publish(Event{JobID: job.ID, Status: job.Status, Stage: job.Stage, Percent: percent, Terminal: job.Status.Terminal(), At: m.cfg.Clock()})
}
