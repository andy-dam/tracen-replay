package api

import (
	"context"
	"net/http"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

const (
	madeBy    = "1111111111111111111111111111111111111111111111111111111111111111"
	installed = "2222222222222222222222222222222222222222222222222222222222222222"
)

// ledgerServer serves one report made by a job whose analyzer identity is
// digest, beside an installed analyzer reported by analyzer.
func ledgerServer(t *testing.T, digest string, analyzer func(context.Context) (worker.WorkerVersion, error)) *Server {
	t.Helper()
	report := fixtureReport(t)
	report.Origin, report.JobID = "job", "job-1"
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	job := jobs.Job{ID: "job-1", Status: jobs.Succeeded}
	if digest != "" {
		job.Result = &worker.Result{WorkerVersion: &worker.WorkerVersion{Package: "0.1.0", CodeDigest: digest}}
	}
	fj.jobs["job-1"] = job
	return New(Config{Jobs: fj, Analyzer: analyzer,
		Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}}})
}

func constant(digest string) func(context.Context) (worker.WorkerVersion, error) {
	return func(context.Context) (worker.WorkerVersion, error) {
		return worker.WorkerVersion{Package: "0.1.0", CodeDigest: digest}, nil
	}
}

func ledger(t *testing.T, srv *Server) map[string]any {
	t.Helper()
	code, body := do(t, srv, "GET", "/api/reports/rep-1/summary", "")
	if code != http.StatusOK {
		t.Fatalf("summary: %d", code)
	}
	value, _ := body["analyzer"].(map[string]any)
	return value
}

func TestSummarySaysWhenTheAnalyzerHasMovedOn(t *testing.T) {
	got := ledger(t, ledgerServer(t, madeBy, constant(installed)))
	if got["stale"] != true {
		t.Fatalf("a report made by another analyzer must read as stale: %v", got)
	}
	made, _ := got["report"].(map[string]any)
	if made["code_digest"] != madeBy {
		t.Fatalf("report identity = %v", made)
	}
	have, _ := got["installed"].(map[string]any)
	if have["code_digest"] != installed {
		t.Fatalf("installed identity = %v", have)
	}
}

func TestSummarySaysWhenTheReportIsStillCurrent(t *testing.T) {
	got := ledger(t, ledgerServer(t, madeBy, constant(madeBy)))
	if got["stale"] != false {
		t.Fatalf("the same analyzer must not read as stale: %v", got)
	}
}

func TestSummaryClaimsNothingItCannotKnow(t *testing.T) {
	// An unreachable analyzer must not make a report look current or stale.
	unreachable := func(context.Context) (worker.WorkerVersion, error) {
		return worker.WorkerVersion{}, context.DeadlineExceeded
	}
	got := ledger(t, ledgerServer(t, madeBy, unreachable))
	if _, said := got["stale"]; said {
		t.Fatalf("staleness was claimed without an installed analyzer: %v", got)
	}
	if _, said := got["installed"]; said {
		t.Fatalf("an unreachable analyzer was reported anyway: %v", got)
	}

	// A job that recorded no identity leaves the report half unstated.
	got = ledger(t, ledgerServer(t, "", constant(installed)))
	if _, said := got["report"]; said {
		t.Fatalf("an identity was invented for the report: %v", got)
	}
	if _, said := got["stale"]; said {
		t.Fatalf("staleness was claimed without the report's identity: %v", got)
	}

	// With neither half there is nothing to say, and nothing is said.
	if got := ledger(t, ledgerServer(t, "", nil)); got != nil {
		t.Fatalf("expected no ledger at all, got %v", got)
	}
	// A report that never came from a job has no ledger either.
	if got := ledger(t, newServerWithAnalyzer(t, constant(installed))); got["report"] != nil {
		t.Fatalf("an imported report claimed an analyzer: %v", got)
	}
}

func newServerWithAnalyzer(t *testing.T, analyzer func(context.Context) (worker.WorkerVersion, error)) *Server {
	t.Helper()
	report := fixtureReport(t)
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	return New(Config{Jobs: fj, Analyzer: analyzer,
		Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}}})
}
