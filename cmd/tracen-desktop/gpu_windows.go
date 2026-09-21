//go:build windows

package main

import (
	"context"
	"os/exec"
	"regexp"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

// totalMemoryBytes is the machine's installed memory, or 0 when Windows
// could not be asked.
func totalMemoryBytes() uint64 {
	// MEMORYSTATUSEX; the first field is the structure's own size.
	var status struct {
		length, load                                                              uint32
		totalPhys, availPhys, totalPage, availPage, totalVirtual, availVirtual, _ uint64
	}
	status.length = uint32(unsafe.Sizeof(status))
	ok, _, _ := windows.NewLazySystemDLL("kernel32.dll").NewProc("GlobalMemoryStatusEx").Call(uintptr(unsafe.Pointer(&status)))
	if ok == 0 {
		return 0
	}
	return status.totalPhys
}

// quiet keeps a helper program from flashing a console window over the
// application.
func quiet(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW}
}

// software names the display adapters that are not graphics hardware: the
// fallback Windows uses without a driver, and the ones remote sessions and
// virtual machines present. DirectML runs on them too, in software, far
// slower than the processor path, so they do not count as a graphics card.
var software = regexp.MustCompile(`(?i)microsoft basic|remote display|hyper-v|virtual|vmware|parsec|citrix|mirage|idd`)

var discrete = regexp.MustCompile(`(?i)nvidia|geforce|\brtx\b|radeon rx|\barc\b`)

// hardwareAccelerator names the machine's graphics card, if it has one the
// provider can really use. known is false when Windows could not be asked.
func hardwareAccelerator(provider string) (name string, known bool) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
		"(Get-CimInstance Win32_VideoController).Name")
	quiet(cmd)
	out, err := cmd.Output()
	if err != nil {
		return "", false
	}
	var best string
	for _, line := range strings.Split(string(out), "\n") {
		adapter := strings.TrimSpace(line)
		if adapter == "" || software.MatchString(adapter) {
			continue
		}
		// CUDA needs an NVIDIA card; DirectML takes any.
		if provider == "cuda" && !strings.Contains(strings.ToLower(adapter), "nvidia") {
			continue
		}
		// A discrete card is preferred to the processor's own graphics when
		// both are listed.
		if best == "" || discrete.MatchString(adapter) {
			best = adapter
		}
	}
	return best, true
}
