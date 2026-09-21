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
	"sort"
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
	// Gate, when set, bounds how many extractions run at once (NewGate);
	// each is an ffmpeg process seeking through a recording.
	Gate chan struct{}
	// MaxCacheBytes bounds the cache; Prune removes the oldest frames until
	// it fits. Zero leaves the cache unbounded.
	MaxCacheBytes int64
}

// NewGate allows n extractions at once.
func NewGate(n int) chan struct{} {
	return make(chan struct{}, max(1, n))
}

// Prune removes the oldest cached frames until the cache is within
// MaxCacheBytes, and returns the bytes removed. A frame's age is when it was
// extracted; a viewer who comes back to an old report gets it extracted
// again, which costs one ffmpeg seek.
func (f Frames) Prune() (int64, error) {
	if f.CacheDir == "" || f.MaxCacheBytes <= 0 {
		return 0, nil
	}
	type frame struct {
		path string
		size int64
		used time.Time
	}
	var frames []frame
	var total int64
	err := filepath.WalkDir(f.CacheDir, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return nil
		}
		frames = append(frames, frame{path: path, size: info.Size(), used: info.ModTime()})
		total += info.Size()
		return nil
	})
	if err != nil {
		return 0, err
	}
	if total <= f.MaxCacheBytes {
		return 0, nil
	}
	sort.Slice(frames, func(i, j int) bool { return frames[i].used.Before(frames[j].used) })
	var removed int64
	for _, frame := range frames {
		if total-removed <= f.MaxCacheBytes {
			break
		}
		if err := os.Remove(frame.path); err == nil {
			removed += frame.size
		}
	}
	return removed, nil
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

// errNoFrame is what At answers when ffmpeg had no frame to give, however
// the installed ffmpeg reports that.
var errNoFrame = errors.New("ffmpeg produced no frame (timestamp beyond the recording?)")

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
	if f.Gate != nil {
		select {
		case f.Gate <- struct{}{}:
			defer func() { <-f.Gate }()
		case <-ctx.Done():
			return "", ctx.Err()
		}
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
		// A timestamp past the end: older ffmpeg exits cleanly with no file
		// (the case below); newer ffmpeg fails, its encoder never having
		// seen a frame. The answer is the same either way.
		if text := string(output); strings.Contains(text, "Nothing was written into output file") || strings.Contains(text, "received no packets") {
			return "", errNoFrame
		}
		return "", fmt.Errorf("ffmpeg: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if info, err := os.Stat(tmp); err != nil || info.Size() == 0 {
		os.Remove(tmp)
		return "", errNoFrame
	}
	if err := os.Rename(tmp, out); err != nil {
		return "", err
	}
	return out, nil
}
