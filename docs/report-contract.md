# The report and timeline contracts

The analyzer writes two JSON documents next to each other in a run
directory: `report.json`, the full analysis record, and `timeline.json`, a
compact derived projection of it. This document is a field-level reference
for both. See [analyzer-pipeline.md](analyzer-pipeline.md) for how the
values are produced and [architecture.md](architecture.md) for how the
service stores and serves them.

The service treats `report.json` as opaque beyond its schema version, path
and SHA-256 (`internal/worker/command.go`: "Nothing here parses report.json
beyond its schema version."). Every browser-facing endpoint is built on
`timeline.json` instead (`internal/timeline/timeline.go`). `report.json`
remains the reference for the analyzer's own tooling, evaluation, and for
anyone auditing a specific value back to its source frame.

## report.json

Schema `tracen-replay/full-recording-v1` (`report_contract.FULL_RECORDING_SCHEMA`).
`report_contract.validate` is the authority for the required shape; a report
missing any of these fails to load.

### Top level

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | must equal `tracen-replay/full-recording-v1` |
| `source` | object | `name`, `sha256`, `size_bytes`, `duration_ms`, `timeline_origin_seconds`, `width`, `height`, `codec` |
| `clip` | object | `source_start_ms`, `duration_ms`: the sampled interval of the source (a full recording samples the whole thing) |
| `sampling` | object | `requested_fps`, `frame_count`, `method` (`minimum_interval_on_decoded_pts`), `guarantees_all_events` (always false) |
| `frames` | array | one entry per captured frame: `id`, `evidence`, `source_timestamp_ms`, `clip_timestamp_ms`, `source_pts`, `time_base`, `screen_label`, `confidence`, `origin` |
| `observations` | array | imported reference annotations (rarely used for a full recording) |
| `limitations` | array of strings | fixed caveats about sampling coverage |
| `recognition` | object | `enabled` (bool), `model` (the OCR stack description), `device` (`cpu`, `dml` or `cuda`) |
| `gameplay_tracking` | object | the reconstructed collections; see below |
| `verification` | object | coverage and unresolved-work audit; see below |
| `evidence_integrity_snapshot` | object, optional | present only when a matching `evidence-audit.json` was found for this exact source/capture/inspection state |
| `turn_ledger` | object | schema `tracen-replay/turn-ledger-v1`; see [turn-ledger.md](turn-ledger.md) |
| `causal_accounting` | object | schema `tracen-replay/causal-accounting-v1`; see below |
| `automatic_refinement` | object, optional | discovery/reread counts from the automatic refinement stage |
| `training_gain_recovery`, `numeric_receipt_recovery`, `occluded_receipt_recovery`, `boundary_state_recovery` | object, optional | one plan-and-result record per bounded reread module; see [analyzer-pipeline.md](analyzer-pipeline.md) |
| `stage_failures` | array, optional | `stage`, `error`, `traceback` for each non-fatal stage that failed; a run with any of these has job status `completed_with_stage_failures` |

### gameplay_tracking

Required (`report_contract._GAMEPLAY_ARRAYS` / `_GAMEPLAY_OBJECTS`):

| Field | Type | Meaning |
| --- | --- | --- |
| `method` | string | always `neural_gameplay_v1` |
| `auxiliary_log_used` | bool | always `false`; the turn ledger and causal accounting refuse to build otherwise |
| `input_region` | array of 4 ints | the gameplay crop, `[148, 0, 958, 1080]` |
| `readings` | array | one parsed reading per captured frame (`screen`, `facts`, `stats`, `evidence`, `source_timestamp_ms`) |
| `screens` | array | per-screen-kind spans (`screen_summary`) |
| `checkpoints` | array | stable stat snapshots |
| `events` | array | training and outcome events, with `deltas`, `performance_deltas`, `effects`, `conflicting_readings` |
| `intervals` | array | stat accounting between consecutive checkpoints |
| `training_previews` | array | contiguous training-preview spans |
| `dialogue_choices` | array | committed event-choice selections |
| `turn_action_receipts` | array | one committed action per turn: `kind` (`training`, `race`, `rest`, `outing`, `infirmary`), `identity_basis`, evidence |
| `lesson_purchases` | array | named acquisitions joined to a request/confirmation and a cost basis |
| `skill_purchases` | array | receipt-backed skill batches with a skill-point debit |
| `skill_receipts` | array | `skill_receipt` screen spans |
| `concerts` | array | concert results joined to aftermath receipts and bonus candidates |
| `races` | array | grouped race results |
| `song_acquisitions` | array | songs learned |
| `unparsed_receipt_candidates` | array | receipt captions the parser could not fully read |
| `lesson_debit_observations` | array | lesson menu transitions used for debit evidence |
| `performance_accounting` | object | `checkpoints` and `intervals` for the five performance currencies |
| `fan_accounting` | object | race fan-total accounting |
| `owned_skill_inventory` | object | `complete`, `observed_owned_cards`, `summary_frames`, `scope`, `unresolved` |

Optional, present when the corresponding evidence exists: `preview_observations`,
`status_observations`, `state_observations`, `skill_menu_observations`,
`hint_card_observations`, `hint_card_recovery`, `event_choice_observations`,
`race_reward_observations`.

### verification

Built by `recording_verification.audit`. Selected fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `source_sha256`, `source_duration_ms` | string, int | identify the source this audit covers |
| `full_source_processed` | bool | every expected base frame was OCR processed and endpoints/spacing check out |
| `source_coverage_errors` | array of strings | for example `source_endpoint_gap`, `base_sampling_gap`, `unprocessed_base_frames` |
| `base_frames_expected`, `base_frames_processed` | int | base-frame OCR coverage |
| `missing_base_timestamps_ms` | array of ints | expected timestamps never processed |
| `sampled_coverage_by_minute` | array | per-minute coverage bins |
| `stat_intervals`, `performance_intervals` | object | counts of interval accounting statuses |
| `unresolved_stat_intervals` | array | intervals whose status is `unresolved` |
| `fan_intervals`, `song_acquisitions` | object | status counts for those collections |
| `lesson_receipts`, `lesson_costs_unresolved`, `skill_batches`, `skill_costs_unresolved`, `complete_skill_lists`, `skill_bundle_charges_assigned` | int | purchase-coverage counters |
| `concerts_with_reward_receipts`, `concerts_with_activation_receipts` | int | concert-coverage counters |
| `calendar_action_coverage` | object | per-turn action coverage from `calendar_coverage.audit` |
| `final_state` | object | the career-summary/completion-hub cross-check (`recording_verification.final_crosscheck`) |
| `fully_verified`, `go_ready` | bool | always `false` from the analyzer; the worker boundary never promotes these on its own |
| `remaining_gates` | array of strings | fixed list of manual review gates still open |

`analysis_job.py` additionally requires `verification.source_sha256` to
match `report.source.sha256`, and refuses a report claiming
`full_source_processed: true` while `source_coverage_errors` is non-empty or
`sampled_coverage_by_minute` shows an incomplete bin.

### causal_accounting

Schema `tracen-replay/causal-accounting-v1`. See
[analyzer-pipeline.md](analyzer-pipeline.md) for the status vocabulary and
the `turn_difference` rule and the learned reader's
`observed_learned_training_gain`, which counts as observed. Top-level fields: `contributions` (one row per
accepted or flagged amount, with `channel`, `field`, `amount`, `basis`,
`evidence`, `turn_id`), `comparisons` (checkpoint-to-checkpoint),
`turn_transitions` (turn-opening-to-turn-opening, with `endpoint_availability`
and an optional `terminal_observation`), `terminal_observations`,
`other_effects`, `issues`, `unassigned_contribution_refs`, `summary`,
`limitations`.

## timeline.json

Schema `tracen-replay/timeline-v1`. Written by
`analyzer/tracen_replay/timeline_document.py` (`build`/`write`); read by
`internal/timeline/timeline.go` (`Document`, `Turn`, `Entry`, and related
types). It is a strict subset of `report.json`: nothing in it is computed
beyond selecting and reshaping fields the report already states, and it
carries no frame image paths or OCR text. The two sides are generated from,
and checked against, the same field names; there is no field either side
silently drops or expects that the other does not send. The one
accommodation on the Go side is `Turn.ExpectsOneAction`:
`timeline.TurnSummaries` also falls back to checking `window_kind` directly,
so a timeline written before that field existed still reports the right
value.

### Document (top level)

| Field | Python type | Go field | Meaning |
| --- | --- | --- | --- |
| `schema_version` | str | `SchemaVersion` | `tracen-replay/timeline-v1` |
| `report_schema_version` | str | `ReportSchemaVersion` | the source report's own schema version |
| `source` | object | `Source` | `name`, `sha256`, `duration_ms`, `width`, `height` |
| `recognition` | object | `Recognition` | `model`, `device` |
| `turns` | array of Turn | `Turns` | see below |
| `entries` | array of Entry | `Entries` | see below |
| `summary` | object | `Summary` | see below |
| `note` | str | `Note` | a fixed reminder that facts are located by timestamp, not by path |

### Turn

| Field | Python type | Go type | Meaning |
| --- | --- | --- | --- |
| `id` | str | string | for example `turn-003` |
| `label` | str | string | the observed calendar/countdown label |
| `phase` | str | string | `dated`, `Junior Year Pre-Debut` or `Finale Underway` |
| `calendar_value` | int or null | `json.RawMessage` | the date ordinal or countdown number |
| `start_ms`, `end_ms` | int | `*int64` | the window's source bounds |
| `window_kind` | str | string | `calendar_turn`, `countdown_segment`, `unresolved_phase` or `phase_race_turn` |
| `action_status` | str | string | `one_action`, `missing_action` or `multiple_actions` |
| `action_count` | int | int | number of committed decisions (excluding a scheduled race) |
| `expects_one_action` | bool | bool | true for `calendar_turn` and `phase_race_turn` |
| `scheduled_race` | str, optional | string, omitempty | the race that ends a `phase_race_turn` |
| `opening` | object | `Opening` | `stats` and `performance` value maps, each field int or null |
| `accounting` | object of channel to field to row | `map[string]map[string]FieldAccounting` | see below |

`FieldAccounting` (the accounting row): `before`, `after` (int or null),
`status` (see the vocabulary below), `direct`, `derived`, `unresolved` (int
or null), `turn_difference` (int, optional: the worked-out part of
`derived`), `turn_difference_owner` (str, optional), `window_start_ms`,
`window_end_ms`. `turn_difference_owner` is written by
`timeline_document.owner_kind` as `training`, `event` (a receipt that lost
its number), `lesson`, or `race` (the turn's one race, for a skill-point
residual, `causal_accounting`'s `sole_race_takes_skill_point_residual`
basis). The Go package's own comments on `FieldAccounting.TurnDifferenceOwner`
and on `Difference.Owner` list only `training`, `event` and `lesson`; `race`
is a real value the Python side emits that those two comments do not
mention. This is a comment gap, not a functional one: both fields are plain
strings and carry the value through either way.

### Entry

| Field | Python type | Go type | Meaning |
| --- | --- | --- | --- |
| `id` | str | string | for example `entry-0042` |
| `kind` | str | string | for example `training`, `outcome`, `committed_action`, `lesson_purchases`, `races`, `skill_hint_change`, `ambiguous_effect`, `unparsed_receipt` |
| `turn_id` | str, optional | string | empty when the entry is outside every observed turn |
| `first_seen_ms`, `last_seen_ms` | int | `*int64` | the entry's observed span |
| `assignment_basis` | str, optional | string | `observed_within_calendar_window`, `unconfirmed_calendar_transition`, `crosses_calendar_boundary` or `outside_observed_turns` |
| `context_title` | str, optional | string | the event's narrative title, when read |
| `training_option` | str, optional | string | the training's option name |
| `action_kind` | str, optional | string | `training`, `race`, `rest`, `outing` or `infirmary` for a `committed_action` entry |
| `raw_text` | str, optional | string | the caption text for an unparsed receipt |
| `accounting_role` | str, optional | string | for example `reference_only_not_an_additional_award` |
| `transaction_id` | str, optional | string | the underlying transaction's own id |
| `reward_link_status` | str, optional | string | `linked_event` or `separate_receipt` for a committed action |
| `effect` | object, optional | `json.RawMessage` | the raw effect record for a `skill_hint_change` or `ambiguous_effect` entry |
| `accepted_award` | bool, optional | `*bool` | false for an ambiguous or unparsed candidate |
| `conflicts_present` | bool, optional | `*bool` | true when the underlying event/transaction has an unresolved conflict |
| `changes` | object of channel to field to Change | `map[string]map[string]Change` | see below |
| `detail` | object, optional | `json.RawMessage` | the compacted underlying record (event/transaction), evidence and duplicated identity fields stripped |

`Change`: `amount` (int or null), `basis` (str, optional: `observed_receipt`,
`observed_training_gain`, `observed_learned_training_gain`,
`committed_skill_debit`, `state_derived`, `state_constrained`,
`projected_debit`, `summary_only`, or `turn_difference`),
`read_amount` (int, optional: the digits actually read when a clipped badge
was completed by a `turn_difference`; `amount` is then the completed value),
`contradicted_by` (array of int, optional: the gains a reader saw on the
training's own card while `amount` was worked out from the turn difference;
the amount stands and the disagreement is a reviewer's to settle).

### Summary

| Field | Python type | Go type | Meaning |
| --- | --- | --- | --- |
| `field_status_counts` | object | `map[string]int` | counts of `causal_accounting` field statuses across the whole run |
| `action_statuses` | object | `map[string]int` | counts of `one_action` / `missing_action` / `multiple_actions` across turns |
| `observed_turn_windows` | int | int | number of confirmed turns |
| `entry_counts` | object | `map[string]int` | count of entries by `kind` |
| `stage_failures` | array | `[]StageFailure` | `stage`, `error`, `traceback` (all three are carried on both sides: Python copies the report's own `stage_failures` verbatim, and the Go `StageFailure` struct has a `Traceback` field too) |

## Accounting status vocabulary

Used on both `causal_accounting` comparisons/turn_transitions and on
`timeline.json` turn `accounting` rows:

| Status | Meaning |
| --- | --- |
| `balanced_observations` | the observed change equals the sum of directly observed contributions |
| `balanced_with_derived_changes` | it balances, but includes a state-derived or otherwise indirect contribution |
| `unexplained_change` | a residual remains after every accepted contribution |
| `unresolved_attribution` | a candidate contribution is ambiguous (conflicting, untimed, or itself conflicted) |
| `missing_endpoint` | one of the two compared states was not observed |
| `unordered_endpoints` | the compared turn-opening observations are not in increasing time order (turn comparisons only) |

## Ledger vocabulary

`action_status` (per turn): `one_action`, `multiple_actions`, `missing_action`.

`window_kind`: `calendar_turn` (a dated half-month turn), `countdown_segment`
(a pre-debut phase identified only by its turn countdown),
`unresolved_phase` (a phase whose countdown was never read), `phase_race_turn`
(a window a scheduled race ends: a finale race, or the last pre-debut
countdown turn when its window also holds the player's decision).

`expects_one_action` is true only for `calendar_turn` and `phase_race_turn`.

See [turn-ledger.md](turn-ledger.md) for how these are assigned.
