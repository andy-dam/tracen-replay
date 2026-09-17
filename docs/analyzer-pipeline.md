# Analyzer pipeline

The analyzer is the Python package `tracen_replay` under `analyzer/`. The
service runs it as `python -m tracen_replay.analysis_job`, a thin worker
boundary described in [analysis-job.md](analysis-job.md). That boundary calls
`tracen_replay.full_recording`, which runs the stages below in order and
writes `report.json` and `timeline.json` into the run directory. This
document describes those stages and the rules the reconstruction step
applies. For the shape of the two output documents, see
[report-contract.md](report-contract.md); for the turn ledger specifically,
see [turn-ledger.md](turn-ledger.md).

Every stage after capture and OCR is either fatal (a bad report contract, a
missing base reading) or soft: a soft stage's failure is recorded on the
report under `stage_failures` with a traceback, and the run continues without
that stage's contribution.

## Capture

`full_recording.capture` requires an English 1920x1080 recording. It hashes
the source file (SHA-256) and decodes it through `ffmpeg` in 120-second parts
so a long recording can resume from `part-NNN/frames.json` if a prior attempt
was interrupted. Each part is decoded with an `ffmpeg select` filter that
keeps frames at least `1/fps` apart on the stream's own presentation
timestamps (`sampling.method: minimum_interval_on_decoded_pts`); it does not
resample onto an invented time grid, so a variable frame rate source yields
whatever original frames satisfy the spacing. The base rate is 1 to 8 FPS,
4 by default.

A frame's identity is its integer decoder PTS and time base, from which
`source_timestamp_ms` is computed exactly (`round(pts * numerator/denominator
* 1000)`); frames are rejected if that computed timestamp does not increase
strictly from one frame to the next. Separately, every decoded image is
content-hashed (SHA-256) once it is cropped to the gameplay pane; that hash,
together with the OCR engine's fingerprint, is what later stages use to
decide whether a cached OCR observation is still valid for a frame.
The decoder's `showinfo` log is the only source of those timestamps, and its line sequence must match the images written one for one; a part whose log comes out inconsistent is decoded once more from scratch before the run fails, and the failure names the offending frame index. `capture.json` is the immutable manifest of this stage: source identity,
duration, per-frame PTS/timestamp/evidence path, and the sampling
configuration. A second run against the same `--output` directory reuses it
after checking the source hash and requested FPS still match.

## OCR and base readings

Each frame's gameplay pane (a fixed 810x1080 crop at pixel region
`(148, 0, 958, 1080)` of the 1920x1080 frame) is read by a `NeuralReader`
built on RapidOCR 3.9.2 over ONNX Runtime, using the `PP-OCRv6_det_small`
text detector and the English `en_PP-OCRv5_rec_mobile` recognizer. Model
weights live under `--model-dir` (`.local/models/rapidocr` by default) and
are downloaded once; every later analysis run is fully local and offline.
The recognition device is not a CLI flag: `TRACEN_REPLAY_OCR_DEVICE`
(`auto`, `cpu`, `dml` or `cuda`) picks DirectML, then CUDA, then CPU; DirectML
and CUDA readers run one per OS process because their sessions are not safe
to share across threads, so the base OCR pass uses a process pool for those
devices and a thread pool otherwise. `--workers` (1 to 8, default 4) sizes
that pool.

A per-frame OCR result is cached under `neural/<frame id>.json`, keyed by the
OCR engine's fingerprint and the gameplay crop's content hash; `--reparse-only`
reinterprets those cached rows with the current parser instead of rerunning
OCR. Every reading carries a `screen` classification (`training_result`,
`training_preview`, `event_outcome`, `lesson_selection`, `skill_selection`,
`race_result`, `concert_confirmation`, `concert_result_candidate`,
`rest_confirmation`, `infirmary_confirmation`, `skill_confirmation`,
`skill_receipt`, `lesson_confirmation`, `outing_confirmation`,
`outing_selection`, `concert_bonus_update`, `concert_info`,
`career_completion_hub`, `career_summary`, `career_finish_confirmation`,
`career_account_totals`, `playback_confirmation`, `training_result_candidate`,
or `unknown` when nothing matched), a `stats` block, a `facts` dict of
whatever that screen's fields were actually read on that frame, the raw OCR
lines under `ocr`, and provenance (`source_frame_sha256`, `engine_fingerprint`,
`model_sha256`, `gameplay_sha256`). A field the frame did not show, or showed
below the parser's confidence floor, is absent or `null`; it is never filled
from a neighboring frame or a balance.

