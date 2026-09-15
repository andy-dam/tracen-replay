# The turn ledger

`turn_ledger.build` (`analyzer/tracen_replay/turn_ledger.py`) is a
reproducible projection over a finished report's `gameplay_tracking`
collections. It groups the readings into turn windows, assigns every
timeline-worthy fact to the window that contains it, and records each
window's committed action. It does not rerun OCR, invent a boundary state,
or invent an award: every field it emits is a JSON pointer into a collection
the report already has, or a value copied from one. `report_contract.validate`
recomputes this projection and rejects a report whose stored `turn_ledger`
does not match it exactly.

A turn ledger requires `gameplay_tracking.auxiliary_log_used` to be `false`;
it is built only from `readings`, never from a legacy auxiliary log.

## What a turn window is

A window is not one calendar day; it is the source interval between two
successive confirmed calendar or countdown observations
(`turn_ledger._windows`). Three kinds of window exist, distinguished by
`window_kind`:

- **`calendar_turn`**: a dated half-month turn, identified by its calendar
  text (`date_key`).
- **`countdown_segment`**: a pre-debut phase identified only by its "N turns
  to goal" countdown, when the calendar text itself does not carry a date.
  A phase whose countdown was never read stays an **`unresolved_phase`**
  instead of being split or numbered by guesswork. The exception is the
  phase's opening frames: when the phase is read before its first countdown
  is legible and nothing is played before that countdown appears, those
  frames are the start of the first countdown turn, not a window of their
  own.
- **`phase_race_turn`**: a window whose scheduled race, not the player, ends
  it (see below).

A window needs at least two distinct observed timestamps carrying its
calendar/countdown identity to be confirmed; a single observation is
rejected as `single_calendar_observation` and stays visible in
`calendar_issues`, not as a turn. A countdown or date reading that
contradicts both of its neighbors (11, then 0, then 10) is rejected as
`calendar_transient_misread` instead of splitting the turn; its neighbors
are rejoined when they carry the same value. A newly observed phase closes
the previous dated turn even before its own countdown is readable; a missing
countdown inside an already-numbered phase is not a reset.

## The goal race rule

The finale keeps one calendar label and one countdown across all three of
its turns (train, then the Qualifier; train, then the Semifinal; train, then
the Finals). There, each finale race ends its own turn instead of the
calendar: the next home observation after a `race_result` screen opens the
next window (`boundary_basis: finale_race_advance`). Such a window is
`window_kind: phase_race_turn`, labeled by the race that ends it, and carries
`scheduled_race`; the race itself is counted under `scheduled_race_actions`,
not as the turn's decision, so a trained finale turn still shows
`action_status: one_action`. The screens after the Finals keep the finale
label but offer no training menu, so they stay inside the Finals window
instead of becoming a turn of their own.

The last pre-debut countdown turn ("1 turns to goal") works the same way,
with one added condition: its window's race becomes `scheduled_race` (and
the window `phase_race_turn`) only when that window *also* holds the
player's decision, i.e. the countdown was confirmed before or alongside the
training action. If the countdown was confirmed only after the training
(the race is the first calendar-bearing thing in the window), the race
stands alone in the window and is that window's one action, not a scheduled
race running alongside a separate decision. This resolution happens after
every window's actions are known (`turn_ledger.build`, the final loop over
`turns`): a window whose `goal_race` was tentatively set is only promoted to
`phase_race_turn` when it also contains a non-race action.

## Assigning entries to turns

Every event, committed action, transaction, unparsed receipt candidate and
ambiguous effect candidate becomes one timeline entry via `turn_ledger.add`.
An entry's `assignment_basis` explains how it landed on its turn:

| `assignment_basis` | Meaning |
| --- | --- |
| `observed_within_calendar_window` | the entry's whole time span sits inside exactly one confirmed turn window; it is assigned (`turn_id` set) |
| `unconfirmed_calendar_transition` | the entry overlaps a rejected/uncertain calendar reading (`calendar_issues`); never assigned |
| `crosses_calendar_boundary` | the entry's span overlaps more than one confirmed window; listed on each as a `candidate_turn_id`, assigned to none |
| `outside_observed_turns` | the entry's span touches no confirmed window at all |

An assigned entry is appended to its turn's `timeline_refs`; an entry that
overlaps turns without being assigned is appended to each candidate's
`ambiguous_timeline_refs` instead. This is deliberately strict: temporal
proximity to a turn's action is never treated as proof that the action
caused the entry.

## Action statuses

Every window's committed decisions are the entries of kind
`committed_action` assigned to it, excluding the scheduled race in a
`phase_race_turn` (`scheduled_race_actions` are counted separately).
`action_status` is one of:

- **`one_action`**: exactly one decision in the window.
- **`missing_action`**: no decision in the window.
- **`multiple_actions`**: more than one decision in the window.

`action_count` is the number of decisions counted. `expects_one_action` is
`true` only for `window_kind` values `calendar_turn` and `phase_race_turn`
(a resolved window); an `unresolved_phase` or a bare `countdown_segment`
does not carry that expectation, because its boundaries may still contain
more than one action.

