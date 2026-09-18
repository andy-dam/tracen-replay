package runner

import (
	"bytes"
	"context"
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

// AnalyzerVersion asks the installed analyzer who it is, once. The answer is
// the identity of a tree on disk, so it cannot change while the service runs,
// and asking costs an interpreter start that no request should pay twice. A
// failure is not remembered: the analyzer may simply not have been reachable
// yet, and the next caller should be free to ask again.
type AnalyzerVersion struct {
	// Exec starts the interpreter; Python and WorkDir address the analyzer.
	Exec    Exec
	Python  string
	WorkDir string

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
	argv, err := worker.VersionArgv(a.Python)
	if err != nil {
		return worker.WorkerVersion{}, err
	}
	stdout, err := a.Exec.Probe(ctx, argv, a.WorkDir)
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
