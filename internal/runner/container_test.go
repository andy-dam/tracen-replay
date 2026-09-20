package runner

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/worker"
)

// fakeDocker stands in for the docker client when TestMain runs in "docker"
// mode: it records every call, one line per call with the arguments joined
// by NUL, in the file named by TRACEN_FAKE_DOCKER_LOG, and answers run, rm,
// ps and image inspect the way docker does as far as Container can see. A
// "hang" run lives until its marker file under TRACEN_FAKE_DOCKER_STATE is
// removed by rm, which is what makes a container outlive its client.
func fakeDocker(args []string) int {
	if f, err := os.OpenFile(os.Getenv("TRACEN_FAKE_DOCKER_LOG"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
		fmt.Fprintln(f, strings.Join(args, "\x00"))
		f.Close()
	}
	state := os.Getenv("TRACEN_FAKE_DOCKER_STATE")
	mode := os.Getenv("TRACEN_FAKE_DOCKER_MODE")
	listed := strings.Fields(os.Getenv("TRACEN_FAKE_DOCKER_PS"))
	if len(args) == 0 {
		return 1
	}
	switch args[0] {
	case "run":
		var name string
		rest := args[1:]
		i := 0
		for i < len(rest) && strings.HasPrefix(rest[i], "-") {
			switch rest[i] {
			case "--rm":
				i++
			case "--name":
				name = rest[i+1]
				i += 2
			default: // --label, --user, --gpus, -e and -v all take a value
				i += 2
			}
		}
		if i >= len(rest) {
			fmt.Fprintln(os.Stderr, `docker: 'docker run' requires at least 1 argument`)
			return 125
		}
		image, analyzer := rest[i], rest[i+1:]
		switch mode {
		case "refused":
			fmt.Fprintf(os.Stderr, "Unable to find image '%s' locally\ndocker: Error response from daemon: pull access denied for %s.\n", image, image)
			return 125
		case "version":
			if len(analyzer) != 1 || analyzer[0] != "--worker-version" {
				return 4
			}
			fmt.Fprint(os.Stdout, `{"schema_version":"tracen-replay/worker-version-v1",`+
				`"worker_version":{"package":"0.1.0","code_digest":`+
				`"2eca55da00a834e13e6d32583091299d086ba8d7c9033639527be5b099f0af65"}}`)
			return 0
		case "hang":
			marker := filepath.Join(state, name)
			os.WriteFile(marker, []byte(image), 0o644)
			fmt.Fprintln(os.Stderr, `{"stage": "stage_done", "name": "capture", "wall_s": 1.0}`)
			for range 600 {
				if _, err := os.Stat(marker); err != nil {
					return 137
				}
				time.Sleep(100 * time.Millisecond)
			}
			return 0
		}
		fmt.Fprintln(os.Stderr, "[WARNING] some library noise")
		fmt.Fprintln(os.Stderr, `{"stage": "ocr", "processed": 3, "total": 3, "elapsed_seconds": 1}`)
		fmt.Fprint(os.Stdout, `{"schema_version":"tracen-replay/analysis-job-v1","status":"succeeded",`+
			`"report_schema_version":"tracen-replay/full-recording-v1",`+
			`"report_path":"/job/run/report.json","evidence_root":"/job/run","timeline_path":"/job/run/timeline.json",`+
			`"report_sha256":"`+strings.Repeat("a", 64)+`","source_sha256":"`+strings.Repeat("b", 64)+`",`+
			`"full_source_processed":true,"worker_version":{"package":"0.1.0","code_digest":"`+strings.Repeat("c", 64)+`"}}`)
		return 0
	case "rm":
		code := 0
		for _, name := range args[2:] {
			if err := os.Remove(filepath.Join(state, name)); err != nil && !slices.Contains(listed, name) {
				fmt.Fprintf(os.Stderr, "Error response from daemon: No such container: %s\n", name)
				code = 1
				continue
			}
			fmt.Fprintln(os.Stdout, name)
		}
		return code
	case "ps":
		for _, id := range listed {
			fmt.Fprintln(os.Stdout, id)
		}
		return 0
	case "image":
		if mode == "refused" {
			fmt.Fprintf(os.Stderr, "Error response from daemon: No such image: %s\n", args[len(args)-1])
			return 1
		}
		fmt.Fprintln(os.Stdout, "sha256:0123")
		return 0
	}
	return 4
}

func fakeContainer(t *testing.T, mode string, extra ...string) (Container, string) {
	t.Helper()
	dir := t.TempDir()
	logPath := filepath.Join(dir, "docker.log")
	env := append(os.Environ(), "TRACEN_FAKE_WORKER=docker", "TRACEN_FAKE_DOCKER_MODE="+mode,
		"TRACEN_FAKE_DOCKER_LOG="+logPath, "TRACEN_FAKE_DOCKER_STATE="+dir)
	env = append(env, extra...)
	return Container{Docker: os.Args[0], Image: "tracen-replay-worker:test", Owner: "svc-1", Env: env, KillGrace: 10 * time.Second}, logPath
}

func dockerCalls(t *testing.T, logPath string) [][]string {
	t.Helper()
	data, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("no docker calls were recorded: %v", err)
	}
	var calls [][]string
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		calls = append(calls, strings.Split(line, "\x00"))
	}
	return calls
}

