# Evaluation

How the analyzer's output is checked today, and the plan for the learned
readers that will replace its remaining hand-tuned OCR-repair heuristics.

## Part A: how the analyzer is validated

### Sealed code under test

`analyzer/tools/snapshot_analyzer_implementation.py` copies `tracen_replay`,
`tools`, `lab` and `pyproject.toml` into a snapshot directory together with a
`code-manifest.json` listing every file's SHA-256 and an overall manifest
hash. `verify()` re-hashes the working tree and the copied files against that
manifest and fails if either has changed. Recording a run's manifest hash
alongside its report ties that report to the exact code that produced it, so
two reports can be compared knowing whether the code changed between them.

### Unit tests

Run the suite from the repository root:

```
python -X utf8 -m unittest discover -s analyzer/tests -t analyzer
```

Tests that depend on locally preserved fixtures (frozen source caches,
prepared recording sidecars, installed OCR models) call `self.skipTest(...)`
or `@unittest.skipUnless(...)` when those fixtures are not present, so the
suite still passes on a clean checkout. Rule tests build a small fixture of
frame readings, checkpoints and events, then assert what the rule under test
does with them; the aim is one focused test per rule rather than one test
that exercises a whole report.

### Fresh runs as acceptance evidence

The acceptance evidence for a change is a fresh, full run of `analysis_job`
on a recording that was held out from developing the change, and recordings
from more than one recorder are used so a rule is not tuned and graded on the
same source. A recording used to tune a rule is never counted as evidence for
that rule.

`full_recording --reparse-only` rebuilds a report from a preserved run's
cached OCR observations without starting a new OCR pass. It is the declared
way to retest a parser or accounting change: the two reports are compared
directly, field by field. It cannot exercise a new 60 fps reread window,
because that reread only runs against the source video, so a change to
reread window selection or its OCR is judged only on a fresh run.

### What is compared between two reports

- Event counts, grouped by kind and timestamp.
- Lesson purchases and their prices.
- Songs, races, skill purchases and concerts.
- Dialogue choices.
- The accounting's field counts across its five statuses:
  `balanced_observations`, `balanced_with_derived_changes`,
  `unexplained_change`, `unresolved_attribution`, `missing_endpoint`.
- The turn ledger's action status per turn (`one_action`, `missing_action`,
  `multiple_actions`).

A change is accepted only when every difference between the two reports is
explained by the rule that changed. An unexplained difference blocks
acceptance until it is understood.

### The accounting as the measure

Every turn's opening stats plus its entries must equal the next turn's
opening stats. The counts of `unexplained_change` and
`unresolved_attribution` fields, of `missing_action` and `multiple_actions`
turns, and of unpriced purchases are the analyzer's reliability numbers, per
recording. This document does not restate any of those counts; read them
from the run's own report.

### Human verification

The review layer lets a viewer correct a turn's action and its entries and
add missed events. Corrections are stored per viewer, separately from the
report, and on every read the service checks them against the turn's
observed endpoints: opening stats plus what the report explains plus the
viewer's edits must equal the next turn's observed opening stats. See
[local-app.md](local-app.md) for the review editor and its routes.

## Part B: learned readers

### Why

The rules already decide which screen is on frame and what an event means
from OCR text and layout; that decision is not the source of the analyzer's
remaining errors. The errors that remain are reading errors on small, fixed
crops: a stat badge misread or clipped at its edge, a zero drawn too dim to
detect, a counter hidden behind the player's cursor, the song note glyph that
marks a song receipt, and receipt words mangled by compression or motion
blur. A learned reader targeted at exactly those crops is a narrower, more
tractable model than a screen classifier, and it plugs into the same
evidence-and-accounting structure the analyzer already has.

### The accounting as a label source

The analyzer's own evidence labels the crops a reader sees, without hand
annotation. A training result card sits between two stat bars the run
observed, the one before the training and the one after it. With the stat
receipts read in between, those bars give each stat's value before the
training, its value after, and the gain, none of which depends on what the
reader made of the card itself.

`analyzer/tools/build_reader_dataset.py` walks run roots that still hold
their frames: the ordinary pass's gameplay panes and the card's high-rate
rereads. A pruned run gets its panes back with `--reparse-only
--rehydrate-frames`. The tool cuts every result box of every training result
frame at the fixed box the reader uses and labels it by what it shows:

| Content | Rule | Target |
|---|---|---|
| `badge` | the reader's value equals the value before or after the training (a card shows either, depending on the moment) and, for a stat, the same cap is read on at least three frames within three minutes | `value/cap`, or `value` for skill points |
| `gain` | the reader's "+N" overlay equals the bracketed gain | `+N` |
| `blank` | the box holds no ink: under 0.2% of its pixels are dark and not blue | empty |
| `unknown` | anything else: the large animated digits, a covered badge, a value the reader missed | none |

Every box keeps its before, after and gain values and what the reader read,
so a reader can be judged on the unknown boxes too. The lesson menu's
performance counters are a second kind, whose target is the consensus of the
menu visit the frame belongs to. Runs are the unit of splitting: `--holdout`
names runs that never feed training and `--group` tags each with its
recorder, both recorded in `manifest.json` beside the counts per split;
`crops.jsonl` carries one row per box.

`analyzer/tools/reader_baseline.py` judges a reader the way the accounting
uses one. It works per card rather than per frame, because a card is sampled
on several frames and most of them show the box blank or covered while the
card animates:

- **Value read**: the card has a frame yielding the stat's value before or
  after the training.
- **Gain recovered**: for a card whose training raised the stat, a frame
  yields the gain, or the value after (the stat bar before supplies the
  rest).
- **False values and false gains**: frames whose value is neither before,
  after nor a count-up between them, or whose gain is not the card's. Any
  read of a blank box is false.

Run on the analyzer's own reads, it is the baseline table. Counter targets
come from the consensus of the reader's own visit, so the current reader's
counter numbers are complete by construction and measure nothing. The table
lives in the local records beside the dataset, never in the repository.

### First model: result box reader

Our own convolutional network, trained from nothing on those boxes. It takes
one box as RGB at 64×192. Two convolutions at each of the first two scales
resolve the cap's thin digits before the height is pooled. After four
poolings the box is 48 columns of 4 rows, and each column's rows are stacked
rather than averaged, so a small digit low in the box stays distinct from a
tall one. Two 1-D convolutions give each column its neighbours' context, and
each column is classified over the digits, `/`, `+` and a blank. CTC
decoding turns the columns into a transcription with a confidence per
character: its peak over the columns it spans. A read counts only in the
shapes the analyzer's own reader accepts (`value/cap` with the value not
above the cap, a plain value for skill points, `+N`), and only when the
least certain character the accounting uses (a stat's value digits, not its
cap) is at or above a confidence threshold.

`analyzer/tools/train_reader.py` trains it; it needs the `train` extra
(torch, torchvision, onnx) and runs on the GPU whenever PyTorch has CUDA,
which on Windows means installing torch from the PyTorch index for the
card's CUDA version rather than the default CPU wheel. On an RTX 5060 Ti
twenty-five epochs take under a minute and a half. Training uses the labeled boxes of the training
runs, at most eight crops per card box and target so a card's dozens of
rereads do not dominate, with badges drawn at twice the share of gains and
of blanks. The model is judged on every box of the held-out run, beside the
current reader and pooled with it. An ImageNet ResNet-18 trunk, frozen or
fine-tuned under the same head and data, can be trained as a comparison row;
it is never exported. The export is ONNX with a dynamic batch (input `crop`,
RGB in [0, 1]), checked against onnxruntime and timed per box on the CPU.
Results, the model and its log go to the local records.

### Second model: confusion-aware text repair

A model trained on receipt lines paired with their resolved names, learning
which character confusions the recognizer actually makes instead of the
fixed edit-distance thresholds each rule sets by hand today (see
[roadmap.md](roadmap.md) for the related plan to unify those thresholds into
one shared vocabulary repair).

### Later: boundary and animation-state detection

Using the current rules' boundary and animation-state decisions as labels,
once the two reader models above are in place.

### Splits and integration

Splits are grouped by recording and by recorder, the same grouping the
accounting evidence already carries; a recording held out for testing is
never used to pick a threshold. A model is one more reader: it yields an
observation with a frame, a value and a confidence, exactly like an OCR
reading does today. The causal accounting stays the arbiter of what is
accepted; a model's output never fills a report field on its own.

### Metrics

- Fewer `unexplained_change` and `unresolved_attribution` fields on held-out
  recordings, compared to the current OCR baseline on the same recordings.
- Per-field accepted-read accuracy and coverage (the accounting's balanced
  fields as ground truth).
- CPU inference latency per crop, since the worker runs without a GPU
  guarantee (see [ocr-performance.md](ocr-performance.md)).

No results are reported here until a model exists; this section is a plan,
not a record of a run.
