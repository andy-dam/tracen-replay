# Tracen Replay on your own machine

Double-click **Tracen Replay.cmd**. A console window opens (leave it open;
closing it stops the service), and your browser opens
http://127.0.0.1:8765/. Create an account there; it exists only on this
machine.

**What stays here:** everything. Accounts, recordings, reports and the
frames the viewer shows live under `%LOCALAPPDATA%\TracenReplay`. The
service listens on this machine only and never sends anything anywhere,
except the once-a-day check for a newer version, which asks GitHub for the
latest release number and nothing else.

**Speed:** the analyzer uses your graphics card through DirectX 12 when it
has one (any card from the last several years); without one it uses the
processor and takes much longer. The Runs page says which it found.

**Updating:** download the newer zip, unzip it next to this one, and start
that one instead; your data stays where it is.

**Removing:** delete this folder and, if you want the data gone too,
`%LOCALAPPDATA%\TracenReplay`.

The first start on a machine may show a Windows SmartScreen warning because
the launcher is not signed: choose "More info", then "Run anyway".
