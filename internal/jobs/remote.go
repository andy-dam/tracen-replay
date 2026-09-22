package jobs

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"mime"
	"os"
	"path"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/andy-dam/tracen-replay/internal/queue"
)

// The shared-queue mode: the API process submits and follows, a worker
// process, possibly on another machine, takes jobs from the queue, fetches
// the recording, runs the analyzer and uploads what it made.

const (
	// hold is how long a taken message stays hidden, and holdRenew how
	// often the worker renews it while the job runs.
	hold      = 10 * time.Minute
	holdRenew = 4 * time.Minute
	// heartbeatEvery is how often a running job's record is touched when
	// no progress line does it; staleAfter is how long a silence means the
	// worker died.
	heartbeatEvery = time.Minute
	staleAfter     = 15 * time.Minute
	// maxTakes is how often a queued job's message may come back before the
	// job is failed instead of tried again.
	maxTakes = 3
	// idlePollsBeforeExit is how many empty polls in a row end a worker
	// that exits when idle.
	idlePollsBeforeExit = 3
	// defaultPoll is the wait between looks at an empty queue, for a
	// cancellation request, and at the active jobs.
	defaultPoll = 3 * time.Second
)

// placement is where one run's files are on this machine, and, once
// made, the key of the playback copy.
type placement struct{ source, output, log, kept string }

func (m *Manager) remote() bool { return m.cfg.Queue != nil }

func (m *Manager) poll() time.Duration {
	if m.cfg.Poll > 0 {
		return m.cfg.Poll
	}
	return defaultPoll
}

// Work takes jobs from the shared queue and runs them, up to Parallel at
// once, until ctx is cancelled.
func (m *Manager) Work(ctx context.Context) error {
	if !m.remote() {
		return errors.New("jobs: Work needs a shared queue")
	}
	if m.cfg.Python == "" || m.cfg.WorkDir == "" || m.cfg.Scratch == "" {
		return errors.New("jobs: python, working directory and scratch directory are required to work")
	}
	if err := os.MkdirAll(m.cfg.Scratch, 0o755); err != nil {
		return err
	}
	m.sweepScratch(ctx)
	slots := make(chan struct{}, max(1, m.cfg.Parallel))
	var running sync.WaitGroup
	defer running.Wait()
	idle := 0
	for {
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			return nil
		}
		message, ok, err := m.cfg.Queue.Receive(ctx, hold)
		if err != nil && ctx.Err() == nil {
			m.log.Error("queue read failed", "error", err)
		}
		if !ok {
			<-slots
			idle++
			if m.cfg.ExitWhenIdle && err == nil && idle >= idlePollsBeforeExit && len(slots) == 0 {
				m.log.Info("queue empty; exiting as asked")
				return nil
			}
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(m.poll()):
			}
			continue
		}
		idle = 0
		id := message.Body
		if !m.takeable(ctx, message) {
			m.cfg.Queue.Delete(ctx, message)
			m.dropSettled(ctx, id)
			<-slots
			continue
		}
		claimed := make(chan struct{})
		running.Add(1)
		go func() {
			defer running.Done()
			defer func() { <-slots }()
			// Every renewal of the hold gives the message a new receipt, and
			// only the latest one deletes it. The watch hands back the
			// message as it last held it. Deleting with the receipt the
			// message was taken with failed after any run longer than one
			// renewal: the message stayed, hidden, for the rest of its hold,
			// and the queue-length trigger started an idle worker every
			// minute until it came back and was dropped.
			watchCtx, stopWatch := context.WithCancel(context.Background())
			latest := make(chan queue.Message, 1)
			go func() { latest <- m.watch(watchCtx, id, message) }()
			m.runOne(ctx, id, claimed)
			stopWatch()
			message := <-latest
			if err := m.cfg.Queue.Delete(context.WithoutCancel(ctx), message); err != nil {
				m.log.Warn("finished job message not deleted", "job", id, "error", err)
			}
		}()
		<-claimed
	}
}

// takeable decides whether a message is a job to run now. A job that is no
// longer queued (cancelled, or left running by a worker that died) is
// settled and its message dropped; a message that keeps coming back fails
// the job rather than running it again.
func (m *Manager) takeable(ctx context.Context, message queue.Message) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	job, err := m.store.GetJob(ctx, message.Body)
	if err != nil {
		m.log.Warn("queued job unknown", "job", message.Body, "error", err)
		return false
	}
	switch {
	case job.Status == Running && message.Dequeued > 1:
		m.pauseDead(ctx, &job)
		m.publish(job, nil)
		m.log.Warn("job left running by a dead worker; paused", "job", job.ID)
		return false
	case job.Status != Queued:
		return false
	case message.Dequeued > maxTakes:
		job.Status, job.FinishedAt = Failed, m.cfg.Clock()
		job.Error = &Failure{Code: "worker_unavailable", Message: fmt.Sprintf("the job was handed to a worker %d times without starting", message.Dequeued-1)}
		m.store.UpdateJob(ctx, job)
		m.publish(job, nil)
		return false
	}
	return true
}

