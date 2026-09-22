# Evaluation

How the analyzer's output is checked today, and the learned readers meant to
replace its remaining hand-tuned OCR-repair heuristics: the first runs in the
analyzer behind a flag, the others are plans.

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

Every test in that suite runs on a clean checkout; none skips. The one test
that needs the installed OCR models (the reader's fingerprint) lives in
`analyzer/lab/tests` and runs only where the models are:

```
python -X utf8 -m unittest discover -s analyzer/lab/tests -t analyzer
```

Rule tests build a small fixture of
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
- The accounting's field counts across its statuses:
  `balanced_observations`, `balanced_with_derived_changes`,
  `unexplained_change`, `unresolved_attribution`, `missing_endpoint`,
  `not_yet_shown`, `career_end`.
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

One career has been checked by eye, category by category, against frames
cut from its recording (opening stat rows, training cards, outcome boxes,
races), with the report's values printed beside the pixels. The method is a
script over the report and the recording; the record of the first such
check is kept with the local evidence. It found the numbers right wherever
a person could see them and the misses in names read a glyph or two off,
which the accounting cannot see.

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

`analyzer/tools/build_reader_dataset.py` walks finished runs. A run's frames
are read from the images its readings point at, or, given `--video`, decoded
from its source recording at each reading's timestamp, which also covers a
run whose working data was pruned; the decoded frames land on the same
timestamps as the analyzer's own. The tool cuts every result box of every
training result frame and labels it by what it shows. The box is the
analyzer's own widened by 30 pixels on the left, 8 above and below and 6 on
the right: the analyzer's box starts exactly at a three-digit value's first
digit, so a four-digit value, the card's bounce or a zooming overlay pushes
digits outside it.

| Content | Rule | Target |
|---|---|---|
| `badge` | the reader's value equals the value before or after the training (a card shows either, depending on the moment) | `value/` (the value and the slash; the stat's cap after the slash is often half covered and the accounting never needs it), or `value` for skill points |
| `gain` | the reader's "+N" overlay equals the bracketed gain | `+N` |
| `blank` | the box holds no ink: under 0.2% of its pixels are dark and not blue | empty |
| `unknown` | anything else: the large animated digits, a covered badge, a value the reader missed | none |

Every box keeps its before, after and gain values and what the reader read,
so a reader can be judged on the unknown boxes too. The labels are checked by
reading random samples of fifty crops per label source against their labels
by eye, and the answer key the same way against held-out crops; the counts
are kept with the dataset in the local records. The lesson menu's
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
- **By two frames**: the same counts, but a card's value or gain counts only
  when two frames at least 200 ms apart read it alike, the way the analyzer
  settles a card from its rereads, and a value or gain two such frames agree
  on that the card cannot show is a wrong card. The gap matters: the
  analyzer rereads a card at 60 frames a second, and neighbouring frames
  catch the same moment of its animation, half-covered digits and all.

Run on the analyzer's own reads, it is the baseline table. Counter targets
come from the consensus of the reader's own visit, so the current reader's
counter numbers are complete by construction and measure nothing. The table
lives in the local records beside the dataset, never in the repository.

### First model: result box reader

Our own convolutional network, trained from nothing on those boxes. It takes
one box as RGB at 64×192. Two convolutions at each of the first two scales
resolve thin digits before the height is pooled. After four poolings the box
is 48 columns of 4 rows, and each column's rows are stacked rather than
averaged, so a small digit low in the box stays distinct from a tall one.
Two 1-D convolutions give each column its neighbours' context, and each
column is classified over the digits, `/`, `+` and a blank. CTC decoding
turns the columns into a transcription with a confidence per character: its
peak over the columns it spans. A read counts only in the shapes the game
renders (`value/` for a stat, anything after the slash ignored; a plain
value for skill points; `+N`; no leading zeros), and only when its least
certain character is at or above a confidence threshold.

`analyzer/tools/train_reader.py` trains it; it needs the `train` extra
(torch, torchvision, onnx) and runs on the GPU whenever PyTorch has CUDA,
which on Windows means installing torch from the PyTorch index for the
card's CUDA version rather than the default CPU wheel. On an RTX 5060 Ti
a round of twenty-five epochs takes a few minutes. Training uses the labeled
boxes of the training runs, at most eight crops per card box and target so a
card's dozens of rereads do not dominate, with badges drawn at twice the
share of gains and of blanks.

