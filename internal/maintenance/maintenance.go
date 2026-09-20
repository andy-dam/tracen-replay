// Package maintenance keeps the data directory from growing without bound:
// uploads past their retention that no analysis is using are deleted (their
// reports stay), expired sessions are dropped, and the frame cache is pruned
// to its budget. Nothing here touches a report or a job directory.
package maintenance

import (
	"context"
	"log/slog"
	"os"
	"time"

	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/objectstore"
)

// Store is what the sweeper needs from the database.
type Store interface {
	ListRecordingsOlderThan(ctx context.Context, before time.Time) ([]jobs.Recording, error)
	RecordingInUse(ctx context.Context, id string) (bool, error)
	DeleteRecording(ctx context.Context, id string) error
	DeleteExpiredSessions(ctx context.Context, now time.Time) (int64, error)
}

// Sweeper runs the housekeeping.
type Sweeper struct {
	Store Store
	// RecordingsDir confines the files it may delete; with Objects the
	// recordings are objects named by key instead.
	RecordingsDir string
	Objects       objectstore.Store
	// RecordingRetention is how long an upload is kept; zero keeps uploads
	// forever.
	RecordingRetention time.Duration
	// Frames is the cache to prune (its MaxCacheBytes); a zero value prunes
	// nothing.
	Frames artifacts.Frames
	// Every is the sweep interval; zero means an hour.
	Every time.Duration
	// Clock is replaceable for tests.
	Clock  func() time.Time
	Logger *slog.Logger
}

// Summary says what one sweep did.
type Summary struct {
	RecordingsDeleted int
	BytesFreed        int64
	SessionsDeleted   int64
	FrameBytesPruned  int64
	// OrphansDeleted counts uploads in the object store that no record
	// names: direct uploads that were never completed.
	OrphansDeleted int
}

// orphanAge is how old an upload nobody completed must be before it goes:
// longer than an upload URL stays valid.
const orphanAge = 24 * time.Hour

// sweepOrphans deletes objects under originals/ that no recording names
// and that are older than orphanAge.
func (s Sweeper) sweepOrphans(ctx context.Context, now time.Time) (int, error) {
	objects, err := s.Objects.List(ctx, "originals/")
	if err != nil {
		return 0, err
	}
	recordings, err := s.Store.ListRecordingsOlderThan(ctx, now.Add(24*time.Hour))
	if err != nil {
		return 0, err
	}
	named := map[string]bool{}
	for _, recording := range recordings {
		named[recording.Path] = true
	}
	deleted := 0
	for _, object := range objects {
		if named[object.Key] || now.Sub(object.ModTime) < orphanAge {
			continue
		}
		if err := s.Objects.Delete(ctx, object.Key); err != nil {
			s.log().Warn("orphaned upload not removed", "key", object.Key, "error", err)
			continue
		}
		deleted++
	}
	return deleted, nil
}

// Run sweeps at the interval until ctx ends.
func (s Sweeper) Run(ctx context.Context) {
	every := s.Every
	if every <= 0 {
		every = time.Hour
	}
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if summary, err := s.Sweep(ctx); err != nil {
				s.log().Warn("housekeeping sweep failed", "error", err)
			} else if summary != (Summary{}) {
				s.log().Info("housekeeping", "recordings_deleted", summary.RecordingsDeleted, "bytes_freed", summary.BytesFreed,
					"orphans_deleted", summary.OrphansDeleted, "sessions_deleted", summary.SessionsDeleted, "frame_bytes_pruned", summary.FrameBytesPruned)
			}
		}
	}
}

// Sweep does one round of housekeeping.
func (s Sweeper) Sweep(ctx context.Context) (Summary, error) {
	var summary Summary
	now := s.now()
	if s.RecordingRetention > 0 {
		old, err := s.Store.ListRecordingsOlderThan(ctx, now.Add(-s.RecordingRetention))
		if err != nil {
			return summary, err
		}
		for _, recording := range old {
			inUse, err := s.Store.RecordingInUse(ctx, recording.ID)
			if err != nil || inUse {
				continue
			}
			// The file goes only when it lies under the uploads directory,
			// and the row goes with it; a file already gone still frees its row.
			if s.Objects != nil {
				if err := s.Objects.Delete(ctx, recording.Path); err != nil {
					s.log().Warn("expired upload not removed", "recording", recording.ID, "error", err)
					continue
				}
				if recording.KeptPath != "" {
					s.Objects.Delete(ctx, recording.KeptPath)
				}
			} else if path, err := artifacts.Confined(s.RecordingsDir, recording.Path); err == nil {
				if err := os.Remove(path); err != nil {
					s.log().Warn("expired upload not removed", "recording", recording.ID, "error", err)
					continue
				}
			}
			if err := s.Store.DeleteRecording(ctx, recording.ID); err != nil {
				s.log().Warn("expired upload row not removed", "recording", recording.ID, "error", err)
				continue
			}
			summary.RecordingsDeleted++
			summary.BytesFreed += recording.Size
		}
	}
	if s.Objects != nil {
		orphans, err := s.sweepOrphans(ctx, now)
		if err != nil {
			return summary, err
		}
		summary.OrphansDeleted = orphans
	}
	sessions, err := s.Store.DeleteExpiredSessions(ctx, now)
	if err != nil {
		return summary, err
	}
	summary.SessionsDeleted = sessions
	pruned, err := s.Frames.Prune()
	if err != nil {
		return summary, err
	}
	summary.FrameBytesPruned = pruned
	return summary, nil
}

func (s Sweeper) now() time.Time {
	if s.Clock != nil {
		return s.Clock()
	}
	return time.Now()
}

func (s Sweeper) log() *slog.Logger {
	if s.Logger != nil {
		return s.Logger
	}
	return slog.Default()
}
