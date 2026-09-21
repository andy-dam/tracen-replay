//go:build darwin

package main

import (
	"os/exec"
	"runtime"

	"golang.org/x/sys/unix"
)

// totalMemoryBytes is the machine's installed memory, or 0 when the system
// could not be asked.
func totalMemoryBytes() uint64 {
	size, err := unix.SysctlUint64("hw.memsize")
	if err != nil {
		return 0
	}
	return size
}

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