func TestContainerMountsTheJobAndMapsThePathsBack(t *testing.T) {
	c, logPath := fakeContainer(t, "ok")
	dir := t.TempDir()
	cmd := worker.Command{Python: "ignored", WorkDir: "ignored", Source: filepath.Join(dir, "in.mp4"), Output: filepath.Join(dir, "out"),
		ModelDir: "host-models", Workers: 1, OCRDevice: "cpu", LearnedReader: filepath.Join(dir, "reader.onnx"), PruneFrames: true, OwnerPID: 4242}
	var progress []worker.Progress
	var logs strings.Builder
	exit, stdout, err := c.Run(context.Background(), cmd, func(p worker.Progress) { progress = append(progress, p) }, &logs)
	if err != nil || exit != 0 {
		t.Fatalf("Run: exit %d, %v", exit, err)
	}
	result, err := worker.Interpret(exit, stdout)
	if err != nil {
		t.Fatalf("terminal object after mapping: %v\n%s", err, stdout)
	}
	out, _ := filepath.Abs(cmd.Output)
	if result.ReportPath != filepath.Join(out, "report.json") || result.EvidenceRoot != out || result.TimelinePath != filepath.Join(out, "timeline.json") {
		t.Fatalf("paths were not mapped to the host: %+v", result)
	}
	if result.ReportSHA256 != strings.Repeat("a", 64) || result.WorkerVersion == nil || result.WorkerVersion.Package != "0.1.0" {
		t.Fatalf("the rest of the object was not kept: %+v", result)
	}
	if _, err := os.Stat(out); err != nil {
		t.Fatalf("the run directory was not made before the mount: %v", err)
	}
	if len(progress) != 1 || progress[0].Stage != worker.StageOCR || !strings.Contains(logs.String(), "library noise") {
		t.Fatalf("progress %+v, logs %q", progress, logs.String())
	}
	calls := dockerCalls(t, logPath)
	if len(calls) != 1 || calls[0][0] != "run" {
		t.Fatalf("docker calls: %q", calls)
	}
	run := calls[0]
	source, _ := filepath.Abs(cmd.Source)
	reader, _ := filepath.Abs(cmd.LearnedReader)
	for _, pair := range [][2]string{
		{"--label", "tracen-replay.owner=svc-1"},
		{"-e", "TRACEN_REPLAY_OCR_DEVICE=cpu"},
		{"-v", source + ":/job/source/in.mp4:ro"},
		{"-v", out + ":/job/run"},
		{"-v", reader + ":/job/reader/reader.onnx:ro"},
	} {
		found := false
		for i := 0; i+1 < len(run); i++ {
			if run[i] == pair[0] && run[i+1] == pair[1] {
				found = true
			}
		}
		if !found {
			t.Errorf("docker run lacks %q %q: %q", pair[0], pair[1], run)
		}
	}
	image := slices.Index(run, c.Image)
	if image < 0 {
		t.Fatalf("image missing from %q", run)
	}
	want := []string{"/job/source/in.mp4", "--output", "/job/run", "--workers", "1", "--model-dir", "/opt/tracen/models",
		"--learned-reader", "/job/reader/reader.onnx", "--prune-frames"}
	if got := run[image+1:]; !slices.Equal(got, want) {
		t.Fatalf("analyzer arguments = %q, want %q", got, want)
	}
	if slices.Contains(run, "--owner-pid") || slices.Contains(run, "--gpus") || slices.Contains(run, "--user") {
		t.Fatalf("unexpected options in %q", run)
	}
}