A training only becomes a `committed_action` in the first place when its
receipt clears one of the three `identity_basis` bars documented in
[analyzer-pipeline.md](analyzer-pipeline.md#rules-by-screen): observed gains,
a repeated result screen, or a repeated training name (or, for a banner-only
training, `training_banner`). A single result frame with no read gains and a
name read only once stays an uncommitted training result, which is why a
window can legitimately report `missing_action` instead of an invented
decision.

## Opening and closing states

For each turn and each channel (`stats`, `performance`), the ledger picks one
**opening** state: the earliest of a few candidate kinds, in this order of
preference by observation time, not by kind:

1. a **stable checkpoint** already established by `reconcile.stable_checkpoints`
   (or by another reading repeating one of that checkpoint's values through a
   `supporting_frames` proof) that falls before the turn's action;
2. a **repeated source reading**: two distinct source timestamps in the
   window, before the action, showing the exact same complete value tuple
   (`_repeated_source_state`, `basis: first_repeated_source_state_before_action`);
3. a **corroborated snapshot**: one complete source reading whose every field
   independently repeats at another timestamp within one second of it, with
   no intervening event and no calendar uncertainty in between
   (`_corroborated_source_state`, `basis: complete_source_snapshot_with_repeated_field_corroboration`);
4. the same corroboration applied to a **partial** reading, filling only the
   fields that snapshot actually shows (`basis:
   partial_source_snapshot_with_repeated_field_corroboration`).

Two distinct timestamps must agree; a conflicting, singleton, or
calendar-uncertain reading resolves nothing and leaves the opening
`not_observed_before_action` (or `partially_observed` when only some fields
came through). `exact_turn_boundary` is `true` only when the opening's
observed timestamp equals the window's own start; a later value is never
moved backward to stand in for an unobserved boundary.

A turn's **closing** state, for every turn but the last, is simply the next
turn's opening (`closing_basis: next_turn_first_observed_state`); an event
that happens between the closing observation and the next turn's own action
still shows up on the next turn's timeline, because this is an observation
link, not a claim about exactly when the state changed. The last turn's
closing state is the last observed reading after its action
(`basis: last_observed_state_after_action`,
`closing_basis: last_observed_state_not_inferred_completion`); when nothing
was observed after the action, `closing_basis` is `unavailable`.

`boundary_state_recovery.apply_opening_endpoint_projections` runs after the
ledger's own resolution and can fill an opening that recovery proved was
visible before the action; an accepted or rejected projection is recorded
under `opening_endpoint_projection`.

## Resource comparisons

Stat and performance checkpoints are compared the same way the causal
accounting module compares them: each comparison links exactly one adjacent
checkpoint pair, states its `observed_change`, `supported_change` and
`unexplained_change` per field, and lists the turns it overlaps
(`spans_multiple_turns`). A comparison spanning more than one turn is one
shared object, counted once by its `source_ref`; its residual is never
divided or assigned to just one of those turns.

## How the client uses it

The client and the service read the compact `timeline.json` projection built
from this ledger, not the ledger itself; see
[report-contract.md](report-contract.md) for that document's exact fields. In
short, each timeline turn keeps the ledger's `action_status`, `action_count`,
`expects_one_action`, `window_kind`, `scheduled_race` and opening values;
each timeline entry keeps the ledger's assigned `turn_id`, its first/last
seen timestamps, and its changes with the accounting basis behind each one,
so a viewer can tell a window with a real, single, well-evidenced decision
from one that is still `missing_action`, `multiple_actions`, or resting on an
`unresolved_phase`.

Concretely, `internal/timeline.Document.TurnSummaries()` backs
`GET /api/reports/{id}/turns` (the season strip: one row per window with its
opening values and any unresolved or worked-out differences),
`Document.Turn`/`TurnEntries` back `GET /api/reports/{id}/turns/{turn}` (one
window plus its assigned entries, with a viewer correction overlay when one
exists), and `Document.Unassigned()` backs `GET /api/reports/{id}/unassigned`.
`web/src/components/TurnPane.vue` reads `expects_one_action` directly to pick
its empty-window message: "no decision expected in this window" when it is
false, "no action was seen in this window" when a decision was expected but
the window is `missing_action`. `web/src/components/StatBar.vue` flags any
field whose accounting carries a `turn_difference` as "worked out from the
turn difference" rather than rendering it like a directly observed change.

## Limitations the ledger states about itself

`turn_ledger.build` attaches these limitations to every result, and they
still hold:

- Calendar windows identify observed turn labels, not exact action times.
- A first observed state may follow unobserved changes at the start of a
  turn.
- A shared resource comparison must be counted once by `source_ref`, never
  once per turn it touches.
- Timeline proximity never proves an event was caused by the turn's
  committed action.
- Missing, ambiguous and unparsed effects are not converted into zero
  changes.

`complete_event_history` stays `false` on every turn; the ledger's internal
consistency checks (types, source bounds, key references, arithmetic
agreement) are not a claim that the underlying video was read completely.
