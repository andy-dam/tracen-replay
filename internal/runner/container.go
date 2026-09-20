package runner

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/andy-dam/tracen-replay/internal/worker"
)

// Where a worker container finds its inputs and leaves its run. The
// recording and the learned reader are single files mounted read-only under
// their own directories; the run directory is the job's own, mounted
// read-write, and the worker's terminal object names paths under it that
// Run maps back to the host before returning them.
const (
	ContainerSourceDir = "/job/source"
	ContainerRunDir    = "/job/run"
	ContainerReaderDir = "/job/reader"
	// DefaultWorkerModelDir is where the worker image keeps the OCR models.
	DefaultWorkerModelDir = "/opt/tracen/models"
	// OwnerLabel is the container label that names the service instance a
	// worker container belongs to; Reap ends the ones that instance left.
	OwnerLabel = "tracen-replay.owner"
)

// Container runs each worker command as a one-shot container of the worker
// image (the Dockerfile's worker stage) through the docker command line, so
// the analyzer's dependencies, ffmpeg and the models come from the image and
// not from this machine. The container takes the analyzer's own command line
// (docs/analysis-job.md) with the mounted paths; its stdout and stderr are
// the worker's, so progress and the terminal object read exactly as from a
// child process. Cancellation removes the container by name: a container
// outlives the docker client that started it, so killing the client would
// leave the analysis running. The service's own process id means nothing
// inside the container, so --owner-pid is not passed; every container
// carries OwnerLabel instead, and Reap ends those a dead service left.
type Container struct {
	// Docker is the docker command line client; empty means "docker".
	Docker string
	// Image is the worker image; required.
	Image string
	// ModelDir is the OCR model directory inside the image; empty means
	// DefaultWorkerModelDir.
	ModelDir string
	// GPUs is passed as --gpus when set (for an image built with the CUDA
	// wheel); empty leaves the container on the CPU provider.
	GPUs string
	// User is passed as --user when set (uid[:gid]), so the files the worker
	// leaves in the run directory belong to the service's user on a Linux
	// host; empty runs as the image's user.
	User string
	// Owner names this service instance on every container it starts, and
	// is what Reap looks for. Empty leaves the label off, and Reap idle.
	Owner string
	// Memory, CPUs and PidsLimit are passed as --memory, --cpus and
	// --pids-limit when set: an upload that makes the analyzer balloon is
	// killed inside its container rather than taking the host down. Network
	// is passed as --network when set; the analyzer needs none, its models
	// and ffmpeg being in the image, so "none" is the hosted setting.
	Memory    string
	CPUs      string
	PidsLimit int
	Network   string
	// KillGrace is how long a cancelled container gets to go away before the
	// docker client is killed as a last resort. Zero means five seconds.
	KillGrace time.Duration
	// Env, when set, replaces the docker client's environment.
	Env []string
	// Logger receives what Reap found; nil discards it.
	Logger *slog.Logger
}

func (c Container) docker() string {
	if c.Docker == "" {
		return "docker"
	}
	return c.Docker
}

func (c Container) modelDir() string {
	if c.ModelDir == "" {
		return DefaultWorkerModelDir
	}
	return c.ModelDir
}

func (c Container) command(ctx context.Context, args ...string) *exec.Cmd {
	child := exec.CommandContext(ctx, c.docker(), args...)
	if c.Env != nil {
		child.Env = append([]string{}, c.Env...)
	}
	return child
}

// Run implements jobs.Runner.
func (c Container) Run(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
	if c.Image == "" {
		return 0, nil, errors.New("container: worker image is required")
	}
	if cmd.Source == "" || cmd.Output == "" {
		return 0, nil, errors.New("container: source recording and output directory are required")
	}
	source, err := filepath.Abs(cmd.Source)
	if err != nil {
		return 0, nil, err
	}
	output, err := filepath.Abs(cmd.Output)
	if err != nil {
		return 0, nil, err
	}
	// Made here, not by docker: a directory docker creates for a bind mount
	// belongs to root.
	if err := os.MkdirAll(output, 0o755); err != nil {
		return 0, nil, err
	}
	inner := cmd
	inner.Python, inner.WorkDir = "python", "/opt/tracen/analyzer"
	inner.Source = path.Join(ContainerSourceDir, filepath.Base(source))
	inner.Output = ContainerRunDir
	inner.ModelDir = c.modelDir()
	inner.OwnerPID = 0
	name := "tracen-worker-" + randomSuffix()
	run := []string{"run", "--rm", "--name", name}
	if c.Owner != "" {
		run = append(run, "--label", OwnerLabel+"="+c.Owner)
	}
	if c.User != "" {
		run = append(run, "--user", c.User)
	}
	if c.GPUs != "" {
		run = append(run, "--gpus", c.GPUs)
	}
	if c.Memory != "" {
		run = append(run, "--memory", c.Memory)
	}
	if c.CPUs != "" {
		run = append(run, "--cpus", c.CPUs)
	}
	if c.PidsLimit > 0 {
		run = append(run, "--pids-limit", strconv.Itoa(c.PidsLimit))
	}
	if c.Network != "" {
		run = append(run, "--network", c.Network)
	}
	for _, kv := range cmd.Env() {
		run = append(run, "-e", kv)
	}
	run = append(run, "-v", source+":"+inner.Source+":ro", "-v", output+":"+ContainerRunDir)
	if cmd.LearnedReader != "" {
		reader, err := filepath.Abs(cmd.LearnedReader)
		if err != nil {
			return 0, nil, err
		}
		inner.LearnedReader = path.Join(ContainerReaderDir, filepath.Base(reader))
		run = append(run, "-v", reader+":"+inner.LearnedReader+":ro")
	}
	args, err := inner.Args(path.Clean)
	if err != nil {
		return 0, nil, err
	}
	run = append(append(run, c.Image), args...)

	// Not CommandContext: cancellation is the container's removal, and the
	// client is killed only when that failed to bring it down.
	child := exec.Command(c.docker(), run...)
	if c.Env != nil {
		child.Env = append([]string{}, c.Env...)
	}
	s, err := startStreams(child, onProgress, logs)
	if err != nil {
		return 0, nil, fmt.Errorf("start %s: %w", c.docker(), err)
	}
	select {
	case err := <-s.done:
		exit, stdout, err := exitStatus(err, s.out.Bytes(), s.outErr)
		if err != nil {
			return exit, stdout, err
		}
		// 125, 126 and 127 are docker's own: the daemon refused, or the
		// command could not run. No worker ran, so its last line is the reason.
		if exit >= 125 && exit <= 127 && len(bytes.TrimSpace(stdout)) == 0 {
			return 0, nil, fmt.Errorf("%s run exited %d: %s", c.docker(), exit, s.lastLine)
		}
		return exit, mapTerminalPaths(stdout, ContainerRunDir, output), nil
	case <-ctx.Done():
		// The removal is repeated: a cancel that lands while docker is still
		// creating the container finds nothing to remove the first time.
		deadline := time.After(grace(c.KillGrace))
		again := time.NewTicker(500 * time.Millisecond)
		defer again.Stop()
		c.remove(name)
		for {
			select {
			case <-s.done:
				return -1, s.out.Bytes(), ctx.Err()
			case <-again.C:
				c.remove(name)
			case <-deadline:
				child.Process.Kill()
				<-s.done
				return -1, s.out.Bytes(), ctx.Err()
			}
		}
	}
}

