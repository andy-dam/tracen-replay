package api

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/objectstore"
)

// signingStore is an object store elsewhere: it has no local paths, and
// signs URLs for playback.
type signingStore struct{ local *objectstore.Local }

func (s signingStore) Put(ctx context.Context, key string, body io.Reader, size int64, contentType string) error {
	return s.local.Put(ctx, key, body, size, contentType)
}
func (s signingStore) Open(ctx context.Context, key string) (io.ReadCloser, error) {
	return s.local.Open(ctx, key)
}
func (s signingStore) Stat(ctx context.Context, key string) (objectstore.Object, error) {
	return s.local.Stat(ctx, key)
}
func (s signingStore) Delete(ctx context.Context, key string) error { return s.local.Delete(ctx, key) }
func (s signingStore) List(ctx context.Context, prefix string) ([]objectstore.Object, error) {
	return s.local.List(ctx, prefix)
}
func (s signingStore) PresignUpload(ctx context.Context, key, contentType string, ttl time.Duration) (string, error) {
	return "https://blobs.example.test/" + key + "?sig=up", nil
}
func (s signingStore) PresignRead(ctx context.Context, key string, ttl time.Duration) (string, error) {
	return "https://blobs.example.test/" + key + "?sig=1", nil
}

// With an object store, an upload lives there under a server-chosen key,
// the store's own file plays it back, a report's files are read from it,
// and deleting the report or the recording removes its objects.
func TestObjectStoreHoldsUploadsAndReports(t *testing.T) {
	objects, err := objectstore.NewLocal(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	timeline, err := os.ReadFile(filepath.Join("..", "..", "testdata", "timeline", "mini.json"))
	if err != nil {
		t.Fatal(err)
	}
	objects.Put(ctx, "jobs/j1/run/timeline.json", bytes.NewReader(timeline), int64(len(timeline)), "application/json")
	objects.Put(ctx, "jobs/j1/run/report.json", strings.NewReader(`{"schema_version":"tracen-replay/full-recording-v1"}`), 52, "application/json")
	objects.Put(ctx, "jobs/j1/worker.log", strings.NewReader("line one\nline two\n"), 18, "text/plain")
	report := jobs.Report{ID: "rep-1", UserID: "", Origin: "job", SourceName: "mini.mp4", ReportPath: "jobs/j1/run/report.json", ReportSHA256: "abc",
		TimelinePath: "jobs/j1/run/timeline.json", EvidenceRoot: "jobs/j1/run", SourcePath: "originals/u/missing.mp4", Turns: 2, Entries: 4}
	fj := &fakeJobs{jobs: map[string]jobs.Job{"j1": {ID: "j1", Status: jobs.Succeeded, LogPath: "jobs/j1/worker.log"}}, hub: jobs.NewHub()}
	recs := &memoryRecordings{recs: map[string]jobs.Recording{}}
	srv := New(Config{Jobs: fj, Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}},
		Auth: auth.New(newMemoryAuth()), Recordings: recs, RecordingsDir: t.TempDir(), UploadLimit: 1 << 20, Objects: objects})
	andy := register(t, srv, "andy@example.com")

	// The report's files come from the store.
	rec := call(t, srv, "GET", "/api/reports/rep-1/summary", nil, "", andy)
	var summary map[string]any
	json.Unmarshal(rec.Body.Bytes(), &summary)
	if rec.Code != 200 || summary["turns"].(float64) != 2 || summary["video_available"] != false {
		t.Fatalf("summary %d %s", rec.Code, rec.Body.String())
	}
	if rec := call(t, srv, "GET", "/api/reports/rep-1/download", nil, "", andy); rec.Code != 200 || !strings.Contains(rec.Body.String(), "full-recording-v1") {
		t.Fatalf("download %d %s", rec.Code, rec.Body.String())
	}
	if rec := call(t, srv, "GET", "/api/jobs/j1/log", nil, "", andy); rec.Code != 200 || rec.Body.String() != "line one\nline two\n" {
		t.Fatalf("log %d %q", rec.Code, rec.Body.String())
	}
	if rec := call(t, srv, "GET", "/api/reports/rep-1/frame?ms=1000", nil, "", andy); rec.Code != 404 {
		t.Fatalf("frame of a missing recording %d %s", rec.Code, rec.Body.String())
	}

	// An upload lands in the store under originals/<user>/<id>.<ext> and
	// nothing stays in the probe directory.
	content := bytes.Repeat([]byte("frame"), 2000)
	body, ct := multipartFile(t, "file", "my run.mp4", content)
	rec = call(t, srv, "POST", "/api/recordings", body, ct, andy)
	if rec.Code != http.StatusCreated {
		t.Fatalf("upload: %d %s", rec.Code, rec.Body.String())
	}
	var uploaded jobs.Recording
	json.Unmarshal(rec.Body.Bytes(), &uploaded)
	stored := recs.recs[uploaded.ID]
	if !strings.HasPrefix(stored.Path, "originals/") || !strings.HasSuffix(stored.Path, "/"+uploaded.ID+".mp4") {
		t.Fatalf("upload key: %s", stored.Path)
	}
	if object, err := objects.Stat(ctx, stored.Path); err != nil || object.Size != int64(len(content)) {
		t.Fatalf("stored object: %+v %v", object, err)
	}
	filepath.WalkDir(srv.cfg.RecordingsDir, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			t.Errorf("probe directory keeps %s", p)
		}
		return nil
	})
	// Playback from the store's own file, with ranges.
	req := httptest.NewRequest("GET", "http://127.0.0.1:8765/api/recordings/"+uploaded.ID+"/video", nil)
	req.Header.Set("Range", "bytes=5-9")
	req.AddCookie(&http.Cookie{Name: sessionCookie, Value: andy})
	out := httptest.NewRecorder()
	srv.ServeHTTP(out, req)
	if out.Code != http.StatusPartialContent || out.Body.String() != "frame" {
		t.Fatalf("range request: %d %q", out.Code, out.Body.String())
	}
	// Deleting the recording removes its object.
	if rec := call(t, srv, "DELETE", "/api/recordings/"+uploaded.ID, nil, "", andy); rec.Code != http.StatusNoContent {
		t.Fatalf("delete: %d", rec.Code)
	}
	if _, err := objects.Stat(ctx, stored.Path); err == nil {
		t.Fatal("the recording's object must be gone")
	}

	// A signing store sends the player to a signed URL instead.
	signingReports := fakeReports{reports: map[string]jobs.Report{}}
	signing := New(Config{Jobs: fj, Reports: signingReports, Auth: auth.New(newMemoryAuth()),
		Recordings: recs, RecordingsDir: t.TempDir(), Objects: signingStore{objects}})
	owner := register(t, signing, "owner@example.com")
	rec = call(t, signing, "GET", "/api/auth/me", nil, "", owner)
	var me struct {
		User struct{ ID string } `json:"user"`
	}
	json.Unmarshal(rec.Body.Bytes(), &me)
	if me.User.ID == "" {
		t.Fatalf("me: %d %s", rec.Code, rec.Body.String())
	}
	report.UserID = me.User.ID
	signingReports.reports[report.ID] = report
	objects.Put(ctx, "originals/u/missing.mp4", strings.NewReader("video"), 5, "video/mp4")
	rec = call(t, signing, "GET", "/api/reports/rep-1/video", nil, "", owner)
	if rec.Code != http.StatusFound || rec.Header().Get("Location") != "https://blobs.example.test/originals/u/missing.mp4?sig=1" {
		t.Fatalf("signed playback: %d %s", rec.Code, rec.Header().Get("Location"))
	}
	// Deleting a report removes the run's objects but not the log.
	if rec := call(t, signing, "DELETE", "/api/reports/rep-1", nil, "", owner); rec.Code != http.StatusNoContent {
		t.Fatalf("delete report: %d %s", rec.Code, rec.Body.String())
	}
	if left, _ := objects.List(ctx, "jobs/j1/"); len(left) != 1 || left[0].Key != "jobs/j1/worker.log" {
		t.Fatalf("after deleting the report: %+v", left)
	}
}
