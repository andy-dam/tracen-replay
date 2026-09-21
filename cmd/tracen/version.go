package main

import (
	"context"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/andy-dam/tracen-replay/internal/api"
	"github.com/andy-dam/tracen-replay/internal/updates"
)

// version is the build's identity, set by the linker (-X main.version)
// from the release tag or the commit; "dev" for a plain go build.
var version = "dev"

// versionInfo is what /api/version answers: this build, and a newer release
// when the checker knows of one (internal/updates; nil never does).
func versionInfo(releases *updates.Checker) api.VersionInfo {
	info := api.VersionInfo{Version: version}
	info.Latest, info.URL = releases.Available()
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
