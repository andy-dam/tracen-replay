package artifacts

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// Media is what ffprobe says about an upload before any analysis touches
// it: enough to refuse a file that is not a video, or one the analyzer
// would spend hours or gigabytes on.
type Media struct {
	Duration time.Duration `json:"duration"`
	Width    int           `json:"width"`
	Height   int           `json:"height"`
	FPS      float64       `json:"fps"`
	// HasVideo is false for an audio file or a container ffprobe opened but
	// found no video stream in.
	HasVideo bool `json:"has_video"`
}

// ProbeMedia asks ffprobe (ffmpeg's companion) about the file. The probe
// reads headers, not frames, so it finishes in well under a second for a
// recording of any length; a file it cannot open is not a recording.
func ProbeMedia(ctx context.Context, ffprobe, path string) (Media, error) {
	if ffprobe == "" {
		ffprobe = "ffprobe"
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path)
	out, err := cmd.Output()
	if err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) && len(exit.Stderr) > 0 {
			return Media{}, fmt.Errorf("ffprobe: %s", strings.TrimSpace(string(exit.Stderr)))
		}
		return Media{}, fmt.Errorf("ffprobe: %w", err)
	}
	return parseProbe(out)
}

// parseProbe reads ffprobe's JSON: the first video stream's size and frame
// rate, and the container's duration.
func parseProbe(data []byte) (Media, error) {
	var report struct {
		Streams []struct {
			CodecType    string `json:"codec_type"`
			Width        int    `json:"width"`
			Height       int    `json:"height"`
			AvgFrameRate string `json:"avg_frame_rate"`
			RFrameRate   string `json:"r_frame_rate"`
		} `json:"streams"`
		Format struct {
			Duration string `json:"duration"`
		} `json:"format"`
	}
	if err := json.Unmarshal(data, &report); err != nil {
		return Media{}, fmt.Errorf("ffprobe output: %w", err)
	}
	var media Media
	if seconds, err := strconv.ParseFloat(report.Format.Duration, 64); err == nil && seconds > 0 {
		media.Duration = time.Duration(seconds * float64(time.Second))
	}
	for _, stream := range report.Streams {
		if stream.CodecType != "video" {
			continue
		}
		media.HasVideo = true
		media.Width, media.Height = stream.Width, stream.Height
		media.FPS = frameRate(stream.AvgFrameRate)
		if media.FPS == 0 {
			media.FPS = frameRate(stream.RFrameRate)
		}
		break
	}
	return media, nil
}

// frameRate reads ffprobe's "num/den" rational.
func frameRate(value string) float64 {
	num, den, found := strings.Cut(strings.TrimSpace(value), "/")
	n, err := strconv.ParseFloat(num, 64)
	if err != nil {
		return 0
	}
	if !found {
		return n
	}
	d, err := strconv.ParseFloat(den, 64)
	if err != nil || d == 0 {
		return 0
	}
	return n / d
}