// watch keeps a running job's message held and its heartbeat fresh, and
// stops the run when a cancellation was requested or the message was lost.
// It returns the message with the receipt of its latest renewal.
func (m *Manager) watch(ctx context.Context, id string, message queue.Message) queue.Message {
	every := m.cfg.HoldRenew
	if every <= 0 {
		every = holdRenew
	}
	renew := time.NewTicker(every)
	defer renew.Stop()
	beat := time.NewTicker(heartbeatEvery)
	defer beat.Stop()
	look := time.NewTicker(m.poll())
	defer look.Stop()
	for {
		select {
		case <-ctx.Done():
			return message
		case <-renew.C:
			// Not bound to ctx: a renewal cut off half way by the end of
			// the run could leave the queue with a receipt nobody holds.
			extendCtx, done := context.WithTimeout(context.Background(), 30*time.Second)
			extended, err := m.cfg.Queue.Extend(extendCtx, message, hold)
			done()
			if errors.Is(err, queue.ErrStale) {
				m.log.Warn("job message lost while running; stopping", "job", id)
				m.stop(id)
				return message
			}
			if err == nil {
				message = extended
			}
		case <-beat.C:
			// Bounded: a database call that hangs must not hold the lock
			// every other record update waits on.
			beatCtx, done := context.WithTimeout(ctx, 30*time.Second)
			m.mu.Lock()
			if job, err := m.store.GetJob(beatCtx, id); err == nil && job.Status == Running {
				job.HeartbeatAt = m.cfg.Clock()
				if err := m.store.UpdateJob(beatCtx, job); err != nil {
					m.log.Warn("heartbeat not written", "job", id, "error", err)
				}
			}
			m.mu.Unlock()
			done()
		case <-look.C:
			lookCtx, done := context.WithTimeout(ctx, 30*time.Second)
			if job, err := m.store.GetJob(lookCtx, id); err == nil && job.CancelRequested {
				m.stop(id)
			} else if err == nil && job.PauseRequested {
				m.pauseLocal(id)
			}
			done()
		}
	}
}

// stop cancels a run of this process.
func (m *Manager) stop(id string) {
	m.mu.Lock()
	cancel := m.cancels[id]
	m.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

// Follow, in the API process, reads the active jobs and publishes what
// changed, so an event stream served here follows a worker elsewhere. A
// running job whose heartbeat has gone stale is marked interrupted.
func (m *Manager) Follow(ctx context.Context) {
	type seen struct {
		status           Status
		stage            string
		processed, total int
	}
	last := map[string]seen{}
	go m.expireLoop(ctx)
	ticker := time.NewTicker(m.poll())
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		active, err := m.store.ListActiveJobs(ctx)
		if err != nil {
			continue
		}
		current := map[string]bool{}
		for _, job := range active {
			current[job.ID] = true
			if job.Status == Running && !job.HeartbeatAt.IsZero() && m.cfg.Clock().Sub(job.HeartbeatAt) > staleAfter {
				m.mu.Lock()
				if fresh, err := m.store.GetJob(ctx, job.ID); err == nil && fresh.Status == Running {
					m.pauseDead(ctx, &fresh)
					job = fresh
				}
				m.mu.Unlock()
			}
			now := seen{job.Status, job.Stage, job.OCRProcessed, job.OCRTotal}
			if before, ok := last[job.ID]; !ok || before != now {
				var percent *float64
				if job.OCRTotal > 0 {
					v := 100 * float64(job.OCRProcessed) / float64(job.OCRTotal)
					percent = &v
				}
				m.publish(job, percent)
			}
			if job.Status.Terminal() {
				delete(last, job.ID)
				delete(current, job.ID)
			} else {
				last[job.ID] = now
			}
		}
		for id := range last {
			if current[id] {
				continue
			}
			delete(last, id)
			if job, err := m.store.GetJob(ctx, id); err == nil {
				m.publish(job, nil)
			}
		}
	}
}

// learned takes back to the recording what the analysis found out: its
// hash, when the upload arrived without one, and the playback copy.
func (m *Manager) learned(j *Job, sourceSHA256, kept string) {
	updater, ok := m.cfg.Recordings.(RecordingUpdater)
	if !ok || (sourceSHA256 == "" && kept == "") {
		return
	}
	ctx := context.Background()
	recording, err := m.cfg.Recordings.GetRecording(ctx, j.SourceID)
	if err != nil {
		return
	}
	changed := false
	if sourceSHA256 != "" && recording.SHA256 != sourceSHA256 {
		recording.SHA256, changed = sourceSHA256, true
	}
	if kept != "" && recording.KeptPath != kept {
		recording.KeptPath, changed = kept, true
	}
	if !changed {
		return
	}
	if err := updater.UpdateRecording(ctx, recording); err != nil {
		m.log.Warn("recording not updated after the analysis", "recording", j.SourceID, "error", err)
	}
}