func TestContainerPassesGPUsAndUserWhenAsked(t *testing.T) {
	c, logPath := fakeContainer(t, "ok")
	c.GPUs, c.User = "all", "1000:1000"
	dir := t.TempDir()
	cmd := worker.Command{Python: "p", WorkDir: "w", Source: filepath.Join(dir, "in.mp4"), Output: filepath.Join(dir, "out"), Workers: 2}
	if _, _, err := c.Run(context.Background(), cmd, nil, nil); err != nil {
		t.Fatal(err)
	}
	run := dockerCalls(t, logPath)[0]
	if i := slices.Index(run, "--gpus"); i < 0 || run[i+1] != "all" {
		t.Errorf("--gpus all missing from %q", run)
	}
	if i := slices.Index(run, "--user"); i < 0 || run[i+1] != "1000:1000" {
		t.Errorf("--user missing from %q", run)
	}
}

// A hosted worker is boxed: memory, CPUs and process count are capped and
// it has no network, since everything it needs is in the image.
func TestContainerPassesResourceBoundsWhenAsked(t *testing.T) {
	c, logPath := fakeContainer(t, "ok")
	c.Memory, c.CPUs, c.PidsLimit, c.Network = "14g", "4", 512, "none"
	dir := t.TempDir()
	cmd := worker.Command{Python: "p", WorkDir: "w", Source: filepath.Join(dir, "in.mp4"), Output: filepath.Join(dir, "out"), Workers: 2}
	if _, _, err := c.Run(context.Background(), cmd, nil, nil); err != nil {
		t.Fatal(err)
	}
	run := dockerCalls(t, logPath)[0]
	for flag, want := range map[string]string{"--memory": "14g", "--cpus": "4", "--pids-limit": "512", "--network": "none"} {
		if i := slices.Index(run, flag); i < 0 || run[i+1] != want {
			t.Errorf("%s %s missing from %q", flag, want, run)
		}
	}
}

