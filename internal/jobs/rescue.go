package jobs

import (
	"context"
	"errors"
	"fmt"

	"github.com/andy-dam/tracen-replay/internal/worker"
)

// ErrNotRescuable says a job is not one whose finished output can be taken
// up: only an analysis that ran to the end and whose result was lost on its
// way to the service is.
var ErrNotRescuable = errors.New("only an analysis whose result could not be read can be recovered")

// Rescuable reports whether Rescue applies to a job.
func Rescuable(j Job) bool {
	return j.Status == Failed && j.Error != nil && j.Error.Code == worker.CodeBadTerminalOutput && j.OutputDir != ""
}

// Rescue gives a finished analysis its report when only the hand-off failed:
// the worker wrote the report and the timeline, and something other than its
// terminal object reached its standard output, so the service refused the
// result (bad_terminal_output). The terminal object is rebuilt from the run
// directory and goes through the same verification and recording as any
// other, so nothing is taken on trust; the hours of analysis are not redone.
func (m *Manager) Rescue(ctx context.Context, id string) (Job, error) {
	if m.remote() {
		return Job{}, errors.New("jobs: a job of a shared queue is recovered by its worker, not here")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	job, err := m.store.GetJob(ctx, id)
	if err != nil {
		return Job{}, err
	}
	if !Rescuable(job) {
		return job, ErrNotRescuable
	}
	stdout, err := worker.DescribeOutput(job.OutputDir)
	if err != nil {
		return job, fmt.Errorf("%w: %v", ErrNotRescuable, err)
	}
	rescued := job
	rescued.Error = nil
	m.conclude(&rescued, worker.ExitSuccess, stdout, placement{source: job.SourcePath, output: job.OutputDir, log: job.LogPath})
	if rescued.Status == Failed {
		// The output does not pass verification; the job stays as it was.
		return job, fmt.Errorf("%w: %s", ErrNotRescuable, rescued.Error.Message)
	}
	if err := m.store.UpdateJob(ctx, rescued); err != nil {
		return job, err
	}
	m.log.Info("job recovered from its finished output", "job", id, "status", rescued.Status)
	go m.publish(rescued, nil)
	return rescued, nil
}

// RescueAll recovers every job that Rescue applies to. The desktop
// application runs it at its start, so an analysis that failed this way
// before the fix has its report the next time the application opens.
func (m *Manager) RescueAll(ctx context.Context, userID string) int {
	listed, err := m.store.ListJobsForUser(ctx, userID)
	if err != nil {
		return 0
	}
	recovered := 0
	for _, job := range listed {
		if !Rescuable(job) {
			continue
		}
		if _, err := m.Rescue(ctx, job.ID); err == nil {
			recovered++
		} else {
			m.log.Warn("job not recovered", "job", job.ID, "error", err)
		}
	}
	return recovered
}