func (c Container) remove(name string) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	c.command(ctx, "rm", "-f", name).Run()
}

func randomSuffix() string {
	var b [6]byte
	rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// Reap ends every worker container this service instance left behind. A
// container outlives the docker client that started it, so a service that
// died mid-job leaves its worker running with nobody to read the result;
// the job itself is marked interrupted on restart. It returns how many were
// ended, and does nothing without an Owner.
func (c Container) Reap(ctx context.Context) (int, error) {
	if c.Owner == "" {
		return 0, nil
	}
	out, err := c.command(ctx, "ps", "-q", "--filter", "label="+OwnerLabel+"="+c.Owner).Output()
	if err != nil {
		return 0, fmt.Errorf("list worker containers: %w", withStderr(err))
	}
	ids := strings.Fields(string(out))
	if len(ids) == 0 {
		return 0, nil
	}
	if c.Logger != nil {
		c.Logger.Warn("worker containers left by a previous run are being removed", "count", len(ids))
	}
	if err := c.command(ctx, append([]string{"rm", "-f"}, ids...)...).Run(); err != nil {
		return 0, fmt.Errorf("remove worker containers: %w", withStderr(err))
	}
	return len(ids), nil
}

// ImageID returns the worker image's identity, or why docker cannot see it.
func (c Container) ImageID(ctx context.Context) (string, error) {
	if c.Image == "" {
		return "", errors.New("container: worker image is required")
	}
	out, err := c.command(ctx, "image", "inspect", "--format", "{{.Id}}", c.Image).Output()
	if err != nil {
		return "", withStderr(err)
	}
	return strings.TrimSpace(string(out)), nil
}

// VersionQuery returns the identity query for the analyzer in the worker
// image: the --worker-version run of docs/analysis-job.md, in a container
// that does nothing else.
func (c Container) VersionQuery() func(context.Context) ([]byte, error) {
	return func(ctx context.Context) ([]byte, error) {
		if c.Image == "" {
			return nil, errors.New("container: worker image is required")
		}
		out, err := c.command(ctx, append([]string{"run", "--rm", c.Image}, worker.VersionArgs()...)...).Output()
		if err != nil {
			return nil, fmt.Errorf("probe %s: %w", c.Image, withStderr(err))
		}
		return out, nil
	}
}

// withStderr adds a failed docker command's own words to its exit error.
func withStderr(err error) error {
	var exit *exec.ExitError
	if errors.As(err, &exit) && len(bytes.TrimSpace(exit.Stderr)) > 0 {
		return fmt.Errorf("%w: %s", err, bytes.TrimSpace(exit.Stderr))
	}
	return err
}

// mapTerminalPaths rewrites the run-directory paths of the worker's terminal
// object from the container's mount point to the host directory mounted
// there, so the service verifies and reads the report where it is. Anything
// that is not exactly one JSON object is returned as it was, for the
// contract decoder to refuse in its own words.
func mapTerminalPaths(stdout []byte, from, to string) []byte {
	dec := json.NewDecoder(bytes.NewReader(stdout))
	var fields map[string]json.RawMessage
	if err := dec.Decode(&fields); err != nil || fields == nil {
		return stdout
	}
	if err := dec.Decode(new(json.RawMessage)); !errors.Is(err, io.EOF) {
		return stdout
	}
	for _, key := range []string{"report_path", "evidence_root", "timeline_path"} {
		raw, ok := fields[key]
		if !ok {
			continue
		}
		var value string
		if json.Unmarshal(raw, &value) != nil {
			continue
		}
		if mapped, ok := hostPath(value, from, to); ok {
			fields[key], _ = json.Marshal(mapped)
		}
	}
	out, err := json.Marshal(fields)
	if err != nil {
		return stdout
	}
	return out
}

func hostPath(value, from, to string) (string, bool) {
	switch {
	case value == from:
		return to, true
	case strings.HasPrefix(value, from+"/"):
		return filepath.Join(to, filepath.FromSlash(strings.TrimPrefix(value, from+"/"))), true
	}
	return "", false
}
