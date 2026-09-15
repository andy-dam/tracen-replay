package store

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

func open(t *testing.T) (*Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "tracen.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s, path
}

func TestJobsRoundTripAndQueueOrder(t *testing.T) {
	s, _ := open(t)
	ctx := context.Background()
	base := time.Date(2026, 9, 14, 4, 0, 0, 0, time.UTC)
	for i, id := range []string{"job-b", "job-a", "job-c"} {
		j := jobs.Job{ID: id, SourceID: "src", SourcePath: "p.mp4", SourceName: "p.mp4", Status: jobs.Queued, CreatedAt: base.Add(time.Duration(i) * time.Second), OutputDir: "out/" + id}
		if err := s.CreateJob(ctx, j); err != nil {
			t.Fatal(err)
		}
	}
	next, ok, err := s.NextQueued(ctx)
	if err != nil || !ok || next.ID != "job-b" {
		t.Fatalf("next queued = %+v %v %v", next, ok, err)
	}
	n, _ := s.CountByStatus(ctx, jobs.Queued)
	if n != 3 {
		t.Fatalf("queued count %d", n)
	}
	next.Status = jobs.Running
	next.StartedAt = base.Add(10 * time.Second)
	next.PID = 4242
	next.Stage = "ocr"
	next.StageAt = base.Add(11 * time.Second)
	next.OCRProcessed, next.OCRTotal = 100, 9790
	if err := s.UpdateJob(ctx, next); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetJob(ctx, "job-b")
	if err != nil || got.Status != jobs.Running || got.PID != 4242 || got.OCRTotal != 9790 || !got.StartedAt.Equal(next.StartedAt) || got.Stage != "ocr" {
		t.Fatalf("running job round trip: %+v %v", got, err)
	}
	if got.FinishedAt.IsZero() != true || got.Error != nil || got.Result != nil {
		t.Fatalf("unset fields must stay unset: %+v", got)
	}
	got.Status = jobs.CompletedWithStageFailures
	got.FinishedAt = base.Add(time.Hour)
	got.StageFailure = []worker.StageFailure{{Stage: "hint_card_preparation", Error: "ValueError"}}
	got.Result = &worker.Result{SchemaVersion: worker.SchemaVersion, Status: worker.StatusCompletedWithStageFailures, ReportSHA256: "abc"}
	got.ReportID = "rep-1"
	if err := s.UpdateJob(ctx, got); err != nil {
		t.Fatal(err)
	}
	again, _ := s.GetJob(ctx, "job-b")
	if len(again.StageFailure) != 1 || again.Result == nil || again.Result.ReportSHA256 != "abc" || again.ReportID != "rep-1" || !again.FinishedAt.Equal(got.FinishedAt) {
		t.Fatalf("completed job round trip: %+v", again)
	}
	list, _ := s.ListJobs(ctx)
	if len(list) != 3 || list[0].ID != "job-c" || list[2].ID != "job-b" {
		t.Fatalf("list order newest first: %v", []string{list[0].ID, list[1].ID, list[2].ID})
	}
	next, ok, _ = s.NextQueued(ctx)
	if !ok || next.ID != "job-a" {
		t.Fatalf("second queued = %+v", next)
	}
	var nf *jobs.NotFoundError
	if _, err := s.GetJob(ctx, "missing"); !errors.As(err, &nf) {
		t.Fatalf("missing job: %v", err)
	}
	if err := s.UpdateJob(ctx, jobs.Job{ID: "missing"}); !errors.As(err, &nf) {
		t.Fatalf("update missing job: %v", err)
	}
}

func TestMarkInterruptedAndPersistenceAcrossReopen(t *testing.T) {
	s, path := open(t)
	ctx := context.Background()
	now := time.Now()
	for _, j := range []jobs.Job{
		{ID: "running-1", Status: jobs.Running, CreatedAt: now, StartedAt: now, OutputDir: "o1", SourceID: "s", SourcePath: "p", SourceName: "p"},
		{ID: "queued-1", Status: jobs.Queued, CreatedAt: now, OutputDir: "o2", SourceID: "s", SourcePath: "p", SourceName: "p"},
		{ID: "done-1", Status: jobs.Succeeded, CreatedAt: now, FinishedAt: now, OutputDir: "o3", SourceID: "s", SourcePath: "p", SourceName: "p"},
	} {
		if err := s.CreateJob(ctx, j); err != nil {
			t.Fatal(err)
		}
	}
	interrupted, err := s.MarkInterrupted(ctx, now.Add(time.Minute))
	if err != nil || len(interrupted) != 1 || interrupted[0].ID != "running-1" || interrupted[0].Status != jobs.Interrupted {
		t.Fatalf("mark interrupted: %+v %v", interrupted, err)
	}
	s.Close()
	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	got, _ := reopened.GetJob(ctx, "running-1")
	if got.Status != jobs.Interrupted || got.Error == nil || got.Error.Code != "interrupted" {
		t.Fatalf("interrupted job after reopen: %+v", got)
	}
	queued, _ := reopened.CountByStatus(ctx, jobs.Queued)
	if queued != 1 {
		t.Fatalf("queued jobs must survive a restart, got %d", queued)
	}
}

func TestReports(t *testing.T) {
	s, _ := open(t)
	ctx := context.Background()
	r := jobs.Report{ID: "rep-1", JobID: "job-1", Origin: "job", SourceName: "a.mp4", SourceSHA256: "aa", ReportPath: "r.json", ReportSHA256: "bb",
		TimelinePath: "t.json", EvidenceRoot: "root", CreatedAt: time.Now(), DurationMS: 1000, Turns: 3, Entries: 9}
	if err := s.CreateReport(ctx, r); err != nil {
		t.Fatal(err)
	}
	if err := s.CreateReport(ctx, jobs.Report{ID: "rep-0", Origin: "imported", SourceName: "b.mp4", SourceSHA256: "cc", ReportPath: "r2.json", ReportSHA256: "dd", TimelinePath: "t2.json", EvidenceRoot: "root2", CreatedAt: time.Now().Add(-time.Hour)}); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetReport(ctx, "rep-1")
	if err != nil || got.JobID != "job-1" || got.Turns != 3 || got.Entries != 9 || got.DurationMS != 1000 {
		t.Fatalf("report round trip: %+v %v", got, err)
	}
	list, _ := s.ListReports(ctx)
	if len(list) != 2 || list[0].ID != "rep-1" || list[1].JobID != "" {
		t.Fatalf("report list: %+v", list)
	}
	var nf *jobs.NotFoundError
	if _, err := s.GetReport(ctx, "nope"); !errors.As(err, &nf) {
		t.Fatalf("missing report: %v", err)
	}
	if err := s.CreateReport(ctx, r); err == nil {
		t.Fatal("duplicate report id must be rejected")
	}
}
