// Package worker is the Go side of the Python analyzer contract described in
// docs/analysis-job.md: how the worker command is built, how its progress
// lines on stderr are read, and how its single terminal JSON object on stdout
// is interpreted and verified.
//
// Nothing here parses report.json beyond its schema version. The report is an
// opaque artifact to this service; the browser-facing data comes from
// timeline.json (see package timeline).
package worker

import (
	"errors"
	"fmt"
	"path/filepath"
	"strconv"
)

// Command describes one analyzer invocation.
type Command struct {
	// Python is the interpreter executable (a virtual environment's python).
	Python string
	// WorkDir is the directory that contains the tracen_replay package; the
	// worker is started with it as the working directory.
	WorkDir string
	// Source is the recording to analyze.
	Source string
	// Output is the run directory; every artifact of the job lives below it.
	Output string
	// ModelDir holds the OCR models. Empty keeps the worker's default.
	ModelDir string
	// Workers is the OCR worker count; it also sizes the pooled reload.
	Workers int
	// DenseWorkers is the worker count of the dense re-read OCR passes; zero
	// keeps the worker's default (Workers minus one, at least one). Each
	// dense worker stays near 2 GB, so free cores bound it more than memory.
	DenseWorkers int
	// FPS overrides the sampling rate; zero keeps the worker's default.
	FPS float64
	// OCRDevice selects the worker's OCR device (auto, cpu, dml, cuda) through
	// its TRACEN_REPLAY_OCR_DEVICE environment variable; empty leaves the
	// worker's own default (auto).
	OCRDevice string
	// PruneFrames deletes the sampled and re-read frames after the report is
	// validated. The application always sets it: the timeline locates every
	// fact by timestamp and frames are re-extracted on demand.
	PruneFrames bool
	// PruneWorkingData deletes every directory of the run (OCR caches, crops,
	// recovery inputs, about 1 GB per analysis) once the report is validated;
	// the report, the timeline, the viewer page and the log stay.
	PruneWorkingData bool
	// LearnedReader is the exported learned result-card reader (ONNX). The
	// analyzer stores its reads on training result readings and the
	// accounting uses one only where it equals an unexplained difference.
	// Empty leaves it off.
	LearnedReader string
	// OwnerPID names the process that owns the job (the service itself).
	// The worker watches it and ends, with its own worker processes, as
	// soon as that process is gone: nobody would read the result and the
	// OCR processes would keep the GPU and memory busy. Zero passes nothing.
	OwnerPID int
}

// Argv returns the argument vector for the worker, interpreter first. The
// process must be started with WorkDir as its working directory; no shell is
// involved at any point.
func (c Command) Argv() ([]string, error) {
	switch {
	case c.Python == "":
		return nil, errors.New("worker: python interpreter is required")
	case c.WorkDir == "":
		return nil, errors.New("worker: working directory is required")
	}
	args, err := c.Args(filepath.Clean)
	if err != nil {
		return nil, err
	}
	return append([]string{c.Python, "-X", "utf8", "-m", "tracen_replay.analysis_job"}, args...), nil
}

// Args returns the analyzer's own arguments, source first: what Argv passes
// after the interpreter and the module, and what a worker container takes,
// since the worker image's entrypoint is that interpreter and module. clean
// normalizes each path; Argv passes filepath.Clean, and a caller whose paths
// belong to another file system, a Linux container's, passes path.Clean.
func (c Command) Args(clean func(string) string) ([]string, error) {
	switch {
	case c.Source == "":
		return nil, errors.New("worker: source recording is required")
	case c.Output == "":
		return nil, errors.New("worker: output directory is required")
	case c.Workers < 1:
		return nil, fmt.Errorf("worker: workers must be at least 1, got %d", c.Workers)
	case c.FPS < 0:
		return nil, fmt.Errorf("worker: fps must not be negative, got %v", c.FPS)
	case c.DenseWorkers < 0:
		return nil, fmt.Errorf("worker: dense workers must not be negative, got %d", c.DenseWorkers)
	case c.OwnerPID < 0:
		return nil, fmt.Errorf("worker: owner pid must not be negative, got %d", c.OwnerPID)
	case !validDevice(c.OCRDevice):
		return nil, fmt.Errorf("worker: ocr device %q is not one of auto, cpu, dml, cuda, coreml", c.OCRDevice)
	}
	args := []string{clean(c.Source), "--output", clean(c.Output), "--workers", strconv.Itoa(c.Workers)}
	if c.ModelDir != "" {
		args = append(args, "--model-dir", clean(c.ModelDir))
	}
	if c.DenseWorkers > 0 {
		args = append(args, "--dense-workers", strconv.Itoa(c.DenseWorkers))
	}
	if c.FPS > 0 {
		args = append(args, "--fps", strconv.FormatFloat(c.FPS, 'f', -1, 64))
	}
	if c.LearnedReader != "" {
		args = append(args, "--learned-reader", clean(c.LearnedReader))
	}
	if c.PruneFrames {
		args = append(args, "--prune-frames")
	}
	if c.PruneWorkingData {
		args = append(args, "--prune-working-data")
	}
	if c.OwnerPID > 0 {
		args = append(args, "--owner-pid", strconv.Itoa(c.OwnerPID))
	}
	return args, nil
}

func validDevice(device string) bool {
	switch device {
	case "", "auto", "cpu", "dml", "cuda", "coreml":
		return true
	}
	return false
}

// Env returns the environment additions the worker needs for this command.
func (c Command) Env() []string {
	if c.OCRDevice == "" {
		return nil
	}
	return []string{"TRACEN_REPLAY_OCR_DEVICE=" + c.OCRDevice}
}
