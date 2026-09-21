package store

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
)

func TestUsersSessionsAndRecordingsRoundTrip(t *testing.T) {
	s, _ := open(t)
	ctx := context.Background()
	now := time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC)
	andy := auth.User{ID: "u1", Email: "andy@example.com", DisplayName: "Andy", CreatedAt: now}
	if err := s.CreateUser(ctx, andy, "hash-1"); err != nil {
		t.Fatal(err)
	}
	if err := s.CreateUser(ctx, auth.User{ID: "u2", Email: "andy@example.com", DisplayName: "Dup", CreatedAt: now}, "h"); err == nil {
		t.Fatal("a duplicate email must be rejected by the database")
	}
	got, hash, err := s.UserByEmail(ctx, "andy@example.com")
	if err != nil || hash != "hash-1" || got.ID != "u1" || !got.CreatedAt.Equal(now) {
		t.Fatalf("UserByEmail: %+v %q %v", got, hash, err)
	}
	if _, _, err := s.UserByEmail(ctx, "nobody@example.com"); !errors.Is(err, auth.ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
	if err := s.CreateSession(ctx, "tok-hash", "u1", now, now.Add(time.Hour)); err != nil {
		t.Fatal(err)
	}
	if u, err := s.SessionUser(ctx, "tok-hash", now.Add(30*time.Minute)); err != nil || u.ID != "u1" {
		t.Fatalf("SessionUser: %+v %v", u, err)
	}
	if _, err := s.SessionUser(ctx, "tok-hash", now.Add(2*time.Hour)); !errors.Is(err, auth.ErrNotFound) {
		t.Fatalf("an expired session must not resolve, got %v", err)
	}
	if _, err := s.SessionUser(ctx, "tok-hash", now); !errors.Is(err, auth.ErrNotFound) {
		t.Fatal("an expired session is removed when seen")
	}
	if err := s.DeleteSession(ctx, "tok-hash"); !errors.Is(err, auth.ErrNotFound) {
		t.Fatalf("deleting a removed session reports not found, got %v", err)
	}

	rec := jobs.Recording{ID: "r1", UserID: "u1", Name: "run.mp4", Path: "/data/recordings/u1/r1.mp4", Size: 42, SHA256: "abc", CreatedAt: now}
	if err := s.CreateRecording(ctx, rec); err != nil {
		t.Fatal(err)
	}
	if err := s.CreateRecording(ctx, jobs.Recording{ID: "r2", UserID: "ghost", Name: "x.mp4", Path: "p", Size: 1, SHA256: "d", CreatedAt: now}); err == nil {
		t.Fatal("a recording must belong to an existing user")
	}
	list, err := s.ListRecordingsForUser(ctx, "u1")
	if err != nil || len(list) != 1 || list[0].Path != rec.Path {
		t.Fatalf("ListRecordingsForUser: %+v %v", list, err)
	}
	if inUse, _ := s.RecordingInUse(ctx, "r1"); inUse {
		t.Fatal("no job uses the recording yet")
	}
	job := jobs.Job{ID: "j1", UserID: "u1", SourceID: "r1", SourcePath: rec.Path, SourceName: rec.Name, Status: jobs.Queued, CreatedAt: now, OutputDir: "o"}
	if err := s.CreateJob(ctx, job); err != nil {
		t.Fatal(err)
	}
	if inUse, _ := s.RecordingInUse(ctx, "r1"); !inUse {
		t.Fatal("a queued job uses the recording")
	}
	mine, _ := s.ListJobsForUser(ctx, "u1")
	theirs, _ := s.ListJobsForUser(ctx, "u9")
	if len(mine) != 1 || mine[0].UserID != "u1" || len(theirs) != 0 {
		t.Fatalf("job visibility: mine %d theirs %d", len(mine), len(theirs))
	}
	unowned := jobs.Report{ID: "imp", Origin: "imported", SourceName: "a.mp4", SourceSHA256: "s", ReportPath: "r", ReportSHA256: "h", TimelinePath: "t", EvidenceRoot: "e", CreatedAt: now}
	owned := jobs.Report{ID: "job-j1", UserID: "u1", JobID: "j1", Origin: "job", SourceName: "run.mp4", SourceSHA256: "s", ReportPath: "r", ReportSHA256: "h", TimelinePath: "t", EvidenceRoot: "e", CreatedAt: now.Add(time.Second)}
	for _, r := range []jobs.Report{unowned, owned} {
		if err := s.CreateReport(ctx, r); err != nil {
			t.Fatal(err)
		}
	}
	mineReports, _ := s.ListReportsForUser(ctx, "u1")
	otherReports, _ := s.ListReportsForUser(ctx, "u9")
	if len(mineReports) != 2 || len(otherReports) != 1 || otherReports[0].ID != "imp" {
		t.Fatalf("report visibility: mine %d other %d", len(mineReports), len(otherReports))
	}
	if err := s.DeleteRecording(ctx, "r1"); err != nil {
		t.Fatal(err)
	}
	if err := s.DeleteRecording(ctx, "r1"); err == nil {
		t.Fatal("deleting twice must report not found")
	}
}

