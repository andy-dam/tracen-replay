package main

import (
	"os/exec"
	"syscall"

	"golang.org/x/sys/windows"
)

// revealDirectory opens a directory in File Explorer.
func revealDirectory(directory string) error {
	cmd := exec.Command("explorer.exe", directory)
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW}
	// Explorer answers with exit code 1 even when it opened the window.
	cmd.Run()
	return nil
}
