//go:build windows

package runner

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

// TestClosingTheJobEndsTheTree proves the guarantee the service relies on
// when it dies without cleanup: the job handle closes with the process, and
// KILL_ON_JOB_CLOSE ends the worker and its grandchild.
func TestClosingTheJobEndsTheTree(t *testing.T) {
	dir := t.TempDir()
	pidFile := filepath.Join(dir, "child.pid")
	child := exec.Command(os.Args[0], "-test.run=TestMain")
	child.Env = append(os.Environ(), "TRACEN_FAKE_WORKER=hang", "TRACEN_FAKE_PIDFILE="+pidFile)
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	tree, err := adoptTree(child)
	if err != nil {
		child.Process.Kill()
		t.Fatalf("adoptTree: %v", err)
	}
	var grandchild int
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if data, err := os.ReadFile(pidFile); err == nil {
			grandchild, _ = strconv.Atoi(strings.TrimSpace(string(data)))
			if grandchild != 0 {
				break
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	if grandchild == 0 || !Alive(grandchild) {
		tree.kill(child)
		t.Fatalf("grandchild %d is not running", grandchild)
	}
	tree.close() // what happens implicitly when the service process dies
	deadline = time.Now().Add(5 * time.Second)
	for (Alive(grandchild) || Alive(child.Process.Pid)) && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
	}
	if Alive(grandchild) {
		t.Fatalf("grandchild %d survived the job close", grandchild)
	}
	if Alive(child.Process.Pid) {
		t.Fatalf("child %d survived the job close", child.Process.Pid)
	}
	child.Wait()
}
