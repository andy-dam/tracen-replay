package jobs_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// An analysis on a Mac ran for hours, wrote its report, and failed because
// a native library's log line was all the service could read of its result.
// The report on disk is the result; Rescue takes it up without a new run.
func TestRescueRecoversAFinishedAnalysisWhoseResultWasLost(t *testing.T) {
	h := newHarness(t, 4)
	source := hex.EncodeToString(sha256.New().Sum([]byte("clip")))[:64]
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		writeArtifacts(t, cmd.Output, worker.StatusSucceeded)
		// The report names its recording, as a real one does.
		report := fmt.Sprintf(`{"schema_version":"tracen-replay/full-recording-v1","source":{"sha256":%q},"verification":{"full_source_processed":true}}`, source)
		if err := os.WriteFile(filepath.Join(cmd.Output, "report.json"), []byte(report), 0o644); err != nil {
			t.Fatal(err)
		}
		return 0, []byte("E5RT encountered an unknown exception\n"), nil
	}
	h.start()
	job, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	failed := h.waitStatus(job.ID, jobs.Failed)
	if failed.Error == nil || failed.Error.Code != worker.CodeBadTerminalOutput || !jobs.Rescuable(failed) {
		t.Fatalf("the lost result: %+v", failed.Error)
	}

	rescued, err := h.manager.Rescue(context.Background(), job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if rescued.Status != jobs.Succeeded || rescued.Error != nil || rescued.ReportID == "" || rescued.Result == nil || !rescued.Result.FullSourceProcessed {
		t.Fatalf("the rescued job: %+v", rescued)
	}
	report, err := h.store.GetReport(context.Background(), rescued.ReportID)
	if err != nil || report.JobID != job.ID || report.Turns != 1 || report.SourceSHA256 != source {
		t.Fatalf("the rescued report: %+v %v", report, err)
	}
	// Once recovered there is nothing left to recover, and a job that
	// failed for another reason never was.
	if _, err := h.manager.Rescue(context.Background(), job.ID); !errors.Is(err, jobs.ErrNotRescuable) {
		t.Fatalf("a second rescue: %v", err)
	}
	if n := h.manager.RescueAll(context.Background(), "u1"); n != 0 {
		t.Fatalf("RescueAll found %d more", n)
	}
}

func TestRescueRefusesOutputThatDoesNotVerify(t *testing.T) {
	h := newHarness(t, 4)
	h.runner.script = func(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
		// A run that died before it wrote a report.
		os.MkdirAll(cmd.Output, 0o755)
		return 0, []byte("E5RT encountered an unknown exception\n"), nil
	}
	h.start()
	job, err := h.manager.Submit(context.Background(), "u1", "src-1")
	if err != nil {
		t.Fatal(err)
	}
	h.waitStatus(job.ID, jobs.Failed)
	kept, err := h.manager.Rescue(context.Background(), job.ID)
	if !errors.Is(err, jobs.ErrNotRescuable) || kept.Status != jobs.Failed || kept.Error == nil || kept.Error.Code != worker.CodeBadTerminalOutput {
		t.Fatalf("a run without a report: %v %+v", err, kept)
	}
	if n := h.manager.RescueAll(context.Background(), "u1"); n != 0 {
		t.Fatalf("RescueAll recovered %d", n)
	}
}
