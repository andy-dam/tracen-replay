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

The causal accounting is a source of labels without hand annotation. Every
field the accounting marks `balanced_observations` or
`balanced_with_derived_changes` is a crop with a value confirmed by the
turn-to-turn arithmetic. Every field marked `unexplained_change` or
`unresolved_attribution` is a labeled hard case. Every one of those has an
evidence path: a frame and a timestamp the report already carries. A dataset
for a crop reader can be built by walking reports for these labels, not by
having a person look at frames and type in values.

### First model: badge and counter reader

A small CNN over the fixed stat-badge and resource-counter crops, predicting
a digit string with a confidence. Compare it against three conditions on the
same crops and labels:

| Condition | Method |
|---|---|
| Baseline | The current OCR reader (RapidOCR) |
| Frozen backbone | A pretrained small vision backbone with a trained linear head |
| Fine-tuned backbone | The same backbone and head, unfrozen and adapted |

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
