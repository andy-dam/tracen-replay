package runner

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/worker"
)

// TestMain doubles as the fake analyzer when TRACEN_FAKE_WORKER is set: it
// prints progress lines on stderr and a terminal object on stdout, and in
// "hang" mode spawns a child and sleeps until killed.
func TestMain(m *testing.M) {
	mode := os.Getenv("TRACEN_FAKE_WORKER")
	if mode == "" {
		os.Exit(m.Run())
	}
	switch mode {
	case "ok":
		fmt.Fprintln(os.Stderr, `{"stage": "stage_done", "name": "capture", "wall_s": 1.5}`)
		fmt.Fprintln(os.Stderr, "[WARNING] some library noise")
		fmt.Fprintln(os.Stderr, `{"stage": "ocr", "processed": 10, "total": 10, "elapsed_seconds": 1}`)
		fmt.Fprint(os.Stdout, `{"schema_version":"tracen-replay/analysis-job-v1","status":"failed","error":{"code":"unreadable_source","message":"fake"}}`)
		os.Exit(1)
	case "hang":
		pidFile := os.Getenv("TRACEN_FAKE_PIDFILE")
		child := exec.Command(os.Args[0], "-test.run=TestMain")
		child.Env = append(os.Environ(), "TRACEN_FAKE_WORKER=child")
		if err := child.Start(); err != nil {
			fmt.Fprintln(os.Stderr, "spawn failed:", err)
			os.Exit(3)
		}
		os.WriteFile(pidFile, []byte(strconv.Itoa(child.Process.Pid)), 0o644)
		fmt.Fprintln(os.Stderr, `{"stage": "stage_done", "name": "capture", "wall_s": 1.0}`)
		time.Sleep(60 * time.Second)
		os.Exit(0)
	case "child":
		time.Sleep(60 * time.Second)
		os.Exit(0)
	}
	os.Exit(4)
}

func fakeCommand(t *testing.T) worker.Command {
	t.Helper()
	dir := t.TempDir()
	return worker.Command{Python: os.Args[0], WorkDir: dir, Source: filepath.Join(dir, "in.mp4"), Output: filepath.Join(dir, "out"), Workers: 1}
}

func TestExecCapturesProgressStdoutAndExitCode(t *testing.T) {
	cmd := fakeCommand(t)
	r := Exec{Env: append(os.Environ(), "TRACEN_FAKE_WORKER=ok")}
	var progress []worker.Progress
	var logs strings.Builder
	exit, stdout, err := r.Run(context.Background(), cmd, func(p worker.Progress) { progress = append(progress, p) }, &logs)
	if err != nil {
		t.Fatal(err)
	}
	if exit != 1 {
		t.Fatalf("exit code %d, want 1", exit)
	}
	result, err := worker.Interpret(exit, stdout)
	if err != nil || result.Error == nil || result.Error.Code != "unreadable_source" {
		t.Fatalf("terminal object: %+v %v", result, err)
	}
	if len(progress) != 2 || progress[0].Name != "capture" || progress[1].Stage != worker.StageOCR {
		t.Fatalf("progress: %+v", progress)
	}
	if !strings.Contains(logs.String(), "library noise") || !strings.Contains(logs.String(), `"stage": "ocr"`) {
		t.Fatalf("log capture: %q", logs.String())
	}
}

func TestCancelKillsTheWholeProcessTree(t *testing.T) {
	cmd := fakeCommand(t)
	pidFile := filepath.Join(cmd.WorkDir, "child.pid")
	r := Exec{Env: append(os.Environ(), "TRACEN_FAKE_WORKER=hang", "TRACEN_FAKE_PIDFILE="+pidFile), KillGrace: 5 * time.Second}
	ctx, cancel := context.WithCancel(context.Background())
	started := make(chan struct{}, 1)
	done := make(chan error, 1)
	go func() {
		_, _, err := r.Run(ctx, cmd, func(p worker.Progress) {
			select {
			case started <- struct{}{}:
			default:
			}
		}, nil)
		done <- err
	}()
	select {
	case <-started:
	case <-time.After(20 * time.Second):
		t.Fatal("fake worker did not start")
	}
	var childPID int
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if data, err := os.ReadFile(pidFile); err == nil {
			childPID, _ = strconv.Atoi(strings.TrimSpace(string(data)))
			if childPID != 0 {
				break
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	if childPID == 0 || !Alive(childPID) {
		t.Fatalf("grandchild %d is not running before the cancel", childPID)
	}
	cancel()
	select {
	case err := <-done:
		if err != context.Canceled {
			t.Fatalf("Run returned %v, want context.Canceled", err)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("Run did not return after cancel")
	}
	deadline = time.Now().Add(5 * time.Second)
	for Alive(childPID) && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
	}
	if Alive(childPID) {
		t.Fatalf("grandchild %d survived the cancellation", childPID)
	}
}

func TestStartFailureIsReportedNotHidden(t *testing.T) {
	cmd := fakeCommand(t)
	cmd.Python = filepath.Join(cmd.WorkDir, "missing-python.exe")
	_, _, err := Exec{}.Run(context.Background(), cmd, nil, nil)
	if err == nil || !strings.Contains(err.Error(), "start analyzer") {
		t.Fatalf("expected a start error, got %v", err)
	}
}
