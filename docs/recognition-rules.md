# Recognition rules

This document describes what the analyzer reads from a recording and what it
refuses to infer. The analyzer only ever looks at the isolated gameplay pane
of a 1920x1080 English recording: the crop `(148, 0, 958, 1080)`, an
810x1080 image. The side log panel lies entirely outside that crop; a
viewer can leave it open, but its pixels never reach the recognizer, and no
current module reads it.

Every observation below carries its own source timestamp, evidence image
path, and OCR confidence. The pipeline and report shape are described in
[analyzer-pipeline.md](analyzer-pipeline.md) and
[report-contract.md](report-contract.md); how observations become a turn
ledger is in [turn-ledger.md](turn-ledger.md). This document covers the
per-screen recognition rules that feed both.

## Calendar and stat bar

The current-turn label comes from a fixed box, matched against a pattern
such as `(Junior|Classic|Senior) Year (Pre-Debut|(Early|Late) <Month>)` or
`Finale Underway`, at confidence 95 or higher. Nothing else establishes turn
identity from this screen.

The six stat fields (speed, stamina, power, guts, wit, skill points) are
read from fixed columns in the current-stat bar. A geometry detector
requires a same-frame label, value and cap for all five stats plus a
skill-points value before it accepts the panel; a career/training header
is preferred but not required when the rest of the geometry is complete.
OCR can fuse a stray letter-like glyph onto a stat's leading digit (for
example `U1243`); the detector tolerates that shape only as row geometry,
at a lower confidence floor, never as the number itself, which is always
resolved from its own fixed crop. On a race day, the lower totals row can
supply the same six values from a separate layout, at confidence 97 or
higher, gated by an exact `Race!` control or an already-verified grid.

The five performance-point currencies (dance, passion, vocal, visual,
composure) are read from fixed boxes on the lesson menu and its
confirmation dialog. An empty balance renders as a dim grey `0`, accepted
at confidence 50 or higher; any other low-confidence reading in that slot
stays unknown rather than guessed. A wider full-counter crop is preferred
over a tighter legacy crop, so a truncated leading digit in the narrow one
cannot win.

The same sidebar stays on screen while a training result animates, and a
training can raise more than one of these currencies at once. Each row's
award has its own signed crop, requested on every result-grid frame rather
than only in the dense result inspection. It is used for one purpose: when
the shared detector merges a row's current value and its award into a single
low-confidence line (`58+26`), the signed crop supplies that award at
confidence 97 or higher, inside the row's own band, and only when its amount
agrees with the merged line. A disagreement stays a recorded conflict, and a
row the panel did not read at all is not awarded from the crop alone.

## Log panel

The log panel is out of scope by construction: it lies outside the
gameplay crop that is the analyzer's only input, and no current module
reads it. The full source screenshot is retained as evidence alongside the
gameplay crop, but the log's own pixels are never decoded and contribute
nothing to the report.

## Training preview and training result

A training preview is recognized by a fixed failure-rate banner drawn above
one of the five training tabs; it is recorded as a preview and never
treated as a completed action, regardless of how many frames repeat it.

A training result's outcome comes from the large `SUCCESS`/`FAILURE`
banner, read in a fixed position. An exact spelling at confidence 97 or
higher is accepted directly; a one-letter-off fuzzy reading needs
corroboration from at least two distinct timestamps and evidence paths,
including one exact spelling. The banner can also be clipped by one glyph
(`SUCCES`, `FAILUR`); that clipped prefix is accepted, at confidence 97 or
higher, only when the same frame also shows the complete result scaffold:
the `Training` header, both `Skip` and `Quick` controls, at least two
stat-card labels, and at least two `current/cap`-shaped totals. A clipped
`FAILURE` prefix always wins over a clipped `SUCCESS` prefix or any
competing failure evidence on the same frame.

Gain amounts are read as small "+N" badges beside each stat card and,
during the result animation, as large sweeping "+N" badges that cross the
card rows. A resting badge needs confidence 98 or higher to override a
competing crop; an animated badge needs confidence 95 or higher and is
recorded as an observation, only later attributed to a specific card, since
the animation moves it across the row.

