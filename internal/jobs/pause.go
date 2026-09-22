package jobs

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Pausing and resuming. A paused analysis is a stopped worker whose run
// directory is kept: the analyzer writes its captured frames and every
// frame's OCR row to that directory as it goes and reads them back when it
// is started on it again, so a resumed job goes back to the queue and
// continues from what is there. Paused files take space, so a deployment
// may bound how long they are kept (PausedLifetime); past it the job is
// cancelled, its files go, and its recording reads as never analyzed.

// expireEvery is how often paused jobs are checked against the lifetime.
const expireEvery = time.Minute

// SetPausedLifetime changes how long a paused job's progress is kept; zero
// keeps it for good.
func (m *Manager) SetPausedLifetime(d time.Duration) {
	m.mu.Lock()
	m.cfg.PausedLifetime = max(0, d)
	m.mu.Unlock()
}

func (m *Manager) pausedLifetime() time.Duration {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.cfg.PausedLifetime
}

// served fills in what is computed when a record is handed out.
func (m *Manager) served(job Job) Job {
	if lifetime := m.pausedLifetime(); job.Status == Paused && lifetime > 0 && !job.PausedAt.IsZero() {
		job.PausedUntil = job.PausedAt.Add(lifetime)
	}
	return job
}

// Pause stops a queued or running job and keeps what it has done. For a
// running job it stops the worker's process tree and waits, within a bound,
// for the record to reach paused; the returned job carries whatever state
// was reached.
func (m *Manager) Pause(ctx context.Context, id string) (Job, error) {
	m.mu.Lock()
	job, err := m.store.GetJob(ctx, id)
	if err != nil {
		m.mu.Unlock()
		return Job{}, err
	}
	switch job.Status {
	case Queued:
		job.Status, job.PausedAt = Paused, m.cfg.Clock()
		err := m.store.UpdateJob(ctx, job)
		m.mu.Unlock()
		if err == nil {
			m.publish(job, nil)
		}
		return m.served(job), err
	case Running:
		cancel, done := m.cancels[id], m.done[id]
		if cancel == nil && m.remote() {
			// The worker process that runs it sees the request within
			// seconds; the record reaches paused when it has stopped.
			job.PauseRequested = true
			err := m.store.UpdateJob(ctx, job)
			m.mu.Unlock()
			return job, err
		}
		if cancel == nil {
			m.mu.Unlock()
			return job, &TransitionError{ID: id, Status: job.Status, Action: "pause: it is owned by another process"}
		}
		m.pausing[id] = true
		m.mu.Unlock()
		cancel()
		if done != nil {
			select {
			case <-done:
			case <-time.After(15 * time.Second):
			case <-ctx.Done():
			}
		}
		job, err := m.store.GetJob(ctx, id)
		return m.served(job), err
	default:
		m.mu.Unlock()
		return job, &TransitionError{ID: id, Status: job.Status, Action: "pause"}
	}
}

// pauseDead pauses a job whose worker died: its files are where the worker
// left them, and Resume continues it. Called with m.mu held.
func (m *Manager) pauseDead(ctx context.Context, job *Job) error {
	m.markPaused(job)
	job.Error = &Failure{Code: stoppedOver.Code, Message: "The worker running this analysis stopped answering. Resume continues it."}
	return m.store.UpdateJob(ctx, *job)
}

