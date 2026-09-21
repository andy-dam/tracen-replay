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
one thing the application sends is a question to GitHub once a day: which
release is the newest. It carries the installed version and nothing else,
and Update Check in Settings turns it off.

**Graphics acceleration:** most of an analysis is reading text off the
frames, and the graphics and neural hardware in an Apple chip (through
CoreML) is quicker at that than the processor. The application uses it
from the start. It is new on the Mac: if an analysis fails or reads worse,
turn the switch off in Settings and analyze again.

**Updating:** the Runs page says when a newer version exists and links to
it. Drag it onto Applications and replace; your data stays where it is.

**Removing:** drag the application to the Trash; your data under
`~/Library/Application Support/TracenReplay` stays unless you delete it too.