A result total can be legible while its cap is still covered by the
settling animation, for example `576/13` for a true `576/1300`. That
partial reading is accepted at confidence 97 or higher and kept as a value
with an unknown cap, never as a value plus a guessed cap. It can
corroborate a claimed gain only when a later frame in the same training
result shows the complete, high-confidence ratio: at least three
observations across at least three distinct timestamps and 50ms of span.
The report keeps these partial readings separate from the accepted total.

A gain badge that is itself clipped to a leading digit or two (a `1` that
was really `11`) can be completed, but only through the turn ledger's own
arithmetic, not by guessing at pixels: when a training is the turn's only
action and the ledger's stat residual for that field is positive, the
badge is accepted as the leading digits of that residual, recorded with a
named basis (`sole_training_clipped_badge_prefix_completed_by_turn_residual`).
A field the training panel actually read is never eligible for this
completion. See [turn-ledger.md](turn-ledger.md) for the general
turn-difference rule this is one case of.

## Support events and dialogue choices

`choice_evidence` reads dialogue-choice evidence from the isolated
gameplay crop only. It finds white card interiors by pixel shape, reads
each card's text at confidence 97 or higher, and separately finds yellow
bilateral selection marks on both edges of the menu; a menu is offered
text only, and none of this identifies a selection by itself. A menu is
promoted to a committed choice only on an explicit visual witness: a
paired left/right selection mark aligned with exactly one option, or a
green selected-card fill that also causes the remaining white cards to
visibly collapse.

A repeated menu requires at least two distinct timestamps with matching
card text within 500ms of each other; a dense per-slot reading requires
two distinct timestamps at confidence 97 or higher for every visible slot
within 1500ms. An active menu expires after a 1500ms gap with no matching
observation, and a readable different response at the same screen height
starts a new menu rather than selecting a stale option from the old one. A
single-option response is a dialogue response; more than one option is a
dialogue choice. Selection time is the mark's observed timestamp, never a
click time, and no reward or downstream effect is an input to selection.

## Award popups and label tracking through animation

`award_tracking` associates a stat award with its field label across an
animation that separates the two. It requires two confident label reads
(confidence 97 or higher) of the same field, 50-250ms apart, with
stationary geometry, and every frame between those two anchors following
within 50ms of the last and showing the same label in the same place. At
least three distinct, confident "+N" badges spanning at least 50ms must
appear above the label, and a matching "went up by" caption must be
visible somewhere in the window. A cap caption, an ambiguous or
conflicting badge, a moved label, or a screen change all prevent the
association; the result still enters the ordinary receipt/animation
reconciliation rather than overriding it. A separate check accepts a
normal stat award and that stat's cap increase appearing together only
when both labels are confident, complete, and clearly separated, and the
same amount is independently corroborated by at least three repeated
ordinary receipts within 1000ms of the animation frame.

## Race results

Grade, placing, fan totals, course and condition are each read from their
own fixed box on the race-result screen: race name at confidence 95 or
higher, placing as an exact ordinal (`1st`..`99th`) at confidence 95 or
higher, fan totals from the `Fans N (+M)` text, course from a description
line at confidence 97 or higher, and course condition (`Firm`/`Good`/
`Heavy`) at confidence 97 or higher. Race grade is normalized to a fixed
set of values (`DEBUT`, `G1`, `G2`, `G3`, `OP`, `PRE-OP`, `EX`); anything
else is unresolved. Item reward quantities are read as `xN`/`×N` badges
beneath an `Items` heading, at confidence 97 or higher for both the
heading and each quantity, and assigned to an items or bonus section only
when the governing heading is legible (97 for Items, 90 for Bonus) and the
quantity sits directly below it, closely aligned with the other candidates
in its group; otherwise the section stays unresolved. Item identity is
never read, and reward completeness is always recorded as false.

