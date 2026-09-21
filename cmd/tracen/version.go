package main

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/andy-dam/tracen-replay/internal/api"
)

// version is the build's identity, set by the linker (-X main.version)
// from the release tag or the commit; "dev" for a plain go build.
var version = "dev"

// releasesURL is where the newest release is asked for, once a day: the
// tag name and its page, nothing else is sent or read.
const releasesURL = "https://api.github.com/repos/andy-dam/tracen-replay/releases/latest"

// updates knows the newest release, when asked to look.
type updates struct {
	mu     sync.Mutex
	latest string
	url    string
	log    *slog.Logger
}

// Run asks for the newest release soon after start and then daily, until
// ctx ends.
func (u *updates) Run(ctx context.Context) {
	delay := 20 * time.Second
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}
		u.look(ctx)
		delay = 24 * time.Hour
	}
}

func (u *updates) look(ctx context.Context) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, releasesURL, nil)
	if err != nil {
		return
	}
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("User-Agent", "tracen-replay/"+version)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		u.log.Debug("update check failed", "error", err)
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return
	}
	var release struct {
		Tag string `json:"tag_name"`
		URL string `json:"html_url"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(nil, response.Body, 1<<20)).Decode(&release); err != nil || release.Tag == "" {
		return
	}
	u.mu.Lock()
	u.latest, u.url = release.Tag, release.URL
	u.mu.Unlock()
}

// Info is what /api/version answers.
func (u *updates) Info() api.VersionInfo {
	info := api.VersionInfo{Version: version}
	if u == nil {
		return info
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	// A newer release is one whose tag is not this build's; a dev build
	// is never told to update.
	if u.latest != "" && version != "dev" && u.latest != version && u.latest != "v"+version {
		info.Latest, info.URL = u.latest, u.url
	}
	return info
}

// ocrDevice asks the interpreter which ONNX Runtime providers it has, so
// the readiness answer says what the analyzer will run on before any
// analysis starts: the graphics card through DirectML or CUDA, or the CPU.
func ocrDeviceCheck(python, requested string) func() api.Check {
	var once sync.Once
	var check api.Check
	return func() api.Check {
		once.Do(func() {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			out, err := exec.CommandContext(ctx, python, "-c", "import onnxruntime; print(','.join(onnxruntime.get_available_providers()))").Output()
			if err != nil {
				check = api.Check{Name: "ocr-device", OK: true, Note: requested + " (the analyzer's runtime could not be asked which devices it has)"}
				return
			}
			providers := strings.TrimSpace(string(out))
			device := "CPU only: analyses take much longer"
			switch {
			case strings.Contains(providers, "DmlExecutionProvider") && requested != "cpu" && requested != "cuda":
				device = "DirectML (the graphics card)"
			case strings.Contains(providers, "CUDAExecutionProvider") && requested != "cpu" && requested != "dml":
				device = "CUDA (the graphics card)"
			}
			check = api.Check{Name: "ocr-device", OK: true, Note: device}
		})
		return check
	}
}
