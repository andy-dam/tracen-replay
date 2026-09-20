package artifacts

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// Copy encodes the playback copy of a recording: the picture scaled to
// Height lines, a modest frame rate, no audio, H.264 at a quality that
// keeps the game's text legible at about a tenth of the original's size.
// The viewer plays it and evidence frames come from it; the original is
// what analyses read.
type Copy struct {
	// FFmpeg is the executable; empty means "ffmpeg" on PATH.
	FFmpeg string
	// Height is the picture height; zero means 720.
	Height int
	// FPS is the frame rate; zero means 30.
	FPS int
	// CRF is x264's constant rate factor; zero means 26.
	CRF int
	// Preset is the x264 preset; empty means veryfast.
	Preset string
	// Threads bounds the encoder's threads; zero lets ffmpeg decide.
	Threads int
	// Timeout bounds one encode; zero means three hours.
	Timeout time.Duration
}

// Encode writes the copy of src to dst (an .mp4 with the index at the
// front so a browser can seek before the whole file has arrived).
func (c Copy) Encode(ctx context.Context, src, dst string) error {
	ffmpeg, height, fps, crf, preset, timeout := c.FFmpeg, c.Height, c.FPS, c.CRF, c.Preset, c.Timeout
	if ffmpeg == "" {
		ffmpeg = "ffmpeg"
	}
	if height <= 0 {
		height = 720
	}
	if fps <= 0 {
		fps = 30
	}
	if crf <= 0 {
		crf = 26
	}
	if preset == "" {
		preset = "veryfast"
	}
	if timeout <= 0 {
		timeout = 3 * time.Hour
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	tmp := dst + ".part.mp4"
	args := []string{"-hide_banner", "-loglevel", "error", "-y", "-i", src,
		"-vf", "scale=-2:" + strconv.Itoa(height), "-r", strconv.Itoa(fps), "-an",
		"-c:v", "libx264", "-preset", preset, "-crf", strconv.Itoa(crf), "-pix_fmt", "yuv420p",
		"-movflags", "+faststart"}
	if c.Threads > 0 {
		args = append(args, "-threads", strconv.Itoa(c.Threads))
	}
	args = append(args, "-f", "mp4", tmp)
	cmd := exec.CommandContext(ctx, ffmpeg, args...)
	if output, err := cmd.CombinedOutput(); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("ffmpeg: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if info, err := os.Stat(tmp); err != nil || info.Size() == 0 {
		os.Remove(tmp)
		return errors.New("ffmpeg produced no copy")
	}
	return os.Rename(tmp, dst)
}
