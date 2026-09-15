package sources

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/jobs"
)

func TestFolderListsRecordingsAndResolvesOnlyIssuedIDs(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{"b run.mp4", "a run.MKV", "notes.txt", "clip.mov"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Mkdir(filepath.Join(dir, "folder.mp4"), 0o755); err != nil {
		t.Fatal(err)
	}
	folder, err := NewFolder(dir)
	if err != nil {
		t.Fatal(err)
	}
	list, err := folder.List()
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, s := range list {
		names = append(names, s.Name)
		if s.ID != ID(s.Name) || s.Size != 1 || filepath.Dir(s.Path) != folder.Root() {
			t.Fatalf("source %+v", s)
		}
	}
	if len(names) != 3 || names[0] != "a run.MKV" || names[1] != "b run.mp4" || names[2] != "clip.mov" {
		t.Fatalf("listed %v", names)
	}
	got, err := folder.Resolve(ID("clip.mov"))
	if err != nil || got.Name != "clip.mov" {
		t.Fatalf("resolve: %+v %v", got, err)
	}
	var nf *jobs.NotFoundError
	for _, id := range []string{"", "../clip.mov", filepath.Join(dir, "clip.mov"), ID("notes.txt"), ID("missing.mp4")} {
		if _, err := folder.Resolve(id); !errors.As(err, &nf) {
			t.Fatalf("id %q must not resolve, got %v", id, err)
		}
	}
	if _, err := NewFolder(filepath.Join(dir, "notes.txt")); err == nil {
		t.Fatal("a file is not a recordings folder")
	}
}
