//go:build darwin

package main

import (
	"os/exec"
	"runtime"
)

// quiet has nothing to hide on a Mac: a helper program opens no window.
func quiet(*exec.Cmd) {}

// hardwareAccelerator: CoreML's accelerators (the graphics cores and the
// Neural Engine) are part of every Apple silicon chip, and only there.
func hardwareAccelerator(string) (name string, known bool) {
	if runtime.GOARCH == "arm64" {
		return "Apple silicon", true
	}
	return "", true
}
