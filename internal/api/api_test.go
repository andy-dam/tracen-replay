package api

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
)

// fakeJobs is an in-memory stand-in for the manager.
type fakeJobs struct {
	mu   sync.Mutex
	jobs map[string]jobs.Job
	hub  *jobs.Hub
	full bool
}

func (f *fakeJobs) Submit(ctx context.Context, userID, sourceID string) (jobs.Job, error) {
	if f.full {
		return jobs.Job{}, &jobs.QueueFullError{Limit: 1}
	}
	if sourceID != "src-1" {
		return jobs.Job{}, &jobs.NotFoundError{Kind: "source", ID: sourceID}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	job := jobs.Job{ID: "job-1", SourceID: sourceID, Status: jobs.Queued, CreatedAt: time.Now(), OutputDir: "o"}
	f.jobs[job.ID] = job
	return job, nil
}

func (f *fakeJobs) Cancel(ctx context.Context, id string) (jobs.Job, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.jobs[id]
	if !ok {
		return jobs.Job{}, &jobs.NotFoundError{Kind: "job", ID: id}
	}
	if job.Status.Terminal() {
		return job, &jobs.TransitionError{ID: id, Status: job.Status, Action: "cancel"}
	}
	job.Status = jobs.Cancelled
	f.jobs[id] = job
	return job, nil
}

func (f *fakeJobs) Get(ctx context.Context, id string) (jobs.Job, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.jobs[id]
	if !ok {
		return jobs.Job{}, &jobs.NotFoundError{Kind: "job", ID: id}
	}
	return job, nil
}

func (f *fakeJobs) List(ctx context.Context, userID string) ([]jobs.Job, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []jobs.Job
	for _, j := range f.jobs {
		out = append(out, j)
	}
	return out, nil
}

func (f *fakeJobs) Hub() *jobs.Hub { return f.hub }

type fakeReports struct{ reports map[string]jobs.Report }

func (f fakeReports) ListReportsForUser(ctx context.Context, userID string) ([]jobs.Report, error) {
	var out []jobs.Report
	for _, r := range f.reports {
		out = append(out, r)
	}
	return out, nil
}

func (f fakeReports) GetReport(ctx context.Context, id string) (jobs.Report, error) {
	r, ok := f.reports[id]
	if !ok {
		return jobs.Report{}, &jobs.NotFoundError{Kind: "report", ID: id}
	}
	return r, nil
}

func (f fakeReports) DeleteReport(ctx context.Context, id string) error {
	if _, ok := f.reports[id]; !ok {
		return &jobs.NotFoundError{Kind: "report", ID: id}
	}
	delete(f.reports, id)
	return nil
}

func fixtureReport(t *testing.T) jobs.Report {
	t.Helper()
	root := t.TempDir()
	timelinePath := filepath.Join(root, "timeline.json")
	data, err := os.ReadFile(filepath.Join("..", "..", "testdata", "timeline", "mini.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(timelinePath, data, 0o644); err != nil {
		t.Fatal(err)
	}
	reportPath := filepath.Join(root, "report.json")
	if err := os.WriteFile(reportPath, []byte(`{"schema_version":"tracen-replay/full-recording-v1"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	return jobs.Report{ID: "rep-1", Origin: "imported", SourceName: "mini.mp4", ReportPath: reportPath, ReportSHA256: "abc", TimelinePath: timelinePath, EvidenceRoot: root, Turns: 2, Entries: 4}
}

func newServer(t *testing.T) (*Server, *fakeJobs) {
	t.Helper()
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	report := fixtureReport(t)
	srv := New(Config{Jobs: fj, Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}},
		Ready: func() []Check { return []Check{{Name: "python", OK: true}} }})
	return srv, fj
}

func do(t *testing.T, srv http.Handler, method, path, body string) (int, map[string]any) {
	t.Helper()
	req := httptest.NewRequest(method, "http://127.0.0.1:8765"+path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	var decoded map[string]any
	if strings.HasPrefix(rec.Header().Get("Content-Type"), "application/json") {
		json.Unmarshal(rec.Body.Bytes(), &decoded)
	}
	return rec.Code, decoded
}

// A client served from an allowed origin gets its preflight answered and its
// calls accepted with credentials; any other origin is still refused.
func TestCrossOriginClient(t *testing.T) {
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	report := fixtureReport(t)
	srv := New(Config{Jobs: fj, Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}}, AllowedOrigins: []string{"http://localhost:5173"}})
	req := httptest.NewRequest("OPTIONS", "http://localhost:8765/api/jobs", nil)
	req.Header.Set("Origin", "http://localhost:5173")
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusNoContent || rec.Header().Get("Access-Control-Allow-Origin") != "http://localhost:5173" || rec.Header().Get("Access-Control-Allow-Credentials") != "true" {
		t.Fatalf("preflight: %d %v", rec.Code, rec.Header())
	}
	req = httptest.NewRequest("POST", "http://localhost:8765/api/jobs", strings.NewReader(`{"source_id":"nope"}`))
	req.Header.Set("Origin", "http://localhost:5173")
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code == http.StatusForbidden || rec.Header().Get("Access-Control-Allow-Origin") != "http://localhost:5173" {
		t.Fatalf("allowed origin must pass the origin check, got %d %v", rec.Code, rec.Header())
	}
	req = httptest.NewRequest("POST", "http://localhost:8765/api/jobs", strings.NewReader(`{"source_id":"nope"}`))
	req.Header.Set("Origin", "http://evil.example")
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden || rec.Header().Get("Access-Control-Allow-Origin") != "" {
		t.Fatalf("other origins stay refused, got %d %v", rec.Code, rec.Header())
	}
}

func TestHealthReadyAndHostChecks(t *testing.T) {
	srv, _ := newServer(t)
	if code, body := do(t, srv, "GET", "/healthz", ""); code != 200 || body["status"] != "ok" {
		t.Fatalf("healthz %d %v", code, body)
	}
	if code, body := do(t, srv, "GET", "/readyz", ""); code != 200 || body["ready"] != true {
		t.Fatalf("readyz %d %v", code, body)
	}
	req := httptest.NewRequest("GET", "http://evil.example/healthz", nil)
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusMisdirectedRequest {
		t.Fatalf("foreign host must be refused, got %d", rec.Code)
	}
	req = httptest.NewRequest("POST", "http://localhost:8765/api/jobs", strings.NewReader(`{"source_id":"src-1"}`))
	req.Header.Set("Origin", "http://evil.example")
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("cross-origin POST must be refused, got %d", rec.Code)
	}
}

func TestJobsEndpoints(t *testing.T) {
	srv, fj := newServer(t)
	if code, _ := do(t, srv, "GET", "/api/sources", ""); code != 404 {
		t.Fatalf("the shared-folder listing is gone from the API, got %d", code)
	}
	if code, _ := do(t, srv, "POST", "/api/jobs", `{"source_id":"nope"}`); code != 404 {
		t.Fatalf("unknown source %d", code)
	}
	if code, _ := do(t, srv, "POST", "/api/jobs", `not json`); code != 400 {
		t.Fatalf("bad body %d", code)
	}
	code, body := do(t, srv, "POST", "/api/jobs", `{"source_id":"src-1"}`)
	if code != 202 || body["id"] != "job-1" || body["status"] != "queued" {
		t.Fatalf("submit %d %v", code, body)
	}
	fj.full = true
	if code, body := do(t, srv, "POST", "/api/jobs", `{"source_id":"src-1"}`); code != 429 || body["error"].(map[string]any)["code"] != "queue_full" {
		t.Fatalf("queue full %d %v", code, body)
	}
	if code, body := do(t, srv, "GET", "/api/jobs/job-1", ""); code != 200 || body["status"] != "queued" {
		t.Fatalf("get %d %v", code, body)
	}
	if code, _ := do(t, srv, "GET", "/api/jobs/missing", ""); code != 404 {
		t.Fatalf("missing job %d", code)
	}
	if code, body := do(t, srv, "POST", "/api/jobs/job-1/cancel", ""); code != 200 || body["status"] != "cancelled" {
		t.Fatalf("cancel %d %v", code, body)
	}
	if code, body := do(t, srv, "POST", "/api/jobs/job-1/cancel", ""); code != 409 || body["job"].(map[string]any)["status"] != "cancelled" {
		t.Fatalf("second cancel %d %v", code, body)
	}
	if code, body := do(t, srv, "GET", "/api/jobs", ""); code != 200 || len(body["jobs"].([]any)) != 1 {
		t.Fatalf("list %d %v", code, body)
	}
}

func TestJobEventsStreamCurrentStateThenProgressUntilTerminal(t *testing.T) {
	srv, fj := newServer(t)
	fj.jobs["job-9"] = jobs.Job{ID: "job-9", Status: jobs.Running, OutputDir: "o"}
	server := httptest.NewServer(srv)
	defer server.Close()
	client := server.Client()
	req, _ := http.NewRequest("GET", server.URL+"/api/jobs/job-9/events", nil)
	req.Host = "localhost"
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		t.Fatalf("stream response %d %s", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	reader := bufio.NewReader(resp.Body)
	readEvent := func() (string, string) {
		var name, data string
		for {
			line, err := reader.ReadString('\n')
			if err != nil {
				t.Fatalf("stream ended early: %v", err)
			}
			line = strings.TrimRight(line, "\r\n")
			switch {
			case strings.HasPrefix(line, "event: "):
				name = strings.TrimPrefix(line, "event: ")
			case strings.HasPrefix(line, "data: "):
				data = strings.TrimPrefix(line, "data: ")
			case line == "":
				if name != "" {
					return name, data
				}
			}
		}
	}
	if name, data := readEvent(); name != "job" || !strings.Contains(data, `"status":"running"`) {
		t.Fatalf("first event %s %s", name, data)
	}
	time.Sleep(50 * time.Millisecond)
	pct := 40.0
	fj.hub.Publish(jobs.Event{JobID: "job-9", Status: jobs.Running, Stage: "ocr", Percent: &pct, At: time.Now()})
	if name, data := readEvent(); name != "progress" || !strings.Contains(data, `"percent":40`) {
		t.Fatalf("progress event %s %s", name, data)
	}
	fj.mu.Lock()
	fj.jobs["job-9"] = jobs.Job{ID: "job-9", Status: jobs.Succeeded, OutputDir: "o", ReportID: "rep-9"}
	fj.mu.Unlock()
	fj.hub.Publish(jobs.Event{JobID: "job-9", Status: jobs.Succeeded, Terminal: true, At: time.Now()})
	if name, _ := readEvent(); name != "progress" {
		t.Fatalf("terminal progress event %s", name)
	}
	if name, data := readEvent(); name != "job" || !strings.Contains(data, `"report_id":"rep-9"`) {
		t.Fatalf("final job event %s %s", name, data)
	}
	if _, err := reader.ReadString('\n'); err == nil {
		t.Fatal("stream must close after the terminal event")
	}
}

func TestReportEndpoints(t *testing.T) {
	srv, _ := newServer(t)
	if code, body := do(t, srv, "GET", "/api/reports", ""); code != 200 || len(body["reports"].([]any)) != 1 {
		t.Fatalf("list %d %v", code, body)
	}
	code, body := do(t, srv, "GET", "/api/reports/rep-1/summary", "")
	if code != 200 || body["turns"].(float64) != 2 || body["entries"].(float64) != 4 || body["unassigned"].(float64) != 1 {
		t.Fatalf("summary %d %v", code, body)
	}
	code, body = do(t, srv, "GET", "/api/reports/rep-1/turns", "")
	turns := body["turns"].([]any)
	if code != 200 || len(turns) != 2 || turns[1].(map[string]any)["entry_count"].(float64) != 3 {
		t.Fatalf("turns %d %v", code, body)
	}
	code, body = do(t, srv, "GET", "/api/reports/rep-1/turns/turn-002", "")
	if code != 200 || len(body["entries"].([]any)) != 3 || body["turn"].(map[string]any)["id"] != "turn-002" {
		t.Fatalf("turn detail %d %v", code, body)
	}
	if code, _ := do(t, srv, "GET", "/api/reports/rep-1/turns/turn-999", ""); code != 404 {
		t.Fatalf("unknown turn %d", code)
	}
	code, body = do(t, srv, "GET", "/api/reports/rep-1/unassigned", "")
	if code != 200 || len(body["entries"].([]any)) != 1 {
		t.Fatalf("unassigned %d %v", code, body)
	}
	req := httptest.NewRequest("GET", "http://localhost/api/reports/rep-1/download", nil)
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "full-recording-v1") || !strings.Contains(rec.Header().Get("Content-Disposition"), "report-rep-1.json") {
		t.Fatalf("download %d %s", rec.Code, rec.Body.String())
	}
	if code, _ := do(t, srv, "GET", "/api/reports/nope/summary", ""); code != 404 {
		t.Fatalf("unknown report %d", code)
	}
	if code, body := do(t, srv, "GET", "/api/reports/rep-1/frame?ms=abc", ""); code != 400 || body["error"].(map[string]any)["code"] != "bad_timestamp" {
		t.Fatalf("bad timestamp %d %v", code, body)
	}
	if code, body := do(t, srv, "GET", "/api/reports/rep-1/frame?ms=1000", ""); code != 404 || body["error"].(map[string]any)["code"] != "recording_unavailable" {
		t.Fatalf("frame without a recording %d %v", code, body)
	}
}
