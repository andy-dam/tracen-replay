package api

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/timeline"
)

type fakeCorrections struct {
	rows map[string]timeline.Correction
}

func key(reportID, turnID, userID string) string { return reportID + "|" + turnID + "|" + userID }

func (f *fakeCorrections) GetCorrection(ctx context.Context, reportID, turnID, userID string) (timeline.Correction, bool, error) {
	c, ok := f.rows[key(reportID, turnID, userID)]
	return c, ok, nil
}

func (f *fakeCorrections) PutCorrection(ctx context.Context, c timeline.Correction) error {
	f.rows[key(c.ReportID, c.TurnID, c.UserID)] = c
	return nil
}

func (f *fakeCorrections) DeleteCorrection(ctx context.Context, reportID, turnID, userID string) error {
	delete(f.rows, key(reportID, turnID, userID))
	return nil
}

func (f *fakeCorrections) ListCorrections(ctx context.Context, reportID, userID string) ([]timeline.Correction, error) {
	var out []timeline.Correction
	for _, c := range f.rows {
		if c.ReportID == reportID && c.UserID == userID {
			out = append(out, c)
		}
	}
	return out, nil
}

func newCorrectionServer(t *testing.T) *Server {
	t.Helper()
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	report := fixtureReport(t)
	return New(Config{Jobs: fj, Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}},
		Corrections: &fakeCorrections{rows: map[string]timeline.Correction{}},
		Ready:       func() []Check { return []Check{{Name: "python", OK: true}} }})
}

func TestCorrectionIsStoredAndCheckedAgainstTheNextObservedValues(t *testing.T) {
	srv := newCorrectionServer(t)
	// turn-002 of the mini fixture: power 187 -> 200 with 6 explained, 7 unexplained.
	status, body := do(t, srv, http.MethodPut, "/api/reports/rep-1/turns/turn-002/correction",
		`{"changes":[{"field":"power","amount":7,"note":"event receipt the recognizer dropped"}]}`)
	if status != http.StatusOK {
		t.Fatalf("put: %d %v", status, body)
	}
	verification := body["verification"].(map[string]any)
	if verification["verified"] != true || !strings.HasPrefix(verification["summary"].(string), "Adds up") {
		t.Fatalf("expected a verified correction, got %v", verification)
	}
	status, body = do(t, srv, http.MethodGet, "/api/reports/rep-1/turns/turn-002", "")
	if status != http.StatusOK || body["correction"] == nil || body["verification"].(map[string]any)["verified"] != true {
		t.Fatalf("turn detail should carry the correction: %d %v", status, body)
	}
	status, body = do(t, srv, http.MethodPut, "/api/reports/rep-1/turns/turn-002/correction", `{"changes":[{"field":"power","amount":5}]}`)
	if status != http.StatusOK || body["verification"].(map[string]any)["verified"] != false ||
		!strings.Contains(body["verification"].(map[string]any)["summary"].(string), "power by +2") {
		t.Fatalf("an amount that does not add up must say by how much: %d %v", status, body)
	}
	status, body = do(t, srv, http.MethodGet, "/api/reports/rep-1/corrections", "")
	if status != http.StatusOK || len(body["corrections"].([]any)) != 1 {
		t.Fatalf("list: %d %v", status, body)
	}
	status, body = do(t, srv, http.MethodPut, "/api/reports/rep-1/turns/turn-002/correction", `{"changes":[{"field":"luck","amount":5}]}`)
	if status != http.StatusBadRequest {
		t.Fatalf("an unknown field must be rejected: %d %v", status, body)
	}
	status, _ = do(t, srv, http.MethodDelete, "/api/reports/rep-1/turns/turn-002/correction", "")
	if status != http.StatusNoContent {
		t.Fatalf("delete: %d", status)
	}
	status, body = do(t, srv, http.MethodGet, "/api/reports/rep-1/turns/turn-002/correction", "")
	if status != http.StatusOK || body["correction"] != nil {
		t.Fatalf("after delete: %d %v", status, body)
	}
	status, body = do(t, srv, http.MethodPut, "/api/reports/rep-1/turns/turn-001/correction",
		`{"action":{"kind":"training","training_option":"speed","gains":{"speed":13}}}`)
	if status != http.StatusOK || body["verification"].(map[string]any)["verified"] != false ||
		!strings.HasPrefix(body["verification"].(map[string]any)["summary"].(string), "Cannot verify") {
		t.Fatalf("a turn without an observed opening cannot verify: %d %v", status, body)
	}
}

func TestCorrectionsAreOffWhenNoStoreIsConfigured(t *testing.T) {
	srv, _ := newServer(t)
	status, _ := do(t, srv, http.MethodGet, "/api/reports/rep-1/turns/turn-002/correction", "")
	if status != http.StatusNotFound {
		t.Fatalf("expected 404 without a store, got %d", status)
	}
}
