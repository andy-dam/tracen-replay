# Tracen Replay on your own machine

Start **Tracen Replay.exe** (the installer puts it in the Start menu). One
window opens with Runs, Guide, About and Settings. No account: it is your
machine.

**What stays here:** everything. Recordings, reports and the frames the
viewer shows live under `%APPDATA%\TracenReplay`. The application never
sends anything anywhere.

**Graphics acceleration:** most of an analysis is reading text off the
frames, and a graphics card is a lot quicker at that than the processor.
If your computer has a card that supports DirectX 12, the application uses
it from the start. Settings has the switch: turn it off if an analysis
fails or the computer gets too sluggish while one runs.

**Updating:** install the newer version over this one; your data stays
where it is.

**Removing:** the uninstaller in the Start menu, or delete the folder; your
data under `%APPDATA%\TracenReplay` stays unless you delete it too.

The installer and the application are not signed yet, so Windows may show
a SmartScreen warning the first time: choose "More info", then "Run
anyway".
