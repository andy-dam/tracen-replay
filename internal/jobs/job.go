// Package jobs owns the analysis job lifecycle: admission, the durable queue,
// the single worker loop, cancellation and restart recovery. Recognition and
// accounting stay in the Python worker; this package only decides when a
// worker runs and what its outcome means for the job record.
package jobs

import (
	"context"
	"io"
	"time"

	"github.com/andy-dam/tracen-replay/internal/worker"
)

// Status is a job state. Transitions: queued -> running -> succeeded |
// completed_with_stage_failures | failed; queued | running -> cancelled;
// running -> interrupted when the service restarts over an unfinished run.
type Status string

const (
	Queued                     Status = "queued"
	Running                    Status = "running"
	Succeeded                  Status = "succeeded"
	CompletedWithStageFailures Status = "completed_with_stage_failures"
	Failed                     Status = "failed"
	Cancelled                  Status = "cancelled"
	Interrupted                Status = "interrupted"
)

// Terminal reports whether no further transition is possible.
func (s Status) Terminal() bool {
	switch s {
	case Succeeded, CompletedWithStageFailures, Failed, Cancelled, Interrupted:
		return true
	}
	return false
}

// Completed reports whether the job produced a viewable report.
func (s Status) Completed() bool {
	return s == Succeeded || s == CompletedWithStageFailures
}

// Failure is the reason a job did not complete: the worker's own error
// envelope, a contract violation found on the Go side, or a service event
// such as a cancellation.
type Failure struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Job is the durable record of one analysis attempt.
type Job struct {
	ID           string                `json:"id"`
	UserID       string                `json:"user_id,omitempty"`
	SourceID     string                `json:"source_id"`
	SourcePath   string                `json:"source_path"`
	SourceName   string                `json:"source_name"`
	Status       Status                `json:"status"`
	CreatedAt    time.Time             `json:"created_at"`
	StartedAt    time.Time             `json:"started_at,omitzero"`
	FinishedAt   time.Time             `json:"finished_at,omitzero"`
	OutputDir    string                `json:"output_dir"`
	LogPath      string                `json:"log_path,omitempty"`
	PID          int                   `json:"pid,omitempty"`
	Stage        string                `json:"stage,omitempty"`
	StageAt      time.Time             `json:"stage_at,omitzero"`
	OCRProcessed int                   `json:"ocr_processed,omitempty"`
	OCRTotal     int                   `json:"ocr_total,omitempty"`
	Error        *Failure              `json:"error,omitempty"`
	StageFailure []worker.StageFailure `json:"stage_failures,omitempty"`
	ReportID     string                `json:"report_id,omitempty"`
	Result       *worker.Result        `json:"result,omitempty"`
}

// Report is a validated, immutable analyzer output: produced by a job or
// registered from an existing bundle.
type Report struct {
	ID           string    `json:"id"`
	UserID       string    `json:"user_id,omitempty"`
	JobID        string    `json:"job_id,omitempty"`
	Origin       string    `json:"origin"` // "job" or "imported"
	SourceName   string    `json:"source_name"`
	SourceSHA256 string    `json:"source_sha256"`
	ReportPath   string    `json:"report_path"`
	ReportSHA256 string    `json:"report_sha256"`
	TimelinePath string    `json:"timeline_path"`
	EvidenceRoot string    `json:"evidence_root"`
	SourcePath   string    `json:"source_path,omitempty"`
	CreatedAt    time.Time `json:"created_at"`
	DurationMS   int64     `json:"duration_ms"`
	Turns        int       `json:"turns"`
	Entries      int       `json:"entries"`
}

// Store is the durable job and report record. The ForUser variants return
// the user's own records plus records without an owner (bundles registered
// from the command line before accounts existed).
type Store interface {
	CreateJob(ctx context.Context, job Job) error
	UpdateJob(ctx context.Context, job Job) error
	GetJob(ctx context.Context, id string) (Job, error)
	ListJobs(ctx context.Context) ([]Job, error)
	ListJobsForUser(ctx context.Context, userID string) ([]Job, error)
	NextQueued(ctx context.Context) (Job, bool, error)
	CountByStatus(ctx context.Context, status Status) (int, error)
	MarkInterrupted(ctx context.Context, at time.Time) ([]Job, error)
	CreateReport(ctx context.Context, report Report) error
	GetReport(ctx context.Context, id string) (Report, error)
	ListReports(ctx context.Context) ([]Report, error)
	ListReportsForUser(ctx context.Context, userID string) ([]Report, error)
}

// StorageUsage is how much of the upload space a user, or everyone, holds.
type StorageUsage struct {
	Count int   `json:"count"`
	Bytes int64 `json:"bytes"`
}

// Recording is a video a user uploaded through the service; it lives under
// the service's data directory and belongs to that user only.
type Recording struct {
	ID        string    `json:"id"`
	UserID    string    `json:"user_id"`
	Name      string    `json:"name"`
	Path      string    `json:"-"`
	Size      int64     `json:"size"`
	SHA256    string    `json:"sha256"`
	CreatedAt time.Time `json:"created_at"`
}

// Recordings resolves uploaded recordings; a job may analyze one of them
// when it belongs to the submitting user.
type Recordings interface {
	GetRecording(ctx context.Context, id string) (Recording, error)
}

// ErrNotFound is returned by a Store for an unknown id.
type NotFoundError struct{ Kind, ID string }

func (e *NotFoundError) Error() string { return e.Kind + " " + e.ID + " not found" }

// Runner starts one worker process and blocks until it exits or ctx is
// cancelled, in which case the whole process tree must be gone before Run
// returns. Progress lines are delivered through onProgress as they arrive;
// the complete stderr stream is copied to logs. Run returns the exit code and
// the captured stdout; err is only for failures to start or to observe the
// process, never for a non-zero exit.
type Runner interface {
	Run(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (exitCode int, stdout []byte, err error)
}

// Source is a recording the service is allowed to analyze, named by a
// server-issued identifier rather than a client-supplied path.
type Source struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Path string `json:"-"`
	Size int64  `json:"size"`
}
