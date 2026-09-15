package jobs_test

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/store"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

type idleRunner struct{}

func (idleRunner) Run(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
	<-ctx.Done()
	return -1, nil, ctx.Err()
}

type recordingsOf struct{ recs map[string]jobs.Recording }

func (r recordingsOf) GetRecording(ctx context.Context, id string) (jobs.Recording, error) {
	rec, ok := r.recs[id]
	if !ok {
		return jobs.Recording{}, &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	return rec, nil
}

// TestSubmitResolvesOnlyTheUsersOwnUpload proves an upload id submitted by
// another user is treated as unknown.
func TestSubmitResolvesOnlyTheUsersOwnUpload(t *testing.T) {
	dir := t.TempDir()
	st, err := store.Open(filepath.Join(dir, "t.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	recording := filepath.Join(dir, "clip.mp4")
	os.WriteFile(recording, []byte("x"), 0o644)
	recs := recordingsOf{recs: map[string]jobs.Recording{"up-1": {ID: "up-1", UserID: "andy", Name: "clip.mp4", Path: recording, Size: 1}}}
	manager, err := jobs.NewManager(jobs.Config{DataDir: dir, Python: "python", WorkDir: dir, Workers: 1, QueueLimit: 2, Recordings: recs}, st, idleRunner{})
	if err != nil {
		t.Fatal(err)
	}
	var nf *jobs.NotFoundError
	if _, err := manager.Submit(context.Background(), "someone", "up-1"); !errors.As(err, &nf) {
		t.Fatalf("another user's upload must be unknown, got %v", err)
	}
	job, err := manager.Submit(context.Background(), "andy", "up-1")
	if err != nil {
		t.Fatal(err)
	}
	if job.UserID != "andy" || job.SourcePath != recording || job.SourceName != "clip.mp4" {
		t.Fatalf("job from upload: %+v", job)
	}
	mine, _ := manager.List(context.Background(), "andy")
	theirs, _ := manager.List(context.Background(), "someone")
	if len(mine) != 1 || len(theirs) != 0 {
		t.Fatalf("visibility: mine %d theirs %d", len(mine), len(theirs))
	}
}
