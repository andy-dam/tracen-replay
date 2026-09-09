# Race reward quantities

Race result parsing retains high-confidence `xN` or `×N` text beneath a visible Items heading in the supported gameplay layout. A result group exposes `visible_item_reward_snapshots` only after matching quantities and positions appear at two distinct timestamps no more than 500 ms apart. A missing observation, movement, changed quantity, or longer gap breaks the snapshot.

Each snapshot carries frame evidence and unknown item names. Several visible quantities can coexist. Snapshots are observations, not inventory additions: repeated or scrolling views cannot be summed, and neither item identity nor list completeness is certified.

Snapshot items also retain `section`: `items`, `bonus`, or `null` when unresolved.
Section assignment requires a visible high-confidence heading and a single nearby
quantity row that does not cross the next heading. Duplicate, reversed or weak
headings do not establish a section. Each supporting observation retains the
heading text and box; missing section evidence breaks snapshot agreement.
Identical quantities in Items and Bonus remain separate observations. This does
not identify the reward icon or establish inventory changes.

Race-reference item snapshots may add `sections`, for example
`{"items": [1, 600], "bonus": [600]}`, alongside the complete flattened
`quantities: [1, 600, 600]`. The section lists must partition those quantities.
The evaluator checks section labels in the report's per-frame reward observations
against the exact referenced timestamp, evidence path, quantity and box. Unknown
or conflicting sections fail that check. References without `sections` retain
their older quantity-only comparison and do not validate section identity.

For missed quantity badges in the supported result layout, generate targeted recognition artifacts before rebuilding the cached report:

```powershell
python -m tracen_replay.race_quantity_refinement RUN_DIRECTORY
```

For a bounded investigation, repeat `--frame-id FRAME_ID` for every adjacent
sample to be used. Generation then limits both target frames and supporting
observations to that selection while preserving the complete capture manifest.
One selected frame cannot meet the two-timestamp agreement requirement.

The refinement reads gameplay badge crops at three scales. Acceptance requires agreement at confidence 97 or higher at two distinct source timestamps; multiple scales of one frame count only once. The cached pipeline validates the capture manifest, source frames and timestamps, OCR/model bindings, and exact crop pixels, then recomputes agreement from the observations. A recovered badge is added only to frames that supported its reading. Conflicts remain explicit, and item identities and list completeness remain unknown.

On the separate recording, the source-reviewed snapshot at 591 seconds now matches all four quantities, alongside all nine labeled race fields. The snapshot at 331.5 seconds recovers both missed `×1` badges but still misses `×20`, so its quantity check continues to fail while its ten labeled race fields pass. All 124 numeric intervals are unchanged, and the 12 preserved regression checks pass. These are development results after source-driven changes, not a new independent evaluation.

Both supplied recordings were rebuilt from cached OCR. The original has quantity snapshots for 12 race results; the separate recording has them for 11 of 12. These are extraction counts, not manually verified accuracy scores.

The preserved source-selected 150–160 second debut reference in the separate recording previously matched 10 of 11 scored fields. It now matches 11 of 11, including the visible quantity 200. Reference screenshot hashes were checked, and the initial failed evaluation remains separate from the new quantity evaluation. Item identity, pre-race fans, strategy, and race time remain unscored by that check.

The reusable race-reference evaluator separates typed result fields from per-frame item observations:

```powershell
python -m tracen_replay.race_evaluate REFERENCE REPORT --evidence-root RUN_DIRECTORY --output SCORE_FILE
```

References use `tracen-replay/race-reference-v1` with a source hash, reviewed time scope, race windows, expected fields, and timestamped proof hashes. Optional item snapshots declare quantities at exact proof timestamps. Matching uses time windows before comparing values; ambiguous candidates fail. Missing races remain in the expected field and item counts. Unknown fields are unscored, and booleans or floats cannot match integer rewards. An empty reference requires an explicit reviewed negative with evidence.

The converted debut reference passes 10 typed fields and one quantity snapshot. The evaluator's 17 focused tests pass, including missing races, ambiguous windows, typed comparisons, and proof mismatches. These checks do not establish full-recording race recall or item identities. Full-recording recall, complete inventory, active concert bonuses, and unresolved non-stat mechanics still require validation; this change does not close the Go gate.
