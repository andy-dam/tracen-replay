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

	"github.com/andy-dam/tracen-replay/internal/objectstore"
	"github.com/andy-dam/tracen-replay/internal/queue"
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
	// MaxActivePerUser bounds one user's queued plus running jobs; zero
	// leaves it off. One keeps a single user from filling the queue.
	MaxActivePerUser int
	// DailyPerUser and DailyTotal bound the analyses started in any 24
	// hours by one user and by everyone; zero leaves each off. Analyses
	// are the service's whole cost, so these are what keep a year within
	// its budget.
	DailyPerUser int
	DailyTotal   int
	// MonthlyTotal bounds the analyses everyone together starts in any 30
	// days; zero leaves it off. It is the budget's last fence: what the
	// daily bound lets through on a busy month.
	MonthlyTotal int
	// MaxDuration bounds one analysis's wall-clock time; zero leaves it
	// off. A worker that runs past it is stopped and the job fails as
	// timed_out.
	MaxDuration time.Duration
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
	// OriginalLifetime is how long after its upload an original can still
	// be analyzed (the object store's lifecycle deletes it after); zero
	// means for good. A submission past it is refused up front.
	OriginalLifetime time.Duration
	// Queue, when set, is shared with other processes: Submit puts the job
	// id on it and a worker process takes ids from it with Work, while Run
	// is not used. Objects then holds every file: the recording under
	// Job.SourcePath (an object key), the outputs under Job.OutputDir (a
	// key prefix) and the log under Job.LogPath. Scratch is the worker's
	// directory for the downloaded recording and the run.
	Queue   queue.Queue
	Objects objectstore.Store
	Scratch string
	// KeepCopy, when set in the shared-queue mode, encodes the playback
	// copy of a recording (source to destination file) after a completed
	// analysis; the copy is stored under kept/ and the report and the
	// recording play from it. nil keeps no copy.
	KeepCopy func(ctx context.Context, src, dst string) error
	// Poll is how often, in the shared-queue mode, a worker looks at an
	// empty queue and for a cancellation request, and the API process
	// re-reads the active jobs; zero means a few seconds.
	Poll time.Duration
	// ExitWhenIdle makes Work return once the queue has been empty for a
	// few polls in a row and nothing is running: a worker started on
	// demand (a Container Apps job) ends instead of waiting.
	ExitWhenIdle bool
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

// LimitError is returned by Submit when a per-user or daily bound is
// reached. Code is the API's error code: too_many_jobs for the active
// bound, daily_limit for the day's.
type LimitError struct {
	Code    string
	Limit   int
	Message string
}

func (e *LimitError) Error() string { return e.Message }

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
	case cfg.Queue == nil && (cfg.DataDir == "" || cfg.Python == "" || cfg.WorkDir == ""):
		return nil, errors.New("jobs: data directory, python and working directory are required")
	case cfg.Queue != nil && cfg.Objects == nil:
		return nil, errors.New("jobs: a shared queue needs an object store")
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

// SetOCRDevice changes the device the next analyses run on (auto, cpu,
// dml, cuda, coreml); an analysis already running keeps its own.
func (m *Manager) SetOCRDevice(device string) {
	m.mu.Lock()
	m.cfg.OCRDevice = device
	m.mu.Unlock()
}