Training runs in rounds, because the labels above only reach boxes the
analyzer's own reader already read. After a round, the model scores every
unlabeled training box against the few texts its card allows: the value
before, the value after, the gain, or nothing. When one of them is at least
90% likely and a hundred times likelier than any other, the box is labeled
with it, and the next round retrains from nothing with those hard frames
added. The model never labels a box with a value the card could not show.
Most of these frames are clear badges the analyzer's rereads never recorded
a value for; looser picks also take frames whose number is only partly
visible, which teaches the model to fill in hidden digits and raises its
false reads.

The player's mouse rests on the card in many recordings, and the game's own
pointer, a bright lime arrow, then lies over a digit: a `+8` under it was
once read as `+9` at high confidence. The dataset flags every result box the
pointer lies over (`pointer`, from the lime pixels inside the box proper,
past the margin where a green stat icon can sit), such boxes keep their own
training bucket so the per-card cap never trades them for clean frames of
the same card, and the held-out judgement reports them as a scope of their
own beside all frames and pass frames.

Each round is judged on every box of the held-out runs, per run and over
all of them, beside the current reader and pooled with it, both on all
frames and on the ordinary pass's frames alone, which is what a reader
gets without the analyzer's high-rate rereads. Once a setting has been
judged, `--final` trains the same way on every run, held-out ones included,
for the model that ships; nothing is left to judge it on, so its accuracy is
the held-out result of the same setting. An ImageNet ResNet-18 trunk, frozen or
fine-tuned under the same head and data, can be trained as a comparison row;
it is never exported. The export is ONNX with a dynamic batch (input `crop`,
RGB in [0, 1]), checked against onnxruntime and timed per box on the CPU.
Results, the model and its log go to the local records.

### Integration: the reader in the analyzer

The exported model runs inside the analyzer behind `--learned-reader` (and
the service's `-learned-reader`). Its reads are observations on the training
result readings, never values of their own: the causal accounting uses one
only where it matches a difference the stat bars left unexplained, as the
gain itself or as the value the stat lands on with it, turning an
amount the accounting would otherwise work out from that difference into an
observed one (see [analyzer-pipeline.md](analyzer-pipeline.md)). Integration
is judged twice. The held-out model's reads are attached to the reports of
recorders it never trained on and their accounting is rebuilt with and
without them, counting unexplained, worked-out and observed fields; every
amount the reads confirm is then checked by eye against its frame. And a
fresh analysis of a held-out recording with the flag shows the whole path,
recognizer to report, holding together.

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

Results are not recorded here; they are kept with each model in the local
records.

### Second model: the recognizer's confusions

What it learns: how the recognizer damages names. Every rare spelling of a
supporter or skill name in a career (fewer sightings than make a name
known) is paired with the one known name of that run within three plain
edits, the same pairing the hand-set resolver made; the alignment of the
pair says, character by character, what happened: a match, a
substitution, a dropped letter, an added one. The counts over the fourteen
careers, smoothed, are the model: a probability for each substitution the
recognizer has been seen to make, for dropping each letter, for adding
one. `analyzer/lab/learn_confusions.py` writes them to
`tracen_replay/data/confusions.json`.

How it is used: the distance between a read spelling and a candidate
name is the cheapest alignment under those probabilities, in nats, so a
confusion the recognizer often makes costs little and one it never makes
costs a lot (`tracen_replay/confusions.py`). The resolver keeps its shape:
one candidate within the margin, under the limit, the same circle grade,
never a name read on the same frame. The limit and the margin are not set
by hand: the limit is the costliest training pair, the margin the median
cost of one confusion, both written into the table by the script. Without
a table the distance is unit edits and the limits the old constants.

How it is judged: leave one career out, learn from the other thirteen,
and count that career's rare spellings resolved by the hand-set rules and
by the learned resolver, and any spelling the two send to different names.
On 2026-09-20: 304 rare spellings; rules 20, learned 23, every one of the
rules' 20 among the learned 23, no disagreement. The three the rules
refused and the learned resolver accepts are right: "Symboli Ralf"
(Symboli Rudolf), "Subauea Front Kunners" (Subdued Front Runners),
"Light He!" (Light Hello); each is more than two edits but made of
confusions the recognizer is known for. Short names (six letters or
fewer) take a limit learned from the short pairs alone, as the hand-set
rule allowed them one edit instead of two; 27 pairs in all, 2 of them
short, so the table will sharpen as careers accumulate.

What stays hand-set: the field labels of the stat bar
(`current_state_layout`, two edits) and the receipt grammar's own
distances. They can move to the same table once their pairs are
collected the same way.