func TestContainerCancelRemovesTheContainerNotJustTheClient(t *testing.T) {
	c, logPath := fakeContainer(t, "hang")
	dir := t.TempDir()
	cmd := worker.Command{Python: "p", WorkDir: "w", Source: filepath.Join(dir, "in.mp4"), Output: filepath.Join(dir, "out"), Workers: 1}
	ctx, cancel := context.WithCancel(context.Background())
	started := make(chan struct{}, 1)
	done := make(chan error, 1)
	go func() {
		_, _, err := c.Run(ctx, cmd, func(worker.Progress) {
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
		t.Fatal("fake container did not start")
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
	calls := dockerCalls(t, logPath)
	run := calls[0]
	name := run[slices.Index(run, "--name")+1]
	removed := false
	for _, call := range calls[1:] {
		if slices.Equal(call, []string{"rm", "-f", name}) {
			removed = true
		}
	}
	if !removed {
		t.Fatalf("the container was not removed by name: %q", calls)
	}
	state := c.Env[len(c.Env)-1][len("TRACEN_FAKE_DOCKER_STATE="):]
	if _, err := os.Stat(filepath.Join(state, name)); err == nil {
		t.Fatal("the container is still there after the cancel")
	}
}

func TestContainerReportsDockersOwnRefusal(t *testing.T) {
	c, _ := fakeContainer(t, "refused")
	dir := t.TempDir()
	cmd := worker.Command{Python: "p", WorkDir: "w", Source: filepath.Join(dir, "in.mp4"), Output: filepath.Join(dir, "out"), Workers: 1}
	_, _, err := c.Run(context.Background(), cmd, nil, nil)
	if err == nil || !strings.Contains(err.Error(), "pull access denied") {
		t.Fatalf("a refused run must fail to start with docker's words, got %v", err)
	}
	if _, err := c.ImageID(context.Background()); err == nil || !strings.Contains(err.Error(), "No such image") {
		t.Fatalf("ImageID must carry docker's words, got %v", err)
	}
}

func TestContainerRequiresAnImage(t *testing.T) {
	c, _ := fakeContainer(t, "ok")
	c.Image = ""
	cmd := worker.Command{Python: "p", WorkDir: "w", Source: "in.mp4", Output: "out", Workers: 1}
	if _, _, err := c.Run(context.Background(), cmd, nil, nil); err == nil {
		t.Fatal("expected a refusal without an image")
	}
	if _, err := (&AnalyzerVersion{Ask: c.VersionQuery()}).Version(context.Background()); err == nil {
		t.Fatal("expected the version query to refuse without an image")
	}
}

func TestContainerReapEndsTheOwnersContainersOnly(t *testing.T) {
	c, logPath := fakeContainer(t, "ok", "TRACEN_FAKE_DOCKER_PS=abc def")
	n, err := c.Reap(context.Background())
	if err != nil || n != 2 {
		t.Fatalf("Reap = %d, %v", n, err)
	}
	calls := dockerCalls(t, logPath)
	if len(calls) != 2 || !slices.Equal(calls[0], []string{"ps", "-q", "--filter", "label=tracen-replay.owner=svc-1"}) ||
		!slices.Equal(calls[1], []string{"rm", "-f", "abc", "def"}) {
		t.Fatalf("docker calls: %q", calls)
	}
	c.Owner = ""
	if n, err := c.Reap(context.Background()); err != nil || n != 0 {
		t.Fatalf("without an owner Reap must do nothing, got %d, %v", n, err)
	}
	if len(dockerCalls(t, logPath)) != 2 {
		t.Fatal("Reap without an owner still called docker")
	}
}

func TestContainerVersionQueryAsksTheImage(t *testing.T) {
	c, logPath := fakeContainer(t, "version")
	version, err := (&AnalyzerVersion{Ask: c.VersionQuery()}).Version(context.Background())
	if err != nil || version.Package != "0.1.0" {
		t.Fatalf("version = %+v, %v", version, err)
	}
	if calls := dockerCalls(t, logPath); !slices.Equal(calls[0], []string{"run", "--rm", c.Image, "--worker-version"}) {
		t.Fatalf("docker calls: %q", calls)
	}
}

func TestMapTerminalPathsLeavesAnythingButOneObjectAlone(t *testing.T) {
	for _, in := range []string{"", "not json", `{"a":1} {"b":2}`, `[1,2]`} {
		if got := mapTerminalPaths([]byte(in), "/job/run", "C:\\host"); string(got) != in {
			t.Errorf("%q became %q", in, got)
		}
	}
	failed := `{"schema_version":"tracen-replay/analysis-job-v1","status":"failed","error":{"code":"unreadable_source","message":"x"}}`
	result, err := worker.DecodeTerminal(mapTerminalPaths([]byte(failed), "/job/run", "C:\\host"))
	if err != nil || result.Error == nil || result.Error.Code != "unreadable_source" {
		t.Fatalf("a failed object must survive unchanged: %+v, %v", result, err)
	}
	if got, ok := hostPath("/job/runner/x", "/job/run", "h"); ok {
		t.Fatalf("a path that merely starts with the mount point's letters was mapped: %q", got)
	}
}