// ocrDevice is the device the next analysis runs on.
func (m *Manager) ocrDevice() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.cfg.OCRDevice
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
// With a shared queue the running jobs belong to other processes, which
// Follow and Work watch instead.
func (m *Manager) Recover(ctx context.Context) ([]Job, error) {
	if m.remote() {
		return nil, nil
	}
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
	if err := m.withinLimits(ctx, userID); err != nil {
		return Job{}, err
	}
	id := m.cfg.NewID()
	job := Job{ID: id, UserID: userID, SourceID: source.ID, SourcePath: source.Path, SourceName: source.Name, Status: Queued,
		CreatedAt: m.cfg.Clock()}
	if m.remote() {
		job.OutputDir, job.LogPath = "jobs/"+id+"/run", "jobs/"+id+"/worker.log"
	} else {
		dir := filepath.Join(m.cfg.DataDir, "jobs", id)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return Job{}, err
		}
		job.OutputDir, job.LogPath = filepath.Join(dir, "run"), filepath.Join(dir, "worker.log")
	}
	if err := m.store.CreateJob(ctx, job); err != nil {
		return Job{}, err
	}
	if m.remote() {
		if err := m.cfg.Queue.Enqueue(ctx, id); err != nil {
			job.Status, job.FinishedAt = Failed, m.cfg.Clock()
			job.Error = &Failure{Code: "queue_unavailable", Message: "the analysis could not be queued: " + err.Error()}
			m.store.UpdateJob(ctx, job)
			return Job{}, err
		}
	}
	m.publish(job, nil)
	select {
	case m.wake <- struct{}{}:
	default:
	}
	return job, nil
}

// withinLimits refuses a submission that would pass the user's active
// bound or the day's budgets. Called with m.mu held, so two submissions
// cannot both slip under a bound.
func (m *Manager) withinLimits(ctx context.Context, userID string) error {
	if m.cfg.MaxActivePerUser > 0 {
		active, err := m.store.CountActiveForUser(ctx, userID)
		if err != nil {
			return err
		}
		if active >= m.cfg.MaxActivePerUser {
			return &LimitError{Code: "too_many_jobs", Limit: m.cfg.MaxActivePerUser,
				Message: fmt.Sprintf("you already have %d analysis queued or running; wait for it to finish", active)}
		}
	}
	since := m.cfg.Clock().Add(-24 * time.Hour)
	if m.cfg.DailyPerUser > 0 {
		n, err := m.store.CountJobsSince(ctx, userID, since)
		if err != nil {
			return err
		}
		if n >= m.cfg.DailyPerUser {
			return &LimitError{Code: "daily_limit", Limit: m.cfg.DailyPerUser,
				Message: fmt.Sprintf("you have started %d analyses in the last 24 hours, the most this service allows; try again tomorrow", n)}
		}
	}
	if m.cfg.DailyTotal > 0 {
		n, err := m.store.CountJobsSince(ctx, "", since)
		if err != nil {
			return err
		}
		if n >= m.cfg.DailyTotal {
			return &LimitError{Code: "daily_limit", Limit: m.cfg.DailyTotal,
				Message: "the service has started as many analyses as it runs in a day; try again tomorrow"}
		}
	}
	if m.cfg.MonthlyTotal > 0 {
		n, err := m.store.CountJobsSince(ctx, "", m.cfg.Clock().Add(-30*24*time.Hour))
		if err != nil {
			return err
		}
		if n >= m.cfg.MonthlyTotal {
			return &LimitError{Code: "monthly_limit", Limit: m.cfg.MonthlyTotal,
				Message: "the service has started as many analyses as it runs in a month; try again in a few days"}
		}
	}
	return nil
}

