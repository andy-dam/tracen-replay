//go:build windows

package main

import (
	_ "embed"
	"runtime"

	"fyne.io/systray"
)

// The application's icon, for the notification area (desktop/icon/make_icons.py
// writes it).
//
//go:embed tray.ico
var trayIcon []byte

// background names where the application goes when its window is closed but
// it keeps running.
const background = "tray"

// startTray puts the application's icon in the notification area. A click
// on it, or "Open" in its menu, brings the window back; "Exit" ends the
// application. The icon has its own message loop on a thread of its own.
func startTray(show, quit func()) {
	go func() {
		runtime.LockOSThread()
		systray.Run(func() {
			systray.SetIcon(trayIcon)
			systray.SetTooltip("Tracen Replay")
			systray.SetOnTapped(show)
			open := systray.AddMenuItem("Open Tracen Replay", "")
			systray.AddSeparator()
			exit := systray.AddMenuItem("Exit", "")
			go func() {
				for {
					select {
					case <-open.ClickedCh:
						show()
					case <-exit.ClickedCh:
						quit()
						return
					}
				}
			}()
		}, nil)
	}()
}

func stopTray() { systray.Quit() }