## Refinement passes

After the base pass, `automatic_refinement` discovers weak observations
already present in the cached readings and asks four existing refinement
readers to reread the *same already-captured* source frame at higher
precision (never a new video decode). It is bounded by
`--max-auto-refinement-frames` and `--max-status-refinement-frames`
(512 each by default). A few other refinements are generated as their own
stage, or applied opportunistically while a frame's receipt is parsed. None
of them consult a balance, a neighboring frame, or an expected value; each
either promotes a reread that agrees with fresh pixel evidence or leaves the
field unknown.

| Module | Rereads | Applied |
| --- | --- | --- |
| `automatic_refinement` | orchestrates the four rows below over already-cached frames | its own stage, before reload |
| `weak_state_recovery` | any same-frame field below the normal confidence floor, from bounded crop candidates | via `automatic_refinement`, then loaded as an early sidecar |
| `numeric_cap_refinement` | visible stat/performance caps and clipped current/cap ratios | via `automatic_refinement`, then loaded as an early sidecar |
| `performance_panel_refinement` | the current/projected performance panel when its merged line is weak but two localized crops are readable | via `automatic_refinement`, then loaded as an early sidecar |
| `status_badge_refinement` | a mood/hype badge below the 97% status threshold, rereading the same crop in three contrast views | via `automatic_refinement`, then loaded as an early sidecar |
| `base_receipt_refinement` | a base receipt line, using three correlated OCR views of the same row | applied while loading cached readings |
| `race_identity_refinement` | race-result fields (name, course, grade, placing) that did not change, via a source-bound crop reread | applied while loading cached readings |
| `race_quantity_refinement` | quantities on race-result item badges, from fixed badge regions at two source timestamps | its own stage (fresh runs only) |
| `song_symbols` / `song_symbol_refinement` / `song_star_refinement` | a dropped or misread song-title glyph (music note or outlined star) in a wrapped or single-line song receipt | applied inline while parsing a song receipt, or as cached sidecars |
| `lesson_offer_refinement` (via `lesson_offer_adapter`) | the five price slots on a visible Lessons-menu card | applied inline for `lesson_selection` frames, or as a cached sidecar |
| `concert_panel_refinement` | the Concert Info support-chain frequency level slot | applied while loading cached readings |
| `choice_card_refinement` / `refine_choices` | a weak dialogue-choice card at native size | applied while loading cached readings |
| `refine_inventory` | the final skill panel's card list | applied while loading cached readings |
| `refine_currencies` / `refine_currency_padding` | every lesson-balance slot, read again by the recognizer from a wide fixed crop (the detector's box can start on the currency label or lose a digit to the cursor), then two padded crops for a slot that is still unread | its own stage on fresh runs (`currency_refinement`), between the race quantity stage and the readings reload; the sidecars are then applied while loading cached readings |
| skill-point crop refinements (`skill-points-refinement`, `skill-variants`) | skill-point counters clipped by neighboring badges or missed by text detection | applied while loading cached readings |

Hint-card identity preparation (`hint_card_cache` / `hint_card_identity`) runs
separately, after the readings are reloaded: when a cursor obscures a hint
receipt's skill name, it can establish the identity from repeated readable
hint cards elsewhere in the same event, bound to the same event context,
geometry, and a receipt-prefix reread; it never consults a skill catalog.

## Bounded rereads

Six modules request a small, source-bound higher-rate decode of a specific
window when the base pass left something unresolved. Each is a *reread*: it
can only add observations tied to newly decoded frames of the same source; it
never derives a value from a state difference or a neighboring event. Each
has its own frame-rate and budget, spent across the whole run:

| Module | Requested when | Rate | Budget |
| --- | --- | --- | --- |
| `training_gain_recovery` | a training result has conflicting gain digits, a source-bound candidate that was not accepted, a gain read on exactly one result frame, or a committed result with no signed gain read at all | 60 fps | up to 96 windows and 120 s of source |
| `receipt_recovery` (numeric receipt recovery) | an ordinary receipt caption names a field but its number is incomplete | 30 fps | up to 20 windows and 30 s of source |
| `occluded_receipt_recovery` | a cursor or particle crosses a receipt line for friendship, hints, conditions, songs, or another effect | 16 fps | up to 128 windows and 240 s of source |
| `boundary_state_recovery` | the turn ledger has no stats/performance opening for a turn, and an existing reading proves that panel was visible before the committed action | 60 fps | up to 12 windows, each padded 200 ms, totalling 6 s |
| `weak_state_recovery` | (same-frame, no new decode) a field on an already-captured frame is missing or below the confidence floor | n/a | up to 16 crop requests per frame |
| `preview_recovery` | (same-frame, no new decode) a translucent Grand Live training-preview row overlaps a Concert Bonuses row in one detector box | n/a | up to 16 crop requests per frame |

`training_inspection.json`, `native-inspection.json` and
`receipt-inspection.json` (30 fps result-layout inspection, 60 fps unresolved
training-animation inspection, and bounded receipt review respectively) are
the manual counterparts of the same reread mechanism, merged into the
readings before the automated recovery passes run.

## Assembly

`full_recording.assemble` builds `gameplay_tracking`: the final `readings`
list, several source-projected observation lists (`preview_observations`,
`status_observations`, `state_observations`, `skill_menu_observations`), then
calls `transactions.reconstruct` to turn readings into events and derived
collections. `reconstruct` returns `checkpoints`, `events`, `intervals`,
`dialogue_choices`, `fan_accounting`, `song_acquisitions`,
`unparsed_receipt_candidates`, `lesson_purchases`, `skill_purchases`,
`concerts`, `races`, `turn_action_receipts`, `performance_accounting`,
`lesson_debit_observations`, and (when hint-card evidence exists)
`hint_card_recovery`. Assembly also computes `owned_skill_inventory` and, from
the assembled report, the turn ledger and causal accounting projections
described below.

### Rules by screen

**Purchases.** A lesson purchase is committed only when a `lesson_selection`
request is joined to a matching `named_acquisition`/`song_learned` receipt; a
canceled confirmation creates no purchase, and the offered bonus shown on the
menu card is kept separate from the receipt's actual stat gain. A skill
purchase batch requires a confirmed acquisition receipt plus independently
observed skill-point balances before and after; the Learn menu's displayed
counter is a projected balance, so selecting, removing and reselecting an
item never creates multiple purchases by itself. When several purchases share
one before/after checkpoint pair, each still needs its own confirmation,
receipt and repeated final projected counter, and the last counter must match
the observed settled balance, or the group is left as projected rather than
verified.

**Receipts and captions.** A story caption read whole, and read again with
its tail cut off or with one or two misread letters, is one outcome, not
two: outcome grouping stays open across an unreadable frame for up to 500 ms
and matches captions by a fuzzy same-caption check rather than exact text. A
frame with no receipt and no pending caption only closes the current outcome
when it carries real narrative content, a different context title, or an
unrelated screen; an OCR gap that repeats the same named hint amount, or a
plausible but ungrammatical friendship line, keeps the outcome open without
being accepted as its own effect. A stat receipt that such a gap split into
two outcomes counts once (`receipt_stat_continuity`) when consecutive
samples show the same line in the same place throughout and the frames
between show why they parsed nothing: the line cut short, or read whole
under the confidence a receipt parse needs. The later outcome keeps its other
effects and records the dropped repeat under `deduplicated_receipt_effects`.
A zero balance drawn dim reads as a lone
low-confidence single-digit `0` in a currency slot and is accepted as zero
(`dim_zero_currency_fields`); any other low-confidence card price stays
unknown. A receipt whose every OCR spelling is a bounded variant of the one
name a request dialog showed is that item; an extra word or a digit is a
different item, and a song title proven by its note glyph is kept over a
stray-letter misreading of the same title. Receipt lines whose fixed wording
was corrupted by an on-screen cursor (`"Speed went uply 30."`,
`"Learnd te ong ..."`) are repaired by anchored template or bounded edit
distance against the known phrase; the subject and the amount are never
changed, and a missing amount is not repaired.

**Result banners and rereads.** A training's identity heading counts on
result frames even when a level digit was not read. A result word missing
its final glyph (`SUCCES`, `FAILUR`) is accepted as that word when the same
frame also proves the result panel; a failed training's displayed
performance projections are recorded as `unawarded_performance_projection`
and never applied as a gain. A completed training becomes the turn's action
on one of four bases, recorded as `identity_basis`: `observed_gains` (its
stat gains were read), `repeated_result_frames` (its result card was sampled
on two or more frames), `training_banner` (its animation banner, heading plus
name, was read on three or more consecutive frames leading into a result),
or `repeated_training_name` (only the card's own name was read, on two
distinct frames). A gain read on exactly one result frame that the event did
not accept, or a committed result with no signed gain read at all, requests
the bounded `training_gain_recovery` reread described above rather than
letting the accounting work the amount out from a state difference.

**Races.** Consecutive `race_result` frames with the same fan total and fan
gain form one race group; a fan total still counting up across the first few
frames folds into the settled panel that follows it, and the counting frames
are kept separately as `fan_counter_observations` instead of becoming
conflicting fan readings. A falling total, a different gain, or a pause of
more than a second starts a new race, with one narrow exception: a panel read
again within ten seconds, with the same race name, settled total and gain,
after frames the classifier could not read, is the same panel returning after
a dialog, because a fan total cannot repeat after another race. Placing,
course, fans and item rewards are tracked separately from character stats;
scrolling item lists are only summarized from stable visible snapshots, never
summed while animating.

**Songs.** A `song_learned` effect is linked to a lesson purchase on the same
event id when one exists (`acquisition: paid_lesson`); otherwise it is a
`story_event_receipt` when the event carries a context title, or
`unknown`. Multiple OCR spellings of the same acquisition are kept as
`observed_name_candidates` with `name_conflicted` true rather than silently
corrected against a song catalog; a symbol-refinement pass may still recover
a genuine terminal note or star glyph from source pixels, as covered above.

**Events.** An `outcome` event's boundary is a screen change, an unrelated
context title, real narrative dialogue, or a gap of more than 500 ms with
nothing recognizable in between; wrapped or wide-crop text that merely
repeats the same receipt does not start a new event. Hint awards
(`skill_hint_change`) get their own timeline entries with their first linked
receipt time; ambiguous effect candidates (uncertain hint names, competing
readings) are kept visible with `accepted_award: false` rather than counted. A receipt-shaped line the parser rejected is listed under `unparsed_receipt_candidates`; when it is a cut-short prefix, a bounded misread of about the same length (same subject, same digits, a couple of edits per dozen characters), or a word-by-word reading that may stop early (each word within a couple of edits, every number exactly as the receipt shows it) of a receipt parsed within five seconds, or of a longer unparsed line beside it, it is marked `ocr_fragment` with the line it repeats and is neither a timeline entry nor a review item. A rejected line from a family the ledger does not account for (friendship, a supporter joining, a supporter appearing in training) is marked `out_of_scope` and likewise kept without a timeline entry or a review item.

## Turn ledger

`turn_ledger.build` joins the assembled events, transactions and checkpoints
into turn windows from repeated calendar or countdown observations, without
inventing a boundary or an action. See [turn-ledger.md](turn-ledger.md) for
the full rules; in short, each window gets an `action_status`
(`one_action`, `missing_action`, `multiple_actions`), an opening state per
channel when one was observed, and every timeline entry that falls inside it.

## Causal accounting

`causal_accounting.build` re-derives, from the same collections, a set of
per-field `contributions` (one row per effect, gain, or debit, each with a
`basis` such as `observed_receipt`, `observed_training_gain`,
`committed_skill_debit`, `state_derived`, `state_constrained`, or
`summary_only`), checkpoint-to-checkpoint `comparisons`, and turn-to-turn
`turn_transitions`. Every field comparison gets one status:
`balanced_observations` (only directly observed contributions), 
`balanced_with_derived_changes` (some contributions were derived rather than
directly observed), `unexplained_change` (a nonzero residual remains),
`unresolved_attribution` (a contribution is ambiguous, unobserved, or
conflicted), or `missing_endpoint` (one side of the comparison was never
observed). A turn transition whose two endpoints exist but are out of time
order instead gets `unordered_endpoints`. `turn_transitions` also carry
`transition_kind` (`between_turn_openings` or `terminal_observation_window`)
and per-field `endpoint_availability`, and, for the last turn, a
`terminal_observation` naming later contributions the closing snapshot does
not itself cover.

The one place the accounting extrapolates beyond a direct reading is the
**turn difference owner** rule, flagged with `basis: turn_difference`: inside
one turn window, a field's remaining unexplained difference is assigned to
its *only possible* owner. The candidates, in the order the code checks
them, are:

- the turn's sole training whose reading of the field is unread or itself
  conflicted (`sole_training_takes_turn_residual`);
- that same sole training when it *did* read a badge, but the read digits are
  the leading digits of the completed value (a `1` that was really `11`), or
  a conflicting frame showed a candidate matching the difference
  (`sole_training_clipped_badge_prefix_completed_by_turn_residual` /
  `sole_training_visible_candidate_completed_by_turn_residual`); these keep
  the badge's `read_amount` alongside the completed amount;
- the one outcome whose receipt named the field but lost its number, where
  the surviving digits (after `by`) are a prefix of the difference and the
  caption's up/down direction agrees (`sole_number_cut_receipt_takes_turn_residual`);
- for a negative performance difference, the one lesson bought in the window
  whose cost was never observed (`sole_unpriced_lesson_takes_turn_residual`);
- for a negative skill-point difference, the one skill batch committed in the
  window whose charge was never read
  (`sole_unpriced_skill_batch_takes_turn_residual`); such a batch is also
  named by an `unobserved_purchase_debit` issue whether or not it takes the
  difference;
- for a positive skill-point difference in a turn that contains a race, the
  turn's one race, but only when no training with unread skill points could
  also have paid them (`sole_race_takes_skill_point_residual`). A race never
  charges skill points, so a negative difference on a race turn is left to
  the owners above.

Two possible owners, an ambiguous contribution, or a caption whose remaining
digits contradict the difference leave the difference open with its time
window instead. Every assignment is stored both on the owner record (under
`turn_difference_gains`, `turn_difference_performance_gains`,
`turn_difference_cost`, `turn_difference_captions`, or
`turn_difference_completions`, plus `turn_difference_basis`) and as its own
flagged contribution, so a consumer can always show it as worked out from the
difference between turns, replaceable by a viewer.

A worked-out amount whose own training card the learned reader read as a
different gain is recorded on the training as `contradicted_turn_difference`
with those reads and their frames, raised as a
`worked_out_amount_contradicted_by_card` accounting issue, and listed in the
review queue. The amount does not change: the stat bars decide it, and a read
cut to its leading digits disagrees with nothing.

**The learned reader.** A run given `--learned-reader` runs the exported
result-card model (`learned_reader`) on every training result frame,
including one whose banner stayed too faint to confirm the screen and left it
a `training_result_candidate`, since the card sits in the same place either
way. It stores its reads on the reading as `facts.learned_result_reads`: the model's
fingerprint, the confidence threshold, and per stat box the transcription,
its least certain character, and the value or gain it reads. The report
records the model under `learned_reader`. The reads never make a difference;
they only match one. A stat's difference that the sole training would take
under the rule above becomes an observed amount (basis
`observed_learned_training_gain`, stored on the training under
`learned_reader_gains` with the frames under `learned_reader_frames`) when,
on the training's own result frames, the model read exactly that gain (for a
clipped badge, the read digits completed by the difference) or the value the
stat lands on with it: the turn's opening value, plus every amount counted
before the card, plus the gain. A value counts only when it is the last value
read on the card, because the badge counts up to it, and never when the
model read a different gain on the same card; a zoomed gain cut to its
leading digits is not different. A match by value is also stored under
`learned_reader_values`. Two cases the recognizer alone leaves
open are also closed that way: a panel the recognizer read without the stat,
and a skill-point difference in a turn that also has a race, which the
training takes instead of the race when the model saw that many skill points
on its card.

## Timeline document export

`timeline_document.write` derives `timeline.json`
(schema `tracen-replay/timeline-v1`) from the finished report: it is a strict
reshaping of fields the report already states, nothing computed anew. It
drops every image path, OCR text dump and pixel-hash proof, keeping only
source timestamps, so a frame can always be re-extracted from the original
video but the document itself never references one. See
[report-contract.md](report-contract.md) for its full field layout.

## Evidence policy

Every value in a report or the timeline carries the source frame(s) and
timestamp(s) it was read from; a value with no frame does not exist. A lone
low-confidence single-digit `0` in a fixed currency slot is accepted as a
dim zero; any other low-confidence reading is left unknown rather than
guessed. Establishing a repeated fact (an opening state from readings, a
race panel, a purchase's settled balance) generally requires at least two
distinct source timestamps that agree; a singleton reading is not a
checkpoint. When frames disagree, the disagreement is kept as a conflict
(`conflicting_readings`, `conflicts_present`, `name_conflicted`,
`ambiguous_contributions`) rather than resolved by preference, and a
conflicted field cannot be assigned as a turn-difference owner. Outside the
one flagged turn-difference-owner rule described above, the analyzer never
fills in a number it did not read from a state difference, a balance, or a
neighboring event.