// UpdateRecording changes the hash and the kept copy and nothing else.
func TestUpdateRecordingTakesBackHashAndKeptCopy(t *testing.T) {
	s, _ := open(t)
	ctx := context.Background()
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	if err := s.CreateUser(ctx, auth.User{ID: "u1", Email: "andy@example.com", DisplayName: "Andy", CreatedAt: now}, "hash"); err != nil {
		t.Fatal(err)
	}
	r := jobs.Recording{ID: "r1", UserID: "u1", Name: "run.mp4", Path: "originals/u1/r1.mp4", Size: 42, CreatedAt: now}
	if err := s.CreateRecording(ctx, r); err != nil {
		t.Fatal(err)
	}
	if got, _ := s.GetRecording(ctx, "r1"); got.KeptPath != "" || got.SHA256 != "" {
		t.Fatalf("fresh: %+v", got)
	}
	r.SHA256, r.KeptPath, r.Name = "abc", "kept/u1/r1.mp4", "renamed"
	if err := s.UpdateRecording(ctx, r); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetRecording(ctx, "r1")
	if err != nil || got.SHA256 != "abc" || got.KeptPath != "kept/u1/r1.mp4" || got.Name != "run.mp4" {
		t.Fatalf("updated: %+v %v", got, err)
	}
	if err := s.UpdateRecording(ctx, jobs.Recording{ID: "nope"}); err == nil {
		t.Fatal("updating an unknown recording must fail")
	}
}

// Deleting a user deletes every record kept under it and leaves another
// user's records alone.
func TestDeleteUserDeletesEverythingKeptUnderIt(t *testing.T) {
	s, _ := open(t)
	ctx := context.Background()
	now := time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC)
	for _, id := range []string{"u1", "u2"} {
		if err := s.CreateUser(ctx, auth.User{ID: id, Email: id + "@example.com", DisplayName: id, CreatedAt: now}, "hash"); err != nil {
			t.Fatal(err)
		}
		if err := s.CreateSession(ctx, "tok-"+id, id, now, now.Add(time.Hour)); err != nil {
			t.Fatal(err)
		}
		if err := s.CreateRecording(ctx, jobs.Recording{ID: "rec-" + id, UserID: id, Name: "a.mp4", Path: "a", Size: 1, SHA256: "x", CreatedAt: now}); err != nil {
			t.Fatal(err)
		}
		if err := s.CreateJob(ctx, jobs.Job{ID: "job-" + id, UserID: id, SourceID: "rec-" + id, Status: jobs.Succeeded, CreatedAt: now, OutputDir: "o"}); err != nil {
			t.Fatal(err)
		}
		if err := s.CreateReport(ctx, jobs.Report{ID: "rep-" + id, UserID: id, JobID: "job-" + id, Origin: "job", SourceName: "a", CreatedAt: now}); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.DeleteUser(ctx, "u1"); err != nil {
		t.Fatal(err)
	}
	if err := s.DeleteUser(ctx, "u1"); !errors.Is(err, auth.ErrNotFound) {
		t.Fatalf("a second delete: %v", err)
	}
	if _, err := s.UserByID(ctx, "u1"); !errors.Is(err, auth.ErrNotFound) {
		t.Fatalf("the user is still there: %v", err)
	}
	if _, err := s.SessionUser(ctx, "tok-u1", now); err == nil {
		t.Fatal("the session is still there")
	}
	if _, err := s.GetRecording(ctx, "rec-u1"); err == nil {
		t.Fatal("the recording is still there")
	}
	if _, err := s.GetJob(ctx, "job-u1"); err == nil {
		t.Fatal("the job is still there")
	}
	if _, err := s.GetReport(ctx, "rep-u1"); err == nil {
		t.Fatal("the report is still there")
	}
	for name, err := range map[string]error{"user": second(s.UserByID(ctx, "u2")), "recording": second(s.GetRecording(ctx, "rec-u2")),
		"job": second(s.GetJob(ctx, "job-u2")), "report": second(s.GetReport(ctx, "rep-u2"))} {
		if err != nil {
			t.Fatalf("the other user's %s went too: %v", name, err)
		}
	}
}

func second[T any](_ T, err error) error { return err }