The fan total commonly counts up on screen. A run of single-frame totals
is treated as one race when the gained-fans figure stays fixed and the
total only rises, with no more than 1000ms between frames; that counting
run is folded into the settled panel that follows once the panel repeats
the same total on two consecutive frames. A settled panel can also
reappear after up to 10000ms of unreadable frames and still count as the
same race. A result panel resumed after a canceled playback dialog stays
one race only when both sides repeat every identity field (name, placing,
fans, fans gained, course) and the source sequence through the dialog is
continuous, within 1000ms per step and a 500ms transition either side,
with the dialog itself lasting no more than 10000ms; item snapshots on
either side of the dialog stay separate and are never summed. A completion
animation's exact, large, centered ordinal reading can date an
already-identified race earlier than its detail panel, but only at
confidence 97 or higher, with at least two observations no more than
1000ms apart and no more than 10000ms before the panel; the animation
never creates a race or supplies its rewards on its own.

## Grand Concert lesson menu, confirmation dialog and receipts

The lesson menu shows offer cards with a title and five prices, one per
performance currency; it is recognized by its header and the visible
performance-currency prices themselves. A card is only usable once its
title is legible at confidence 97 or higher; each price needs confidence
97 or higher, except a dim-rendered `0`, accepted at confidence 50 or
higher. Any other low-confidence price stays unknown for that one field
without discarding the rest of the card.

A purchase request repeats the same card name across the confirmation
dialog before it commits. The frames between the confirmation and the
purchase form one request run: the name must repeat at two distinct
timestamps with distinct evidence before the request is usable at all, and
a single blank or unreadable frame in the middle does not end the run
early.

The cost is explained one of several ways, recorded as `cost_basis`: the
repeated request and the repeated menu-card price agreeing with each
other; a repeated balance pair 500ms apart on each side of the purchase;
only a later repeated panel confirming the drop; a matched but otherwise
unlabeled debit; only the request's own displayed number; or unresolved
when none of these apply. Every purchase also carries `debit_window_ms`,
the interval from the request timestamp to the first frame showing the
new balance (or the receipt time if none is observed): the accounted
moment the points left the balance, between pressing Learn and the
receipt appearing.

A song title is followed by a musical note glyph (♪) in its receipt. When
OCR drops that glyph, a separate check looks for the note's pixel shape
directly in the gameplay crop and restores it; since every threshold view
comes from one image, this restoration is never treated as an independent
observation on its own. Separately, the fixed phrase "Learned the song"
itself can be misread ("Learnd te ong ..."); the quoted title anchors the
line, and the prefix before it is accepted only within an edit distance of
5 from "learned the song". A receipt name that differs from the confirmed
name by one bounded OCR typo on its first word, or by exactly one trailing
unresolved character after an eight-character, two-word shared prefix,
can be retained as a variant of the same purchase; an unrelated name is
never folded in.

## Concert bonus panels

The Concert Info dialog's "Concert Bonus Changes" block lists three
bonuses (friendship training effectiveness, specialty priority,
support-chain event frequency), each as a current value and, when a
learned song will raise it, a next value, at confidence 85 or higher in
its fixed column. A bonus change is only recorded as an applied effect
once the same current/next pair has been read on two separate Concert
Info visits and the "Concert bonuses updated!" screen follows within 20
minutes; nothing is inferred from a song purchase alone.

The support-chain level is the one field prone to a `Lvl O` misread. The
parser deliberately does not map that letter to zero; it only accepts a
genuine digit. A separate rereading pass instead checks the same slot
across nearby frames and accepts a value only when it is numeric, has a
stable panel anchor, and agrees across a short contiguous run: confidence
90 or higher, at least 3 frames spanning at least 250ms with no gap over
1000ms between them, all within 1000ms of the target frame.

## Skill purchases and the owned-skill inventory on the final summary

Skill screens are classified from fixed phrases: a confirmation screen
from "learn the above skills", and a receipt screen from "skills learned"
or "trainee learned new skills". A receipt records that a batch was
acquired; it does not by itself name every purchased skill or give its
exact cost at the moment of recognition.

