# Roadmap

The ordered list of concrete next steps, with what "done" means for each,
is [TODO.md](../TODO.md) at the repository root; this page is the longer
horizon.

## 1. Release 0.1.0 of the local application

An end-to-end acceptance run: a recording uploaded through the client,
analyzed by the service, and reviewed in the client's timeline, with the
analyzer's accounting numbers and the review layer's checks both in place.
"Done" for this release is the client, the service and the analyzer exactly
as they run today: the Vue client under `web/` embedded into the `tracen`
binary, uploads as the only way a recording enters the system (see
[architecture.md](architecture.md) and [local-app.md](local-app.md) for how
the service stores and serves them), and the analyzer's
`tracen_replay.analysis_job` as the one worker contract between them
([analysis-job.md](analysis-job.md)).

## 2. Learned readers

Replace the remaining OCR reading errors (clipped or dim badges, hidden
counters, the song note glyph, mangled receipt words) with models trained on
labels the causal accounting already produces, rather than further hand
tuning of the OCR rules. See [evaluation.md](evaluation.md), Part B, for the
model plan, the label source, and the splits and metrics that will judge it.

## 3. Containers, then hosting

One image for the local application (client, service, analyzer, ffmpeg and
the OCR models on the CPU provider, the data directory on a volume) is the
first step off this machine, followed by a worker image the service starts
as a container instead of a child process. [TODO.md](../TODO.md) lists the
steps and what "done" means.


Run the service and the analyzer worker on a host other than the user's own
machine. [deployment.md](deployment.md) lists what the current design already
keeps hostable and the four things that would have to change first: accounts
beyond one machine, an object store for uploads and job outputs, a queue that
outlives one process, and GPU workers addressed rather than spawned. No
provider is chosen yet.

## 4. Recordings the analyzer cannot read yet

A recording is read in its own layout. The game lays out one interface of
1080x1920 design units, scales it by `min(width / 1080, height / 1920)` of
its game area, pins each part to an edge or a centre and keeps each device's
clear margins; the analyzer places every box it reads by that rule (Layout
in [analyzer-pipeline.md](analyzer-pipeline.md)). The PC client in 16:9,
the game filling a portrait phone or tablet screen, and such a portrait game
placed inside a wider landscape video are read. Still refused with an
explicit error rather than read wrongly:

- **A game whose surroundings change as much as it does**: a game inside a
  wider video is found by its picture changing more than what surrounds it.
  Beside a moving background (a blurred copy of the video, a large live
  camera) no such area stands out and the recording is refused; finding the
  game by its lasting edges as well would open these.
- **A non-English client**, the same class of problem one layer up, where
  the fixed words a receipt is repaired against are English.

## 5. Source-code hygiene

- Keep the analyzer package named `tracen_replay` under `analyzer/`; nothing
  in this roadmap renames it.
- Tests that currently skip without a locally preserved fixture should move
  to fixtures small enough to publish with the repository, or be marked
  explicitly as local-only so a clean checkout's skip count is expected
  rather than silent.
- Replace the per-rule edit-distance thresholds scattered across the
  analyzer's text-repair modules with one shared, vocabulary-based OCR
  repair, so every rule that corrects a misread name or word draws on the
  same word list and the same matching logic. The first piece exists:
  `name_vocabulary` folds supporter and skill names read a glyph off into
  the names the run itself read many times (see
  [analyzer-pipeline.md](analyzer-pipeline.md)); the fixed-word receipt
  grammars still repair by their own thresholds.
