//go:build !windows

package runner

import (
	"os/exec"
	"syscall"
)

// configureTree puts the child in its own process group so the whole tree
// can be signalled at once.
func configureTree(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

// tree is the process group on Unix; there is no kernel object that ends the
// group when this process dies, so the worker is also told this process's id
// (worker.Command.OwnerPID) and ends itself when that process is gone.
type tree struct{}

func adoptTree(cmd *exec.Cmd) (*tree, error) { return &tree{}, nil }

func (t *tree) kill(cmd *exec.Cmd) { killTree(cmd) }

func (t *tree) close() {}

func killTree(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
}

func alive(pid int) bool {
	return syscall.Kill(pid, 0) == nil
}