// resolve finds the recording behind a source id among the user's own
// uploads. Another user's upload is not found.
func (m *Manager) resolve(ctx context.Context, userID, sourceID string) (Source, error) {
	recording, rerr := m.cfg.Recordings.GetRecording(ctx, sourceID)
	if rerr != nil || recording.UserID != userID {
		return Source{}, &NotFoundError{Kind: "source", ID: sourceID}
	}
	if m.cfg.OriginalLifetime > 0 && m.cfg.Clock().After(recording.CreatedAt.Add(m.cfg.OriginalLifetime)) {
		return Source{}, &LimitError{Code: "original_expired",
			Message: "the original recording is past the time it is kept for and cannot be analyzed again; upload it again to analyze it"}
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
		if cancel == nil && m.remote() {
			// The worker process that runs it will see the request within
			// seconds and stop; the record reaches cancelled when it has.
			job.CancelRequested = true
			err := m.store.UpdateJob(ctx, job)
			m.mu.Unlock()
			return job, err
		}
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
	if m.cfg.MaxDuration > 0 {
		jobCtx, cancel = context.WithTimeout(ctx, m.cfg.MaxDuration)
	}
	done := make(chan struct{})
	m.cancels[id], m.done[id] = cancel, done
	job.Status = Running
	job.StartedAt = m.cfg.Clock()
	job.HeartbeatAt = job.StartedAt
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

	// Where the worker reads and writes: the job's own paths, or, with a
	// shared queue, a scratch directory the recording is fetched into and
	// the outputs are uploaded from.
	place := placement{source: job.SourcePath, output: job.OutputDir, log: job.LogPath}
	if m.remote() {
		var err error
		if place, err = m.fetch(jobCtx, job); err != nil {
			m.finish(ctx, id, func(j *Job) {
				j.Status = Failed
				j.Error = &Failure{Code: "source_unavailable", Message: "the recording could not be fetched: " + err.Error()}
			})
			cancel()
			return
		}
		defer os.RemoveAll(filepath.Dir(place.output))
	}
	cmd := worker.Command{Python: m.cfg.Python, WorkDir: m.cfg.WorkDir, Source: place.source, Output: place.output,
		ModelDir: m.cfg.ModelDir, Workers: m.cfg.Workers, DenseWorkers: m.cfg.DenseWorkers, OCRDevice: m.ocrDevice(),
		LearnedReader: m.cfg.LearnedReader, PruneFrames: true,
		PruneWorkingData: !m.cfg.KeepWorkingData, OwnerPID: os.Getpid()}
	logs, err := os.Create(place.log)
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
		current.HeartbeatAt = now
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
	if m.remote() {
		if err := m.putFile(context.WithoutCancel(ctx), job.LogPath, place.log, "text/plain; charset=utf-8"); err != nil {
			m.log.Warn("worker log not uploaded", "job", id, "error", err)
		}
		// The playback copy is made here, outside the lock the outcome is
		// recorded under: an encode takes a while.
		if runErr == nil && jobCtx.Err() == nil && m.cfg.KeepCopy != nil {
			if result, err := worker.Interpret(exitCode, stdout); err == nil && result.Completed() {
				place.kept = m.keepCopy(jobCtx, job, place)
			}
		}
	}

	m.finish(ctx, id, func(j *Job) {
		applyProgress(j)
		switch {
		case errors.Is(jobCtx.Err(), context.DeadlineExceeded) && ctx.Err() == nil:
			j.Status = Failed
			j.Error = &Failure{Code: "timed_out", Message: fmt.Sprintf("the analysis ran longer than %s and was stopped", m.cfg.MaxDuration)}
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
			m.conclude(j, exitCode, stdout, place)
		}
	})
	cancel()
}

// conclude turns the worker's exit code and stdout into the job's final state.
func (m *Manager) conclude(j *Job, exitCode int, stdout []byte, place placement) {
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
	if m.remote() {
		// The run's files go to the object store, and the report names them
		// by key; the recording is the key it was analyzed from.
		keys, err := m.upload(context.Background(), j.OutputDir, place.output)
		if err != nil {
			j.Status = Failed
			j.Error = &Failure{Code: "upload_failed", Message: "the analysis finished but its files could not be stored: " + err.Error()}
			j.Result = &result
			return
		}
		report.ReportPath, report.TimelinePath = keys[artifacts.ReportPath], keys[artifacts.TimelinePath]
		report.EvidenceRoot = j.OutputDir
		if place.kept != "" {
			report.SourcePath = place.kept
		}
		if report.ReportPath == "" || report.TimelinePath == "" {
			j.Status = Failed
			j.Error = &Failure{Code: "upload_failed", Message: "the report or the timeline lies outside the run directory"}
			j.Result = &result
			return
		}
	}
	if err := m.store.CreateReport(context.Background(), report); err != nil {
		j.Status = Failed
		j.Error = &Failure{Code: "report_record_failed", Message: err.Error()}
		j.Result = &result
		return
	}
	j.ReportID = report.ID
	j.Result = &result
	j.StageFailure = result.StageFailures
	m.learned(j, result.SourceSHA256, place.kept)
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
