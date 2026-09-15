package artifacts

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestConfinedRejectsEscapesAndNonFiles(t *testing.T) {
	root := t.TempDir()
	inside := filepath.Join(root, "run", "report.json")
	if err := os.MkdirAll(filepath.Dir(inside), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(inside, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "secret.json")
	if err := os.WriteFile(outside, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, err := Confined(root, inside); err != nil || got == "" {
		t.Fatalf("inside file: %v", err)
	}
	if _, err := Confined(root, outside); !errors.Is(err, ErrOutsideRoot) {
		t.Fatalf("outside file: %v", err)
	}
	if _, err := Confined(root, filepath.Join(root, "run", "..", "..", filepath.Base(filepath.Dir(outside)), "secret.json")); !errors.Is(err, ErrOutsideRoot) {
		t.Fatalf("traversal: %v", err)
	}
	if _, err := Confined(root, filepath.Join(root, "run")); err == nil {
		t.Fatal("a directory is not an artifact")
	}
	if _, err := Confined(root, filepath.Join(root, "run", "absent.json")); err == nil {
		t.Fatal("a missing file must fail")
	}
	link := filepath.Join(root, "run", "link.json")
	if err := os.Symlink(outside, link); err == nil {
		if _, err := Confined(root, link); !errors.Is(err, ErrOutsideRoot) {
			t.Fatalf("escaping symlink: %v", err)
		}
	}
}

func TestLogTailReturnsWholeLinesFromTheEnd(t *testing.T) {
	path := filepath.Join(t.TempDir(), "worker.log")
	if err := os.WriteFile(path, []byte("line one\nline two\nline three\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	whole, _ := LogTail(path, 1000)
	if whole != "line one\nline two\nline three\n" {
		t.Fatalf("whole: %q", whole)
	}
	tail, _ := LogTail(path, 15)
	if tail != "line three\n" {
		t.Fatalf("tail: %q", tail)
	}
}

func TestFramesExtractsAndCaches(t *testing.T) {
	ffmpeg, err := exec.LookPath("ffmpeg")
	if err != nil {
		t.Skip("ffmpeg not installed")
	}
	dir := t.TempDir()
	recording := filepath.Join(dir, "clip.mp4")
	make := exec.Command(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "2", "-pix_fmt", "yuv420p", recording)
	if out, err := make.CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg test clip: %v %s", err, out)
	}
	frames := Frames{FFmpeg: ffmpeg, CacheDir: filepath.Join(dir, "cache"), Timeout: 20 * time.Second}
	path, err := frames.At(context.Background(), "rep-1", recording, 1500)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Size() == 0 || !strings.HasSuffix(path, filepath.Join("rep-1", "1500.jpg")) {
		t.Fatalf("frame %s: %v", path, err)
	}
	first := info.ModTime()
	again, err := frames.At(context.Background(), "rep-1", recording, 1500)
	if err != nil || again != path {
		t.Fatalf("cached frame: %s %v", again, err)
	}
	if info, _ := os.Stat(again); !info.ModTime().Equal(first) {
		t.Fatal("cached frame must not be re-extracted")
	}
	if _, err := frames.At(context.Background(), "../escape", recording, 0); err == nil {
		t.Fatal("report id with path separators must be rejected")
	}
	if _, err := frames.At(context.Background(), "rep-1", recording, -5); err == nil {
		t.Fatal("negative timestamp must be rejected")
	}
	if _, err := frames.At(context.Background(), "rep-1", recording, 60_000); err == nil {
		t.Fatal("a timestamp beyond the clip must fail rather than return an empty file")
	}
}
