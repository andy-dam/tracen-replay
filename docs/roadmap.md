# Roadmap

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

## 3. Hosting

Run the service and the analyzer worker on a host other than the user's own
machine. [deployment.md](deployment.md) lists what the current design already
keeps hostable and the four things that would have to change first: accounts
beyond one machine, an object store for uploads and job outputs, a queue that
outlives one process, and GPU workers addressed rather than spawned. No
provider is chosen yet.

## 4. Source-code hygiene

- Keep the analyzer package named `tracen_replay` under `analyzer/`; nothing
  in this roadmap renames it.
- Tests that currently skip without a locally preserved fixture should move
  to fixtures small enough to publish with the repository, or be marked
  explicitly as local-only so a clean checkout's skip count is expected
  rather than silent.
- Replace the per-rule edit-distance thresholds scattered across the
  analyzer's text-repair modules with one shared, vocabulary-based OCR
  repair, so every rule that corrects a misread name or word draws on the
  same word list and the same matching logic.
