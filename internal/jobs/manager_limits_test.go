package jobs_test

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// userUploads serves "src-<user>" as that user's upload of one clip.
type userUploads struct{ path string }

func (u userUploads) GetRecording(ctx context.Context, id string) (jobs.Recording, error) {
	user, ok := strings.CutPrefix(id, "src-")
	if !ok {
		return jobs.Recording{}, &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	return jobs.Recording{ID: id, UserID: user, Name: filepath.Base(u.path), Path: u.path, Size: 4}, nil
}

// One user holds one place in the queue at a time: a second submission
// while the first is queued or running is refused, and free again once it
// has finished.
func TestOneActiveAnalysisPerUser(t *testing.T) {
	h := newHarness(t, 4, func(c *jobs.Config) { c.MaxActivePerUser = 1 })
	release := make(chan struct{})
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		select {
		case <-release:
		case <-ctx.Done():
		}
		return 0, writeArtifacts(t, cmd.Output, worker.StatusSucceeded), nil
	}
	h.start()
	first, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	<-h.runner.started
	h.waitStatus(first.ID, jobs.Running)
	var limit *jobs.LimitError
	if _, err := h.manager.Submit(context.Background(), "u1", "src-1"); !errors.As(err, &limit) || limit.Code != "too_many_jobs" {
		t.Fatalf("a second active analysis must be refused, got %v", err)
	}
	close(release)
	h.waitStatus(first.ID, jobs.Succeeded)
	if _, err := h.manager.Submit(context.Background(), "u1", "src-1"); err != nil {
		t.Fatalf("after the first finished the user may submit again: %v", err)
	}
}

// The day's budget counts analyses started in the last 24 hours, per user
// and for everyone; a job cancelled before it started did not spend it.
func TestDailyBudgets(t *testing.T) {
	now := time.Date(2026, 9, 20, 9, 0, 0, 0, time.UTC)
	h := newHarness(t, 8, func(c *jobs.Config) {
		c.DailyPerUser, c.DailyTotal = 2, 3
		c.Clock = func() time.Time { return now }
		// Every user owns an upload named after them.
		c.Recordings = userUploads{path: c.Recordings.(oneUpload).path}
	})
	// Not started: every job stays queued, which is what the budget counts.
	submit := func(user string) error {
		_, err := h.manager.Submit(context.Background(), user, "src-"+user)
		return err
	}
	for i := 0; i < 2; i++ {
		if err := submit("u1"); err != nil {
			t.Fatalf("submission %d: %v", i+1, err)
		}
	}
	var limit *jobs.LimitError
	if err := submit("u1"); !errors.As(err, &limit) || limit.Code != "daily_limit" || limit.Limit != 2 {
		t.Fatalf("the third of the day must be refused, got %v", err)
	}
	// A queued job the user cancels gives the budget back.
	mine, _ := h.manager.List(context.Background(), "u1")
	if _, err := h.manager.Cancel(context.Background(), mine[0].ID); err != nil {
		t.Fatal(err)
	}
	if err := submit("u1"); err != nil {
		t.Fatalf("after a cancel the user is under budget again: %v", err)
	}
	// Everyone together: two of u1's count, one more fills the day for all.
	if err := submit("u2"); err != nil {
		t.Fatalf("another user's first: %v", err)
	}
	if err := submit("u3"); !errors.As(err, &limit) || limit.Code != "daily_limit" || limit.Limit != 3 {
		t.Fatalf("the service's day is full, got %v", err)
	}
	// A day later the budget is fresh.
	now = now.Add(24*time.Hour + time.Minute)
	if err := submit("u3"); err != nil {
		t.Fatalf("the next day: %v", err)
	}
}

// An analysis that outruns its bound is stopped and fails as timed_out,
// which is not a cancellation and not a service restart.
func TestAnAnalysisPastItsBoundTimesOut(t *testing.T) {
	h := newHarness(t, 2, func(c *jobs.Config) { c.MaxDuration = 100 * time.Millisecond })
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		<-ctx.Done()
		return -1, nil, ctx.Err()
	}
	h.start()
	job, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	done := h.waitStatus(job.ID, jobs.Failed)
	if done.Error == nil || done.Error.Code != "timed_out" {
		t.Fatalf("timed out job: %+v", done.Error)
	}
}

// The month's budget counts every analysis started in the last 30 days,
// whoever started it, over and above the day's.
func TestMonthlyBudget(t *testing.T) {
	now := time.Date(2026, 9, 20, 9, 0, 0, 0, time.UTC)
	h := newHarness(t, 8, func(c *jobs.Config) {
		c.DailyTotal, c.MonthlyTotal = 10, 3
		c.Clock = func() time.Time { return now }
		c.Recordings = userUploads{path: c.Recordings.(oneUpload).path}
	})
	submit := func(user string) error {
		_, err := h.manager.Submit(context.Background(), user, "src-"+user)
		return err
	}
	for i, user := range []string{"u1", "u2", "u3"} {
		if err := submit(user); err != nil {
			t.Fatalf("submission %d: %v", i+1, err)
		}
		now = now.Add(24 * time.Hour)
	}
	var limit *jobs.LimitError
	if err := submit("u4"); !errors.As(err, &limit) || limit.Code != "monthly_limit" || limit.Limit != 3 {
		t.Fatalf("the month is full, got %v", err)
	}
	now = now.Add(28 * 24 * time.Hour)
	if err := submit("u4"); err != nil {
		t.Fatalf("a month on the first has aged out: %v", err)
	}
}
