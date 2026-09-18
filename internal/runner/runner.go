// Package runner starts the Python analyzer as a child process and reports
// its output through the worker contract types. It is the only package that
// executes external commands for analysis. Cancellation kills the whole
// process tree: the analyzer spawns OCR worker processes of its own, and a
// cancelled job must not leave them running.
package runner

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/andy-dam/tracen-replay/internal/worker"
)

// MaxStdout bounds the captured terminal output; the contract is one small
// JSON object, so anything larger is already a violation.
const MaxStdout = 1 << 20

// Exec runs worker commands with os/exec.
type Exec struct {
	// KillGrace is how long a cancelled process tree gets before Run gives
	// up waiting for it. Zero means five seconds.
	KillGrace time.Duration
	// Env, when set, replaces the child's environment.
	Env []string
	// Logger receives the warning when a child could not be bound to its
	// process tree object; nil discards it.
	Logger *slog.Logger
}

// Run implements jobs.Runner.
func (e Exec) Run(ctx context.Context, cmd worker.Command, onProgress func(worker.Progress), logs io.Writer) (int, []byte, error) {
	argv, err := cmd.Argv()
	if err != nil {
		return 0, nil, err
	}
	child := exec.Command(argv[0], argv[1:]...)
	child.Dir = cmd.WorkDir
	base := e.Env
	if base == nil {
		base = os.Environ()
	}
	child.Env = append(append([]string{}, base...), cmd.Env()...)
	configureTree(child)
	s, err := startStreams(child, onProgress, logs)
	if err != nil {
		return 0, nil, fmt.Errorf("start analyzer: %w", err)
	}
	// Bind the child to a tree object whose lifetime is this process's: on
	// Windows a job object that ends every descendant when the handle closes,
	// so a crash of the service cannot leave OCR workers behind.
	tree, err := adoptTree(child)
	if err != nil && e.Logger != nil {
		e.Logger.Warn("worker tree could not be bound to a job object; cancellation falls back to taskkill", "error", err)
	}
	defer tree.close()

	select {
	case err := <-s.done:
		return exitStatus(err, s.out.Bytes(), s.outErr)
	case <-ctx.Done():
		tree.kill(child)
		select {
		case <-s.done:
		case <-time.After(grace(e.KillGrace)):
			child.Process.Kill()
			<-s.done
		}
		return -1, s.out.Bytes(), ctx.Err()
	}
}

// grace is how long a cancelled worker gets to go away before it is killed
// outright; zero means five seconds.
func grace(configured time.Duration) time.Duration {
	if configured == 0 {
		return 5 * time.Second
	}
	return configured
}

// streams is a started worker's output: stdout collected whole, bounded by
// MaxStdout, and stderr copied line by line to the log and parsed for
// progress. done receives the outcome of Wait once both streams are drained.
type streams struct {
	out    bytes.Buffer
	outErr error
	// lastLine is the last non-blank stderr line, the reason when a worker
	// never got as far as its own output.
	lastLine string
	done     chan error
}

func startStreams(child *exec.Cmd, onProgress func(worker.Progress), logs io.Writer) (*streams, error) {
	stdout, err := child.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := child.StderrPipe()
	if err != nil {
		return nil, err
	}
	if err := child.Start(); err != nil {
		return nil, err
	}
	s := &streams{done: make(chan error, 1)}
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		_, s.outErr = io.Copy(&s.out, io.LimitReader(stdout, MaxStdout+1))
		io.Copy(io.Discard, stdout)
	}()
	go func() {
		defer wg.Done()
		scanner := bufio.NewScanner(stderr)
		scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
		for scanner.Scan() {
			line := scanner.Text()
			if logs != nil {
				io.WriteString(logs, line+"\n")
			}
			if strings.TrimSpace(line) != "" {
				s.lastLine = line
			}
			if p, ok := worker.ParseProgress(line); ok && onProgress != nil {
				onProgress(p)
			}
		}
	}()
	go func() {
		wg.Wait()
		s.done <- child.Wait()
	}()
	return s, nil
}

func exitStatus(err error, stdout []byte, outErr error) (int, []byte, error) {
	if len(stdout) > MaxStdout {
		return 0, nil, errors.New("analyzer wrote more than 1 MiB to stdout")
	}
	if outErr != nil {
		return 0, nil, outErr
	}
	if err == nil {
		return 0, stdout, nil
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return exit.ExitCode(), stdout, nil
	}
	return 0, stdout, err
}

// Alive reports whether a process with the given id still exists. It is
// used by tests to prove that the tree was killed.
func Alive(pid int) bool {
	return alive(pid)
}