The final-summary skill panel is read separately from any purchase. It
requires the Skills, Inspiration and Career Info tabs together with at
least three recognized final attributes, and a two-column card grid at
confidence 95 or higher with a consistent row pitch; a wrapped card name
is joined across two OCR lines only when they share a left edge and a
small vertical gap. A name only enters the inventory once it has repeated
within 500ms of itself; a level or circle-suffix marker needs its own
repeat within 500ms to be marked verified, and conflicting readings are
kept rather than resolved. The circle-suffix detector itself requires at
least 3 of 5 thresholded pixel views to agree on the same marker shape
before reporting one. The report's inventory is always marked incomplete:
this reads the visible page only, never establishes scroll coverage, and
is explicitly separate from cart selections, committed purchase bundles
and inheritance sparks.

## Energy, mood and friendship receipts, outings and rests

Ordinary energy, mood and friendship effects come from the same fixed
receipt grammar as every other stat-change line ("Energy recovered by
N.", "Friendship with X went up by N.", "Friendship with X is maxed
out.", and so on), each requiring confidence 60 or higher (90 or higher
before a fixed-phrase repair is attempted). A separate channel,
`energy_popup.py`, reads the centered "+N Energy" popup that can remain
visible while the lower receipt is covered by an animation effect; it
requires confidence 97 or higher for both the amount and the exact
"Energy" label, aligned below and near the amount, and is suppressed
outright on preview and menu screens. A popup and a receipt with the same
amount are merged into one effect with both proofs attached; conflicting
amounts are kept as separate observations rather than reconciled.

Rest and outing confirmations are recognized from their fixed dialog text
("Take the day off to let your trainee recover energy?" / "Go on a fun
outing?", both paired with "entire turn"). A Rest is proved by the
confirmation, a repeated energy-recovery receipt within ten seconds of it,
and the next calendar date (or the next turn-phase countdown) read within
30 seconds of the result. Lessons, the concert and skill purchases spend
points rather than the turn, so when they follow the result the 30-second
wait starts from the last of those screens instead, and they are not
treated as competing actions; a training, race or outing screen in the
same stretch still voids the Rest. An Infirmary visit needs its
own confirmation phrase ("Visit the infirmary?" plus "this will take up
the entire turn."), a later named "At the Infirmary" result, and a
subsequent calendar or turn-phase advance observed within 30 seconds of
the result and within 30 seconds of each other; an explicit cancellation
word anywhere in the sequence voids it.

## What the analyzer does not read

A screen or line that matches none of the fixed classifiers, and none of
the fixed receipt patterns, is recorded as unknown and produces no effect.
In practice this means the analyzer does not model the supporter
deck-building or support-card selection screens, and it does not read
narrative or story dialogue that is not shaped like one of the fixed
receipt sentences above. The two supporter lines that are receipt-shaped,
"X joined your cause." and "New supporters joined!", are read as named
effects when they appear in the outcome band, but supporter identity, bond
growth and any other story content are not otherwise tracked. Anything
without a number or a named effect kind stays outside the report entirely;
it is neither counted nor flagged as missing.

## Review workflow

The client's Check tab is an index, not an editor: it lists every turn
with an accounting or observation caveat and every entry the analyzer
flagged, plus anything the report places outside every turn window.
Selecting a line opens that turn and seeks the recording to it.

The Turn pane shows one turn and, when the viewer opens it for editing,
builds a correction through a dedicated form. The service exposes this as
`GET`/`PUT`/`DELETE` on `/api/reports/{id}/turns/{turn}/correction` and
`GET` on `/api/reports/{id}/corrections`; all four return 404 when the
service runs without a correction store. A correction can name the turn's
action when the report has none (kind, training option or name, per-field
gains); attribute an amount to something no entry carries, with a channel
and a note; edit or delete one of the report's own entries, or mark it
reviewed without a change; or add an event the report is missing
entirely. Every read and write returns the server's own check of the
correction against the turn's observed opening and closing state, so the
client never has to recompute agreement itself. The rest of the review
UI, including how the Check tab and Turn pane fit into the client as a
whole, is covered in [local-app.md](local-app.md).
