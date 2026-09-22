package main

import "os/exec"

// revealDirectory opens a directory in the Finder.
func revealDirectory(directory string) error {
	return exec.Command("open", directory).Run()
}
