// Package artifacts gives confined access to what a job leaves behind: the
// unmodified report file, the tail of the worker log, and frames extracted
// from the recording on demand. Nothing here accepts a client-supplied path;
// every file is located from a report record and checked to lie under its
// evidence root.
package artifacts

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// ErrOutsideRoot is returned when a recorded path resolves outside the
// directory that owns it.
var ErrOutsideRoot = errors.New("artifact path resolves outside its evidence root")

// Confined resolves path and verifies that it lies under root (after
// following symbolic links on both sides) and names an existing regular file.
func Confined(root, path string) (string, error) {
	resolved, err := under(root, path)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() {
		return "", fmt.Errorf("%s is not a regular file", path)
	}
	return resolved, nil
}

// ConfinedDir resolves path and verifies that it is a directory strictly
// below root (after following symbolic links on both sides), so that a
// caller may remove it without touching root itself.
func ConfinedDir(root, path string) (string, error) {
	resolved, err := under(root, path)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		return "", fmt.Errorf("%s is not a directory", path)
	}
	return resolved, nil
}

// under resolves path and returns it when it lies strictly below root.
func under(root, path string) (string, error) {
	rootAbs, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(rootAbs, resolved)
	if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return "", ErrOutsideRoot
	}
	return resolved, nil
}

// LogTail returns the last max bytes of a log file, starting at a line
// boundary when the file was cut.
func LogTail(path string, max int64) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return "", err
	}
	start := int64(0)
	if info.Size() > max {
		start = info.Size() - max
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return "", err
	}
	data, err := io.ReadAll(f)
	if err != nil {
		return "", err
	}
	text := string(data)
	if start > 0 {
		if i := strings.IndexByte(text, '\n'); i >= 0 {
			text = text[i+1:]
		}
	}
	return text, nil
}

// Frames extracts single frames from recordings with ffmpeg and caches them.
type Frames struct {
	// FFmpeg is the executable; empty means "ffmpeg" on PATH.
	FFmpeg string
	// CacheDir receives one JPEG per (report, timestamp).
	CacheDir string
	// Timeout bounds one extraction.
	Timeout time.Duration
}

// At returns the path of a JPEG of the recording at the given source
// timestamp, extracting it on first use. The frame is the full video frame;
// the analyzer's facts are located by this timestamp, so the same instant
// is shown that the report read.
// Forget removes every cached frame of one report or recording.
func (f Frames) Forget(id string) error {
	if f.CacheDir == "" || id == "" {
		return nil
	}
	return os.RemoveAll(filepath.Join(f.CacheDir, id))
}

func (f Frames) At(ctx context.Context, reportID, recording string, timestampMS int64) (string, error) {
	if timestampMS < 0 {
		return "", errors.New("timestamp must not be negative")
	}
	if strings.ContainsAny(reportID, `/\`) || reportID == "" || reportID == "." || reportID == ".." {
		return "", errors.New("invalid report id")
	}
	dir := filepath.Join(f.CacheDir, reportID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	out := filepath.Join(dir, strconv.FormatInt(timestampMS, 10)+".jpg")
	if info, err := os.Stat(out); err == nil && info.Size() > 0 {
		return out, nil
	}
	ffmpeg := f.FFmpeg
	if ffmpeg == "" {
		ffmpeg = "ffmpeg"
	}
	timeout := f.Timeout
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	tmp := out + ".part"
	seconds := strconv.FormatFloat(float64(timestampMS)/1000, 'f', 3, 64)
	cmd := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", seconds, "-i", recording,
		"-frames:v", "1", "-q:v", "3", "-f", "image2", tmp)
	if output, err := cmd.CombinedOutput(); err != nil {
		os.Remove(tmp)
		return "", fmt.Errorf("ffmpeg: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if info, err := os.Stat(tmp); err != nil || info.Size() == 0 {
		os.Remove(tmp)
		return "", errors.New("ffmpeg produced no frame (timestamp beyond the recording?)")
	}
	if err := os.Rename(tmp, out); err != nil {
		return "", err
	}
	return out, nil
}
