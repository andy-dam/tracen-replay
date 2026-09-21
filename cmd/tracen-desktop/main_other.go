//go:build !windows && !darwin

// The desktop application is built for Windows and Mac; on other systems
// the container image or the service binary is the way in.
package main

import (
	"fmt"
	"os"
)

func main() {
	fmt.Fprintln(os.Stderr, "tracen-desktop: the desktop application is available for Windows and Mac; use the tracen service binary here")
	os.Exit(2)
}
