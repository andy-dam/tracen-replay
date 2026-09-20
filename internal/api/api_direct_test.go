package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/objectstore"
)

// A store that signs URLs gives the browser a target; the upload is
// recorded once the object is there and probed; an upload nobody made is
// not recorded; a store that cannot sign sends the client to the
// multipart route.
func TestDirectUploadTargetAndCompletion(t *testing.T) {
	local, err := objectstore.NewLocal(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	recs := &memoryRecordings{recs: map[string]jobs.Recording{}}
	var probed string
	refuse := false
	srv := New(Config{Jobs: &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}, Reports: fakeReports{reports: map[string]jobs.Report{}},
		Auth: auth.New(newMemoryAuth()), Recordings: recs, RecordingsDir: t.TempDir(), UploadLimit: 1 << 20,
		Objects: signingStore{local}, Quota: Quota{MaxRecordingsPerUser: 1},
		Probe: func(ctx context.Context, input string) (artifacts.Media, error) {
			probed = input
			if refuse {
				return artifacts.Media{}, context.DeadlineExceeded
			}
			return artifacts.Media{HasVideo: true, Width: 1920, Height: 1080, FPS: 60}, nil
		}})
	andy := register(t, srv, "andy@example.com")
	begin := func(body string) (int, uploadTarget) {
		rec := call(t, srv, "POST", "/api/recordings/uploads", strings.NewReader(body), "application/json", andy)
		var target uploadTarget
		json.Unmarshal(rec.Body.Bytes(), &target)
		return rec.Code, target
	}
	if code, _ := begin(`{"name":"notes.txt","size":10}`); code != http.StatusBadRequest {
		t.Fatalf("a text file: %d", code)
	}
	if code, _ := begin(`{"name":"run.mp4","size":2000000}`); code != http.StatusRequestEntityTooLarge {
		t.Fatalf("over the limit: %d", code)
	}
	code, target := begin(`{"name":"my run.mp4","size":1000}`)
	if code != 200 || target.Mode != "direct" || target.ID == "" || target.Method != "PUT" || !strings.HasSuffix(target.URL, "?sig=up") ||
		target.Headers["x-ms-blob-type"] != "BlockBlob" || target.Headers["Content-Type"] != "video/mp4" {
		t.Fatalf("target: %d %+v", code, target)
	}
	key := strings.TrimSuffix(strings.TrimPrefix(target.URL, "https://blobs.example.test/"), "?sig=up")
	if !strings.HasPrefix(key, "originals/") || !strings.HasSuffix(key, "/"+target.ID+".mp4") {
		t.Fatalf("key: %s", key)
	}
	complete := func(id, name string) (int, map[string]any) {
		rec := call(t, srv, "POST", "/api/recordings/uploads/"+id+"/complete", strings.NewReader(`{"name":"`+name+`"}`), "application/json", andy)
		var body map[string]any
		json.Unmarshal(rec.Body.Bytes(), &body)
		return rec.Code, body
	}
	// Completing before the bytes arrived records nothing.
	if code, body := complete(target.ID, "my run.mp4"); code != http.StatusNotFound || body["error"].(map[string]any)["code"] != "upload_missing" {
		t.Fatalf("complete without an object: %d %v", code, body)
	}
	if code, _ := complete("not-an-id", "my run.mp4"); code != http.StatusBadRequest {
		t.Fatalf("bad id: %d", code)
	}
	// The browser's PUT, then the completion: probed where it lies, recorded.
	local.Put(ctx, key, strings.NewReader(strings.Repeat("f", 1000)), 1000, "video/mp4")
	code, body := complete(target.ID, "my run.mp4")
	if code != http.StatusCreated || body["id"] != target.ID || body["name"] != "my run.mp4" || body["size"].(float64) != 1000 || body["sha256"] != "" {
		t.Fatalf("complete: %d %v", code, body)
	}
	if probed != "https://blobs.example.test/"+key+"?sig=1" {
		t.Fatalf("probed %q", probed)
	}
	if recs.recs[target.ID].Path != key {
		t.Fatalf("recorded path: %+v", recs.recs[target.ID])
	}
	if code, body := complete(target.ID, "my run.mp4"); code != http.StatusConflict {
		t.Fatalf("completing twice: %d %v", code, body)
	}
	// The user now keeps their one recording; another target is refused.
	if code, _ := begin(`{"name":"second.mp4","size":10}`); code != http.StatusForbidden {
		t.Fatalf("over the quota: %d", code)
	}
	// A file the probe cannot read is deleted and not recorded.
	recs.recs = map[string]jobs.Recording{}
	code, target = begin(`{"name":"bad.mp4","size":10}`)
	if code != 200 {
		t.Fatalf("second target: %d", code)
	}
	badKey := strings.TrimSuffix(strings.TrimPrefix(target.URL, "https://blobs.example.test/"), "?sig=up")
	local.Put(ctx, badKey, strings.NewReader("not video"), 9, "video/mp4")
	refuse = true
	if code, body := complete(target.ID, "bad.mp4"); code != http.StatusBadRequest || body["error"].(map[string]any)["code"] != "unsupported_recording" {
		t.Fatalf("bad upload: %d %v", code, body)
	}
	if _, err := local.Stat(ctx, badKey); err == nil {
		t.Fatal("the refused upload must be deleted")
	}

	// A store without URLs: the client posts the file as before.
	plain := New(Config{Jobs: &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}, Reports: fakeReports{reports: map[string]jobs.Report{}},
		Recordings: recs, RecordingsDir: t.TempDir(), UploadLimit: 1 << 20, Objects: local})
	rec := call(t, plain, "POST", "/api/recordings/uploads", strings.NewReader(`{"name":"run.mp4","size":10}`), "application/json", "")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"mode":"multipart"`) {
		t.Fatalf("local store: %d %s", rec.Code, rec.Body.String())
	}
	rec = call(t, plain, "POST", "/api/recordings/uploads/"+strings.Repeat("a", 32)+"/complete", strings.NewReader(`{"name":"run.mp4"}`), "application/json", "")
	if rec.Code != http.StatusNotFound {
		t.Fatalf("complete on a local store: %d", rec.Code)
	}
}
