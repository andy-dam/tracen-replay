package runner

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"sync"

	"github.com/andy-dam/tracen-replay/internal/worker"
)

// Probe runs a short analyzer query and returns its stdout. It starts the
// interpreter the same way an analysis does -- same environment, same
// console-free process configuration -- because a question that popped up a
// window or inherited a different environment would not be asking the same
// installation. The caller owns the deadline through ctx.
func (e Exec) Probe(ctx context.Context, argv []string, dir string) ([]byte, error) {
	if len(argv) == 0 {
		return nil, fmt.Errorf("probe: no command to run")
	}
	child := exec.CommandContext(ctx, argv[0], argv[1:]...)
	child.Dir = dir
	base := e.Env
	if base == nil {
		base = os.Environ()
	}
	child.Env = append([]string{}, base...)
	configureTree(child)
	var stdout, stderr bytes.Buffer
	child.Stdout = &stdout
	child.Stderr = &stderr
	if err := child.Run(); err != nil {
		if stderr.Len() > 0 {
			return nil, fmt.Errorf("probe %s: %w: %s", argv[0], err, bytes.TrimSpace(stderr.Bytes()))
		}
		return nil, fmt.Errorf("probe %s: %w", argv[0], err)
	}
	return stdout.Bytes(), nil
}

// VersionQuery returns the identity query for the analyzer an interpreter
// reaches: the --worker-version run of docs/analysis-job.md, started the way
// an analysis is.
func (e Exec) VersionQuery(python, workDir string) func(context.Context) ([]byte, error) {
	return func(ctx context.Context) ([]byte, error) {
		argv, err := worker.VersionArgv(python)
		if err != nil {
			return nil, err
		}
		return e.Probe(ctx, argv, workDir)
	}
}

// AnalyzerVersion asks the installed analyzer who it is, once. The answer is
// the identity of a tree on disk or of an image, so it cannot change while
// the service runs, and asking costs an interpreter or container start that
// no request should pay twice. A failure is not remembered: the analyzer may
// simply not have been reachable yet, and the next caller should be free to
// ask again.
type AnalyzerVersion struct {
	// Ask runs the identity query and returns its stdout: Exec.VersionQuery
	// for an interpreter, Container.VersionQuery for the worker image.
	Ask func(ctx context.Context) ([]byte, error)

	once sync.Mutex
	got  bool
	held worker.WorkerVersion
}

// Version returns the installed analyzer's identity.
func (a *AnalyzerVersion) Version(ctx context.Context) (worker.WorkerVersion, error) {
	a.once.Lock()
	defer a.once.Unlock()
	if a.got {
		return a.held, nil
	}
	if a.Ask == nil {
		return worker.WorkerVersion{}, errors.New("no analyzer to ask for its version")
	}
	stdout, err := a.Ask(ctx)
	if err != nil {
		return worker.WorkerVersion{}, err
	}
	version, err := worker.DecodeVersion(stdout)
	if err != nil {
		return worker.WorkerVersion{}, err
	}
	a.got, a.held = true, version
	return version, nil
}
