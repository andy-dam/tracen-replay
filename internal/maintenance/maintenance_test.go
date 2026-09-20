package maintenance

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/objectstore"
	"github.com/andy-dam/tracen-replay/internal/store"
)

// An upload past its retention goes, file and row, unless an analysis is
// queued or running on it; a recent one stays; expired sessions go; the
// frame cache is pruned oldest first to its budget. Reports are not touched.
func TestSweepKeepsWhatIsInUseAndRecent(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(filepath.Join(dir, "t.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	uploads := filepath.Join(dir, "recordings")
	user := auth.User{ID: "u1", Email: "u1@example.com", DisplayName: "U", CreatedAt: now.Add(-40 * 24 * time.Hour)}
	if err := st.CreateUser(ctx, user, "hash"); err != nil {
		t.Fatal(err)
	}
	add := func(id string, age time.Duration) string {
		path := filepath.Join(uploads, "u1", id+".mp4")
		os.MkdirAll(filepath.Dir(path), 0o755)
		os.WriteFile(path, []byte("video"), 0o644)
		if err := st.CreateRecording(ctx, jobs.Recording{ID: id, UserID: "u1", Name: id + ".mp4", Path: path, Size: 5, SHA256: "x", CreatedAt: now.Add(-age)}); err != nil {
			t.Fatal(err)
		}
		return path
	}
	old := add("old", 20*24*time.Hour)
	busy := add("busy", 20*24*time.Hour)
	fresh := add("fresh", 2*24*time.Hour)
	if err := st.CreateJob(ctx, jobs.Job{ID: "job-1", UserID: "u1", SourceID: "busy", SourcePath: busy, SourceName: "busy.mp4", Status: jobs.Queued, CreatedAt: now, OutputDir: dir}); err != nil {
		t.Fatal(err)
	}
	st.CreateSession(ctx, "expired", "u1", now.Add(-31*24*time.Hour), now.Add(-time.Hour))
	st.CreateSession(ctx, "live", "u1", now.Add(-time.Hour), now.Add(29*24*time.Hour))
	cache := filepath.Join(dir, "frames", "rep-1")
	os.MkdirAll(cache, 0o755)
	for i, name := range []string{"1000.jpg", "2000.jpg", "3000.jpg"} {
		path := filepath.Join(cache, name)
		os.WriteFile(path, make([]byte, 100), 0o644)
		os.Chtimes(path, now.Add(time.Duration(i)*time.Minute), now.Add(time.Duration(i)*time.Minute))
	}
	sweeper := Sweeper{Store: st, RecordingsDir: uploads, RecordingRetention: 14 * 24 * time.Hour,
		Frames: artifacts.Frames{CacheDir: filepath.Join(dir, "frames"), MaxCacheBytes: 250}, Clock: func() time.Time { return now }}
	summary, err := sweeper.Sweep(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if summary.RecordingsDeleted != 1 || summary.BytesFreed != 5 || summary.SessionsDeleted != 1 || summary.FrameBytesPruned != 100 {
		t.Fatalf("summary: %+v", summary)
	}
	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Fatal("the old upload's file must be gone")
	}
	if _, err := st.GetRecording(ctx, "old"); err == nil {
		t.Fatal("the old upload's row must be gone")
	}
	for _, kept := range []string{busy, fresh} {
		if _, err := os.Stat(kept); err != nil {
			t.Fatalf("%s must stay: %v", kept, err)
		}
	}
	if _, err := st.SessionUser(ctx, "live", now); err != nil {
		t.Fatal("the live session must stay")
	}
	if _, err := os.Stat(filepath.Join(cache, "1000.jpg")); !os.IsNotExist(err) {
		t.Fatal("the oldest frame goes first")
	}
	if _, err := os.Stat(filepath.Join(cache, "3000.jpg")); err != nil {
		t.Fatal("the newest frame stays")
	}
	// A second sweep finds nothing to do.
	if again, err := sweeper.Sweep(ctx); err != nil || again != (Summary{}) {
		t.Fatalf("second sweep: %+v %v", again, err)
	}
	// Retention off keeps everything, whatever its age.
	forever := sweeper
	forever.RecordingRetention = 0
	add("ancient", 400*24*time.Hour)
	if summary, _ := forever.Sweep(ctx); summary.RecordingsDeleted != 0 {
		t.Fatal("retention off must delete nothing")
	}
}

// With an object store, uploads nobody completed are removed after a day;
// what a record names, and what is fresh, stays.
func TestSweepRemovesOrphanedUploads(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(filepath.Join(dir, "t.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	objects, err := objectstore.NewLocal(filepath.Join(dir, "objects"))
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	now := time.Now()
	if err := st.CreateUser(ctx, auth.User{ID: "u1", Email: "u1@example.com", DisplayName: "U", CreatedAt: now}, "hash"); err != nil {
		t.Fatal(err)
	}
	put := func(key string, age time.Duration) {
		if err := objects.Put(ctx, key, strings.NewReader("video"), 5, "video/mp4"); err != nil {
			t.Fatal(err)
		}
		path, _ := objects.Path(key)
		os.Chtimes(path, now.Add(-age), now.Add(-age))
	}
	put("originals/u1/named.mp4", 3*24*time.Hour)
	put("originals/u1/orphan.mp4", 2*24*time.Hour)
	put("originals/u1/fresh.mp4", time.Hour)
	if err := st.CreateRecording(ctx, jobs.Recording{ID: "named", UserID: "u1", Name: "named.mp4", Path: "originals/u1/named.mp4", Size: 5, CreatedAt: now.Add(-3 * 24 * time.Hour)}); err != nil {
		t.Fatal(err)
	}
	sweeper := Sweeper{Store: st, Objects: objects, Clock: func() time.Time { return now }}
	summary, err := sweeper.Sweep(ctx)
	if err != nil || summary.OrphansDeleted != 1 {
		t.Fatalf("sweep: %+v %v", summary, err)
	}
	left, _ := objects.List(ctx, "originals/")
	if len(left) != 2 || left[0].Key != "originals/u1/fresh.mp4" || left[1].Key != "originals/u1/named.mp4" {
		t.Fatalf("left: %+v", left)
	}
}
