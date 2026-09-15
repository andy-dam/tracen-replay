# Turn ledger

The Python report now includes a `turn_ledger` projection with schema
`tracen-replay/turn-ledger-v1`. It joins the existing evidence collections for
the Go consumer; it does not rerun OCR or invent missing changes. The report
validator recomputes the projection and rejects stale or altered ledger data.
When `causal_accounting` is present, it also recomputes that projection from
the source collections. Altered contribution amounts, evidence references,
observed/derived bases, duplicates, and comparison statuses fail validation.
Historical reports may omit the additive terminal metadata; supplied metadata
must match the projection. These consistency checks do not certify OCR truth.

Each window contains its observed calendar label, boundaries, committed action
count, starting and later observed states, chronological entry references, and
stat/performance comparison references. Entries point into `gameplay_tracking`
with JSON pointers. Consumers resolve those pointers in the same report.

- Dated windows use repeated half-month observations. Pre-debut countdowns
  identify segments that may contain more than one action. A phase without a
  readable countdown remains an unresolved phase, not a fabricated turn.
  `expects_one_action` distinguishes these cases.
- The finale keeps one calendar label and one countdown for its three turns
  (train, then the Qualifier; train, then the Semifinal; train, then the
  Finals). There each finale race ends its turn: the next home observation
  after a `race_result` screen opens the following window
  (`boundary_basis: finale_race_advance`, evidence including the result
  frame). Such windows have `window_kind: phase_race_turn`, are labelled by
  the race that ends them and carry `scheduled_race`; the race is counted as
  `scheduled_race_actions`, not as the turn's decision, so a trained finale
  turn has `one_action`. The screens after the Finals carry the label but no
  training, so they stay in the Finals window. The last pre-debut countdown
  turn ("1 turns to goal") works the same way when its window holds both the
  player's decision and the goal race: the race becomes `scheduled_race` and
  the window a `phase_race_turn`; when the countdown was confirmed only after
  the training, the race stands alone in the window and is its one action.
- A window's committed training action carries `identity_basis` in its receipt:
  `observed_gains` (its stat gains were read), `repeated_result_frames` (its
  result screen was read on two or more frames) or `repeated_training_name`
  (the training's name was read on two distinct frames). A single result frame
  with no read gains and its name read once is not committed, so its window
  reports `missing_action` rather than an invented decision.
- `states.stats` contains all five stats and skill points when observed.
  `states.performance` contains Dance, Passion, Vocal, Visual and Composure.
  A missing state is `null`, not zeros or a carried-forward value.
- An opening state is the first observed state before the action in that
  window. Its timestamp and `exact_turn_boundary` flag remain visible. A later
  state's values are not moved backward to fill an opening. Matching explicit
  source observations can link an unchanged stat checkpoint across dates.
- Repeated complete source readings can also establish an opening when their
  short duration does not qualify them as accounting checkpoints. Two distinct
  timestamps must agree; conflicting, singleton and uncertain-calendar readings
  remain unresolved. `source_ref` identifies the source record and `values_ref`
  points directly to its numeric mapping. Use `values_ref` for both checkpoint
  and reading-backed states; `supporting_source_refs` preserves corroborating
  observations. This does not add a synthetic accounting interval.
- One complete source snapshot can also be corroborated by nearby partial
  readings when every field repeats at another distinct timestamp within one
  second. `field_corroboration` records each value pointer, time and proof.
  All opening values still come from one actual snapshot. Conflicting values,
  intervening events/purchases and uncertain calendar transitions veto this
  fallback; separate partial readings never synthesize a complete state.
- A closing state can refer to the next window's first observed state or the
  last observed state after the final action. Neither promises the value at an
  unseen exact boundary. Events before the next first state remain visible in
  the timeline; this observation link does not reassign their cause.
- Events retain changes and field evidence. Hints have separate entries with
  their first linked receipt time when available. An event spanning two
  windows remains unassigned with both candidate windows listed. Temporal
  proximity never establishes that an event was caused by training.
- Race, lesson, song, concert and skill records are references to the original
  transactions. They are not additional awards to sum on top of their event
  receipts. Unparsed receipt candidates stay visible and unaccepted.
- Ambiguous effect candidates have their own entries, including uncertain hint
  names. Their observed alternatives and amounts are retained, but
  `accepted_award` is false and `occurrence_count` is unknown. Do not count each
  spelling alternative as a separate hint gain.

Resource comparisons link adjacent observed states and retain their original
supported changes and residuals. A comparison spanning multiple turns is one
shared object. Count it once by `source_ref`; never split its amount or assign
its residual to one of those turns. Stat comparisons link their supporting
events, and performance comparisons link the original transaction records.

The causal accounting projection adds `transition_kind` and per-field
`endpoint_availability` without changing the historical arithmetic statuses.
`next_turn: no_next_turn` means that the ledger has no following turn; it does
not mean a terminal value was zero or that the final screens were invisible.
`not_observed` describes the report's evidence, not a source-wide visibility
finding. When a terminal closing observation exists, `terminal_observation`
retains its value pointer and actual timestamp. Its
`later_numeric_contribution_refs` identify recognized changes extending beyond
that observation. For example, skill points observed before a later purchase
must not be presented as the post-purchase balance. Even an empty later-change
list does not establish complete coverage or verified run completion.

`causal_accounting.terminal_observations` also exposes readable completion-hub,
finish-confirmation, and final-summary facts within the final ledger segment.
Each observation contains values from one frame, the exact field pointers,
timestamp, and evidence path. Final attributes and earlier skill or performance
points remain separate observations; missing fields are never borrowed from
another frame. Conflicting readings in the same frame remain explicit.
`later_unquantified_change_refs` also flags confirmed skill purchases whose
cost is unknown, even when no numeric debit could be recorded. These records
are observations only, never additional changes or proof of run completion.

The ledger checks types, source bounds, key references and arithmetic
consistency. It does not certify interpretation of the source video. In
particular, `complete_event_history` stays false until coverage has been
verified independently. The completed bounded source and attribution checks
are recorded in [the first integration acceptance](first-go-acceptance.md).