// pauseLocal pauses a run of this process; the worker calls it when the
// record asks for a pause.
func (m *Manager) pauseLocal(id string) {
	m.mu.Lock()
	cancel := m.cancels[id]
	if cancel != nil {
		m.pausing[id] = true
	}
	m.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

// wasPaused reports, once, whether the run that just ended was stopped by a
// pause.
func (m *Manager) wasPaused(id string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	paused := m.pausing[id]
	delete(m.pausing, id)
	return paused
}

// Resume puts a paused job back in the queue. It is the same job: it does
// not count against the day's budget again.
func (m *Manager) Resume(ctx context.Context, id string) (Job, error) {
	m.mu.Lock()
	job, err := m.store.GetJob(ctx, id)
	if err != nil {
		m.mu.Unlock()
		return Job{}, err
	}
	if job.Status != Paused && job.Status != Interrupted {
		m.mu.Unlock()
		return job, &TransitionError{ID: id, Status: job.Status, Action: "resume"}
	}
	if job.Status == Interrupted {
		// A record from before an interruption was a pause: it was recorded
		// as finished, and its run directory was kept.
		if !job.StartedAt.IsZero() && job.FinishedAt.After(job.StartedAt) {
			job.RanSeconds += int64(job.FinishedAt.Sub(job.StartedAt) / time.Second)
		}
		job.FinishedAt = time.Time{}
	}
	if lifetime := m.cfg.PausedLifetime; lifetime > 0 && !job.PausedAt.IsZero() && !m.cfg.Clock().Before(job.PausedAt.Add(lifetime)) {
		m.mu.Unlock()
		m.ExpirePaused(ctx)
		job, _ = m.store.GetJob(ctx, id)
		return job, &TransitionError{ID: id, Status: job.Status, Action: "resume: its progress is past the time it was kept for"}
	}
	pausedAt := job.PausedAt
	job.Status, job.PausedAt, job.PauseRequested, job.CancelRequested, job.Error = Queued, time.Time{}, false, false, nil
	if err := m.store.UpdateJob(ctx, job); err != nil {
		m.mu.Unlock()
		return Job{}, err
	}
	if m.remote() {
		if err := m.cfg.Queue.Enqueue(ctx, id); err != nil {
			job.Status, job.PausedAt = Paused, pausedAt
			m.store.UpdateJob(ctx, job)
			m.mu.Unlock()
			return m.served(job), err
		}
	}
	m.mu.Unlock()
	m.publish(job, nil)
	m.wakeLoop()
	return job, nil
}

// ExpirePaused cancels every job that stayed paused past the lifetime and
// deletes its working files; it returns how many it cancelled.
func (m *Manager) ExpirePaused(ctx context.Context) int {
	lifetime := m.pausedLifetime()
	if lifetime <= 0 {
		return 0
	}
	paused, err := m.store.ListPausedJobs(ctx)
	if err != nil {
		m.log.Warn("paused jobs not read", "error", err)
		return 0
	}
	expired := 0
	for _, candidate := range paused {
		if candidate.PausedAt.IsZero() || m.cfg.Clock().Before(candidate.PausedAt.Add(lifetime)) {
			continue
		}
		m.mu.Lock()
		job, err := m.store.GetJob(ctx, candidate.ID)
		if err != nil || job.Status != Paused {
			m.mu.Unlock()
			continue
		}
		job.Status, job.FinishedAt = Cancelled, m.cfg.Clock()
		job.Error = &Failure{Code: CodePauseExpired, Message: fmt.Sprintf("Paused progress is kept for %s. It was deleted.", spoken(lifetime))}
		err = m.store.UpdateJob(ctx, job)
		m.mu.Unlock()
		if err != nil {
			continue
		}
		m.discard(job)
		m.publish(job, nil)
		m.log.Info("paused job expired", "job", job.ID)
		expired++
	}
	return expired
}

// expireLoop checks the paused jobs now and then until ctx ends.
func (m *Manager) expireLoop(ctx context.Context) {
	ticker := time.NewTicker(expireEvery)
	defer ticker.Stop()
	for {
		m.ExpirePaused(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// discard deletes the working files a job that will not run again left: its
// run directory. With a shared queue the files are in the workers' scratch
// space, which this process may not see, so the job's id goes on the queue
// once more: the worker that takes it finds the job settled, deletes its
// scratch directory and drops the message (dropSettled). A worker started
// only for that ends a few polls later.
func (m *Manager) discard(job Job) {
	if m.remote() {
		if err := m.cfg.Queue.Enqueue(context.Background(), job.ID); err != nil {
			m.log.Warn("scratch clean-up not queued; the next worker to start sweeps it", "job", job.ID, "error", err)
		}
		return
	}
	dir, within := job.OutputDir, m.cfg.DataDir
	if dir == "" || within == "" || !strings.HasPrefix(filepath.Clean(dir), filepath.Clean(within)+string(filepath.Separator)) {
		return
	}
	if err := os.RemoveAll(dir); err != nil {
		m.log.Warn("working files not deleted", "job", job.ID, "error", err)
	}
}

// dropSettled, in a worker, deletes the scratch directory of a job whose
// message was taken and that will not run again.
func (m *Manager) dropSettled(ctx context.Context, id string) {
	if m.cfg.Scratch == "" || !plainID(id) {
		return
	}
	job, err := m.store.GetJob(ctx, id)
	if err != nil || job.Status == Queued || job.Status == Running || job.Status == Paused {
		return
	}
	dir := filepath.Join(m.cfg.Scratch, id)
	if _, err := os.Stat(dir); err != nil {
		return
	}
	if err := os.RemoveAll(dir); err != nil {
		m.log.Warn("scratch directory not deleted", "job", id, "error", err)
		return
	}
	m.log.Info("scratch directory of a settled job deleted", "job", id)
}

// sweepScratch, in a worker, deletes the scratch directories of jobs that
// will not run again: a run that ended without cleaning up, or a paused job
// that expired while no process could see this directory. A directory that
// names no job goes once it is a day old.
func (m *Manager) sweepScratch(ctx context.Context) {
	entries, err := os.ReadDir(m.cfg.Scratch)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() || !plainID(entry.Name()) {
			continue
		}
		job, err := m.store.GetJob(ctx, entry.Name())
		var missing *NotFoundError
		switch {
		case err == nil && (job.Status == Queued || job.Status == Running || job.Status == Paused):
			continue
		case err == nil:
		case errors.As(err, &missing):
			if info, err := entry.Info(); err != nil || m.cfg.Clock().Sub(info.ModTime()) < 24*time.Hour {
				continue
			}
		default:
			continue
		}
		if err := os.RemoveAll(filepath.Join(m.cfg.Scratch, entry.Name())); err == nil {
			m.log.Info("stale scratch directory deleted", "job", entry.Name())
		}
	}
}

// plainID reports whether id is safe as a directory name: the ids this
// package makes are hexadecimal.
func plainID(id string) bool {
	if id == "" || len(id) > 64 {
		return false
	}
	for _, c := range id {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c == '-' || c == '_') {
			return false
		}
	}
	return true
}

// spoken writes a lifetime the way a person says it.
func spoken(d time.Duration) string {
	switch {
	case d%(24*time.Hour) == 0 && d >= 48*time.Hour:
		return fmt.Sprintf("%d days", d/(24*time.Hour))
	case d == time.Hour:
		return "1 hour"
	case d%time.Hour == 0:
		return fmt.Sprintf("%d hours", d/time.Hour)
	}
	return d.String()
}
