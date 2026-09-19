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

Everything read today assumes one layout. A recording must be exactly
1920x1080 or the capture stage refuses it, the game pane is the constant crop
`[148, 0, 958, 1080]`, and every later read is expressed in that pane's
810x1080 coordinates: the performance rows, the five result badge boxes, the
receipt band, the learned reader's crops. The detector finds text anywhere,
but which row a number belongs to is geometry. A recording from a phone, a
differently sized window or a non-English client is therefore refused with an
explicit error rather than read wrongly, which is the right default and also
the reason none of them can be analyzed.

How much work another layout is depends on one measurement: whether its game
area has the same proportions as the pane above.

- **Same proportions**, as a capture that pillarboxes the game or an emulator
  at that ratio would give: locate the game area in each recording instead of
  assuming the constant, then scale it to 810x1080. Every fixed box survives,
  because they all live in pane coordinates, so one mapping carries the whole
  pipeline.
- **A phone's own proportions**: the game reflows its layout, elements
  anchored to the top and bottom move apart, and no single scale maps the
  boxes. That needs geometry per layout, or reads anchored to landmarks the
  frame itself shows (row labels, panel edges) instead of constants. The fixed
  geometry is load-bearing in the reader, the occlusion gate and the evidence
  proofs, and the learned reader's boxes were cut at those exact coordinates,
  so its dataset would have to be re-cut or the model retrained.

The first step is a measurement, not a design: ten seconds captured on the
device answers which of the two it is. A non-English client is the same class
of problem one layer up, where the fixed words a receipt is repaired against
are English.

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