// keepCopy encodes the playback copy of the job's recording and stores it
// under kept/; it returns the key, or "" when no copy could be made, which
// only costs playback from the copy.
func (m *Manager) keepCopy(ctx context.Context, job Job, place placement) string {
	m.mu.Lock()
	if current, err := m.store.GetJob(ctx, job.ID); err == nil {
		current.Stage, current.StageAt = "keeping a playback copy", m.cfg.Clock()
		current.HeartbeatAt = current.StageAt
		m.store.UpdateJob(ctx, current)
		m.mu.Unlock()
		m.publish(current, nil)
	} else {
		m.mu.Unlock()
	}
	dst := filepath.Join(filepath.Dir(place.output), "kept.mp4")
	if err := m.cfg.KeepCopy(ctx, place.source, dst); err != nil {
		m.log.Warn("playback copy not made", "job", job.ID, "error", err)
		return ""
	}
	owner := "local"
	if job.UserID != "" {
		owner = job.UserID
	}
	key := "kept/" + owner + "/" + job.SourceID + ".mp4"
	if err := m.putFile(context.WithoutCancel(ctx), key, dst, "video/mp4"); err != nil {
		m.log.Warn("playback copy not stored", "job", job.ID, "error", err)
		return ""
	}
	return key
}

// fetchedMarker is written beside a recording once all of it has arrived.
const fetchedMarker = ".fetched"

// fetch downloads the job's recording into its scratch directory and says
// where the run reads and writes. A resumed job whose scratch directory is
// still there (the workers share it, or the same worker takes it again)
// keeps the recording and the run it already has.
func (m *Manager) fetch(ctx context.Context, job Job) (placement, error) {
	dir := filepath.Join(m.cfg.Scratch, job.ID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return placement{}, err
	}
	kept := placement{source: filepath.Join(dir, "source"+path.Ext(job.SourcePath)), output: filepath.Join(dir, "run"), log: filepath.Join(dir, "worker.log")}
	if _, err := os.Stat(filepath.Join(dir, fetchedMarker)); err == nil {
		if _, err := os.Stat(kept.source); err == nil {
			return kept, nil
		}
	}
	m.mu.Lock()
	if current, err := m.store.GetJob(ctx, job.ID); err == nil {
		current.Stage, current.StageAt = "fetching the recording", m.cfg.Clock()
		current.HeartbeatAt = current.StageAt
		m.store.UpdateJob(ctx, current)
		m.mu.Unlock()
		m.publish(current, nil)
	} else {
		m.mu.Unlock()
	}
	place := kept
	reader, err := m.cfg.Objects.Open(ctx, job.SourcePath)
	if err != nil {
		os.RemoveAll(dir)
		return placement{}, err
	}
	defer reader.Close()
	file, err := os.Create(place.source)
	if err != nil {
		os.RemoveAll(dir)
		return placement{}, err
	}
	_, copyErr := io.Copy(file, reader)
	if err := file.Close(); copyErr == nil {
		copyErr = err
	}
	if copyErr != nil {
		os.RemoveAll(dir)
		return placement{}, copyErr
	}
	if err := os.WriteFile(filepath.Join(dir, fetchedMarker), nil, 0o644); err != nil {
		m.log.Warn("fetched recording not marked", "job", job.ID, "error", err)
	}
	return place, nil
}

// upload stores every regular file below dir under prefix and returns the
// key of each by its path.
func (m *Manager) upload(ctx context.Context, prefix, dir string) (map[string]string, error) {
	keys := map[string]string{}
	err := filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil || !d.Type().IsRegular() {
			return err
		}
		rel, err := filepath.Rel(dir, p)
		if err != nil {
			return err
		}
		key := prefix + "/" + filepath.ToSlash(rel)
		if err := m.putFile(ctx, key, p, contentType(p)); err != nil {
			return fmt.Errorf("%s: %w", rel, err)
		}
		keys[p] = key
		return nil
	})
	return keys, err
}

func (m *Manager) putFile(ctx context.Context, key, p, contentType string) error {
	file, err := os.Open(p)
	if err != nil {
		return err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	return m.cfg.Objects.Put(ctx, key, file, info.Size(), contentType)
}

func contentType(p string) string {
	switch strings.ToLower(filepath.Ext(p)) {
	case ".json":
		return "application/json"
	case ".log", ".txt":
		return "text/plain; charset=utf-8"
	case ".html":
		return "text/html; charset=utf-8"
	}
	if t := mime.TypeByExtension(filepath.Ext(p)); t != "" {
		return t
	}
	return "application/octet-stream"
}
