# Tracen Replay on your Mac

Drag **Tracen Replay** onto **Applications**, then open it. One window
opens with Runs, Guide, About and Settings. No account: it is your machine.
This build is for Apple silicon Macs (M1 and later), macOS 11 or newer.

**The first time you open it.** The application is not signed with an
Apple developer certificate yet, so macOS stops it: "Tracen Replay cannot
be opened" or "is damaged". It is not damaged. Open Terminal and run

    xattr -dr com.apple.quarantine "/Applications/Tracen Replay.app"

then open it again. (On some macOS versions you can instead try to open
it once, then go to System Settings, Privacy & Security, and press "Open
Anyway".)

**What stays here:** everything. Recordings, reports and the frames the
viewer shows live under `~/Library/Application Support/TracenReplay`. The
application never sends anything anywhere.

**Speed:** Settings has a switch to use the Apple silicon neural and
graphics hardware (CoreML). It is off to begin with, and the processor
does the reading. The switch is new on the Mac: if an analysis fails or
reads worse with it on, turn it off and analyze again.

**Updating:** drag the newer version onto Applications and replace; your
data stays where it is.

**Removing:** drag the application to the Trash; your data under
`~/Library/Application Support/TracenReplay` stays unless you delete it too.
