//go:build darwin

package main

// On a Mac an application whose window is closed stays in the Dock, and a
// click there brings it back; there is no icon of our own to keep.
const background = "dock"

func startTray(show, quit func()) {}

func stopTray() {}
