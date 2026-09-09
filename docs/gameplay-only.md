# Gameplay-only reconstruction

Status: full-recording development analysis implemented; reliability validation remains open. Go and Azure remain behind the gates below. See [measured results](results-gameplay-only.md) and [full-recording setup](full-recording-analysis.md).

## Recognition boundary

The supported profile is English 1920x1080, with gameplay at `(148, 0, 958, 1080)`. Neural recognition receives only that 810x1080 crop. Full source screenshots are retained as evidence but the side log is not recognition input. The older Tesseract `--gameplay-only` clip runner remains available; legacy `--track-stats` uses the auxiliary log and is not a fallback.

## Report contract

`gameplay_tracking` includes:

- `readings` and `screens`: source-timestamped observations and visibility spans, not action counts.
- `training_previews`: browsed options, never completed-action claims.
- `dialogue_choices`: repeated offered menus and matched selection animations, with single-response dialogue distinguished from multi-option choices. Choice time is an observation, not a known click time or proof of all subsequent effects.
- `turn_action_receipts`: training result options, rest/outing recovery chains and race results. Receipt time and unknown click time are separate.
- `checkpoints`, `events`, `intervals`: repeated six-field states, supported effects and unexplained residuals. Candidate resolutions retain evidence and whether they depend on surrounding states.
- `lesson_purchases`: matching named receipt and confirmation, five-resource costs, observed debits, actual awards and separate projected effects.
- `skill_purchases`: receipt-backed batch SP debit, cart additions/removals, committed bundles, prerequisite candidates and circle variants. Complete ownership is a separate claim.
- `owned_skill_inventory`: repeated visible final-summary card text with source evidence. Partial names, symbol variants, levels and off-screen ownership are not promoted to a complete inventory; see [ownership observations](owned-skill-observations.md).
- `races`: identity, course, placing, fans and repeated visible item-quantity snapshots. Snapshots require distinct timestamps, preserve evidence, and are never summed across scrolling into an inventory transaction. Item identities and reward-list completeness remain unknown.
- `concerts`: confirmation, result, aftermath, queued effects, update receipts and later observed current-bonus snapshots. The update caption does not prove every value.
- `song_acquisitions`: one acquisition per receipt with alternative observed names and supported paid-lesson or story-event associations.
- `performance_accounting` and `fan_accounting`: separate resource ledgers with unknown observations preserved.

`verification` checks source endpoints, sampling continuity, PTS, processed frames per minute, discrepancies, calendar/action coverage and final-state agreement. Separate `evidence-audit.json` validates source hashes and crop pixels. Neither automatically certifies semantic correctness.

When final-state cross-checking lacks a required observation, `verification.final_state` retains separately observed summary attributes and completion-hub skill points with timestamps and evidence. It lists the missing observations and leaves agreement unknown. A hub showing stat ranks cannot corroborate exact numeric attributes; ranks are never converted into numbers. These partial observations do not establish complete final verification or inventory ownership.

Concert `later_active_bonus_snapshot` likewise keeps source-timestamped `observations` separate from confirmed `values`. Confirmation requires matching current-column values at two distinct timestamps; duplicate observations of one frame cannot satisfy it. `unresolved_fields` distinguishes absent readings, conflicting readings, and insufficient temporal support. Planned-column values never fill a missing current value. No reviewed post-fifth-concert panel currently establishes the final active totals in either recording; incomplete source recall means this is a limitation of reviewed evidence, not proof that no such panel exists anywhere in the video.

Validated concert-slot refinements retain their supporting source timestamps and crop proofs through parsing and aggregation. Their distinct frames can confirm the unchanged support-chain level even when the refinement emits one corrected base row; threshold variants of one frame cannot. The original run's first later-current snapshot confirms specialty priority 10 and support-chain level 0, while friendship effectiveness 10 remains separately observed without repeated confirmation.

## Counting rules

| Mechanic | Count from | Keep separate or unknown |
|---|---|---|
| Training | Repeated completed result option; gains tracked separately | Browsing, projections, caps, unreadable rewards, click time |
| Ordinary events | Explicit receipts with title/geometry/time boundaries | Dialogue, actual selected choice, unreadable effects |
| Dialogue choices | Repeated menu and bilateral selection marks consistent with readable selected-card text | Different later responses at the same height, incomplete menus and rewards as selection evidence |
| Rest and outings | Request then recovery; support outings without energy also require matching narrative and the observed next date | Canceled dialogs, returns to the hub and unrelated recovery |
| Races | Result identity, placing and fan receipt | Entry menus, unread item identities |
| Skills | Acquisition receipt plus SP balance evidence | Pending/removed selections, bundled prerequisites, next offered rank |
| Lessons | Named receipt, confirmation and balances | Offered cards, delayed unchanged counters, canceled dialogs |
| Songs | Learned receipt and supported acquisition route | OCR alternatives as multiple songs, story songs as paid lessons |
| Concerts | Result/aftermath and explicit bonus update | Planned values as active; missing values as zero |
| Goal status | Exact readable `Goal Achieved!` in the goal-status area | Persistent status is not a new event, achievement timestamp, or reward |
| Completion | Observed final attributes and remaining SP | Existing skills as fresh purchases; missing inventory as complete |

Immediate effects, future training modifiers and bonuses queued until a concert are distinct. Performance currencies, caps, fans, energy, mood, friendship and hints are distinct resources. `Speed went up by 6 to new heights.` means **6**; the displayed reduction has already happened. No unseen rewards are computed from scenario formulas.

The lower race-day totals row can supply current stats from the original OCR when its layout is established by the label/control geometry or the five blue label bands in the gameplay pixels. All six numeric readings still require 97% confidence; cap denominators, projected gains and duplicate candidates are excluded. The report preserves the field text, boxes and confidence. A visible race button does not establish a completed race, and these observations must meet the ordinary temporal checkpoint requirements before entering the state ledger.

Cursor checks cover numeric receipts, nonnumeric friendship statuses and inheritance inspiration receipts. When a cursor overlaps a receipt and character alignment cannot establish which text is obscured, the parser abstains on the whole line. Inheritance alignment must reproduce the original OCR text exactly before it can narrow that check to the name. This detects the supported cursor appearance; it does not establish that animated confetti or other effects leave a name unobstructed. Raw OCR remains available for review. Malformed supporter-join receipts are also surfaced as review candidates; they do not count as confirmed joins.

A complete numeric field receipt may resolve one shorter digit reading when a later source frame repeats its exact text with only the sentence terminator missing. Both readings require at least 97 OCR confidence and distinct source timestamps and artifacts, 50–500 ms apart within the same outcome. A conflicting repeat, intervening outlier, or recipient name prevents this supplemental resolution. The accepted complete receipt, later corroboration and rejected reading remain in the conflict audit. An unfinished line alone cannot supply the replacement amount, and state totals are not used by this rule. The existing check based on multiple complete receipts remains separate; earlier typewriter prefixes do not invalidate that stronger evidence.

Receipt crop agreement is not sufficient to accept a text replacement: the agreed text must also parse as a supported receipt. Repeated malformed text, truncated receipts and ordinary dialogue do not qualify. These crops remain correlated views of one frame, not independent temporal evidence. Cached outcome confidence promotions likewise recheck the original text, reread score and eligible receipt location instead of trusting a stored acceptance flag.

Base-cache receipt rereading is available for explicit time ranges through `python -m tracen_replay.base_receipt_refinement RUN --start-ms START --end-ms END`. It writes separate `base-receipt-refinement` artifacts and leaves the original neural observations intact. A subsequent cached reparse validates source-frame bytes, gameplay-file bytes, decoded gameplay pixels and crop geometry before applying text changes. Prior line refinements take precedence; collisions remain recorded, and pixel occlusion checks run afterward. This path is optional and its availability does not establish a recognition improvement.

Friendship recipients that change across adjacent observations of the same receipt position remain ambiguous when the award amount or status agrees. This check uses time and overlapping line geometry, without restricting the names to a particular spelling distance. It preserves the observed alternatives and their evidence rather than choosing a name or counting each spelling as another recipient. Simultaneously visible recipients, separate line positions and observations too far apart do not establish this conflict. A shared dialogue line moving vertically also invalidates the stationary-slot comparison: scrolling can put two different recipients in the same screen position. This guard does not promote any OCR reading.

A shorter friendship name can be removed as a cursor-obscured suffix variant when two later, distinct source frames already provide the exact full name and amount. Verified OCR alignments must place the retained letters in the same stationary positions and every omitted letter under the earlier cursor. At least one supporting name must have no cursor intersection; missing alignment, a different amount, a competing recipient, scrolling or a screen change prevents resolution. Validated source, frame, image and model identity travel with the alignment, and cross-frame or cross-source evidence is rejected. This rule neither supplies an unobserved name nor accepts a lone gap-obscured reading. It preserves the shorter reading and the supporting timestamps, alignments and cursor boxes. Overlay alignment collection includes lower-edge intersections so the center-of-line heuristic does not exclude the evidence needed to assess them.

Conflicting inheritance names become explicit ambiguous candidates when adjacent
source observations track the same upward-scrolling receipt. The name boxes must
keep compatible dimensions and move with independently unique neighboring text;
contradictory motion, competing names, missing samples or changed context prevents
that inference. A stationary screen position alone is insufficient because a new
recipient can replace the preceding line there. This guard preserves both spellings
and their evidence without choosing a name or claiming to detect confetti.
Reconstruction annotates copies of parsed effects, keeping source observations
unchanged when reports are rebuilt.

Validation follows the output type: race fan gains are checked in race receipts, goal status in screen observations, and dialogue awards in event effects. A missing value in the wrong evaluator is not a recognition failure. Moving a reference between these checks must preserve its source evidence and original observation count.

Race evaluation uses half-open reference windows. A result first observed before
a window and last observed exactly at its start is carry-in from the preceding
window. A result beginning at the start belongs to the new window; a result
continuing beyond that boundary remains a candidate. This rule applies to both
the review scope and individual race matching, without choosing candidates by
their recognized names or reward values.

An effect reference that also labels training-result grids may explicitly set
`include_training_results: true`. This adds the completed training event's typed
stat and performance deltas to the comparison without changing report events or
counting browsed previews. The score records how many typed training predictions
were included. Existing receipt-only references keep their prior scope. This option
requires `event_start` timing: a value derived from before/after counters does not
establish an exact receipt-observation timestamp. Reference scope corrections must
be versioned separately from recognition fixes.

A maximum-friendship receipt records a status, not an additional numeric gain or proof that the maximum was first reached at that moment. Count repeated frames of one receipt once. Separate training and support-event receipts may each report that same status; a reference group spanning both must retain both observations. Grouping them within one review interval is not evidence that the second receipt is a duplicate.

Effect references may explicitly set `timing_basis` to `first_exact_effect_observation` to assign each award to the window containing its first exact, linked receipt observation. An event can span a window boundary, so its start does not necessarily locate every award. Missing exact timing evidence fails the check instead of silently omitting the effect. References without this field retain the legacy `event_start` convention for reproducibility. Adopting observation timing requires a versioned reference with source-reviewed group bounds; diagnostic rescoring does not replace frozen results or establish improved recognition.

A sampled reference cannot establish an exact onset between frames. A group may declare an `onset_window` with `last_absent_ms` and `first_present_ms` only after reviewing those adjacent samples; the latter must equal the group's start. Predictions after the absent sample can then match the group, including native detections slightly before the first positive reviewed sample. The evaluator rejects invented or nonadjacent bounds. The reviewer remains responsible for verifying absence and presence; this is explicit source uncertainty, not a global timing tolerance.

Identity comparison can bridge one intervening 250 ms frame only when that frame explicitly records name occlusion at the same receipt position with the same award or status. The obscured name is not accepted as an effect. Missing frames, different awards and scrolling do not establish this connection; the conflict retains the middle frame as evidence.

Separately, a hint receipt split across outcome groups can be counted once when exact, high-confidence endpoint readings are joined by one continuously tracked line. Every intermediate sample must retain the same visible hint amount prefix and compatible line geometry; unreadable text remains in `unknown_slot_evidence`. Missing or competing lines, an explicit different identity or amount, a downward reset, or a screen/title/dialogue boundary prevents merging. This continuity check only removes a proven duplicate; it never creates an award from an unreadable line, and unrelated effects keep their own event boundaries.

Lesson transaction references can check the exact observed `name`, `requested_name`, and `receipt_name` alongside costs and awards. A correct numeric reward cannot compensate for a mismatched name. Evaluation output lists `evaluated_fields`; older numeric-only references do not establish lesson identity, and an unreadable name must not be labeled as an empty or null identity.

References may also attach `source_performance_balance` with complete five-currency `before` and `after` observations and explicit `other_changes`. Each balance records `values`, `source_timestamp_ms`, `evidence`, and `sha256`. The evaluator rejects a labeled cost unless it equals before plus other changes minus after for every currency. `--evidence-root` additionally checks the referenced proof files. This verifies label consistency and file integrity; the reviewer must still establish that the screenshots show actual balances around the committed purchase and account for intervening changes. Legacy references without this witness remain reproducible and report zero source-balance checks.

## Gates before the application

Action and effect references with unfinished labels must declare `reference_complete: false`.
Their agreement counts remain available for debugging, but the evaluator cannot
return a passing result even when every labeled effect matches. This also prevents
an empty partial reference from being mistaken for a verified negative interval.
Legacy references without this field keep their existing scoring behavior;
the flag is a reviewer declaration, not automatic proof of annotation completeness.
Completeness applies to the declared channel, interval and sampling scope. A fully
labeled set of 250 ms samples can be complete within that scope while events
between those samples remain unknown. Action scores explicitly distinguish a
complete declaration, an incomplete declaration and legacy unspecified metadata.
Action evaluation accepts only training, rest, outing and race scopes, and records
the exact kinds and interval in its result. Lessons, concerts and dialogue choices
belong to their separate evaluators. Coverage of a training-only reference does
not establish race, rest or outing recall over the same interval. References that
exceed the recording duration or use malformed time bounds are rejected.
Predicted action rows must also carry a supported kind and an integer timestamp
within the recording; malformed rows are rejected before scope filtering, with
the offending row index in the error.

`python -m tracen_replay.action_corpus INDEX REPORT --evidence-root RUN --output SCORE`
combines action references using a versioned index. Its `schema` is
`tracen-replay/action-corpus-v1`; it declares `source_sha256`,
`source_duration_ms`, and a `references` array containing source-relative
`path` and `sha256` entries. An entry may set `selected_kinds` to a nonempty
subset of its reference's declared kinds. This can replace an older rest
reference while retaining its training channel, without rewriting the source
labels. Two selected scopes for the same kind cannot overlap; different kinds
may cover the same interval.

The score preserves each original reference result and reports agreement,
unmatched labels, extra predictions, unassessed predictions, scope gaps and
completeness declarations separately for each kind. Explicitly incomplete
references cannot pass scoped agreement. Matching a positive-only race label
does not establish absence of other actions in its window. Reference file hashes
are verified; source-pixel provenance and visual annotation completeness still
require their separate audits. The command refuses to overwrite an existing
score artifact.

- [x] Structurally isolate gameplay pixels and test independence from the auxiliary log.
- [x] Process the complete supplied recording and verify frame/crop/refinement provenance.
- [x] Separate previews, confirmations, receipts, immediate awards and future effects.
- [x] Account for six stat fields and five performance currencies across observed checkpoints.
- [x] Account for race fans, actions on dated turns, and final attributes/SP.
- [x] Recover observed lesson debits, committed skill charges and concert reward/update receipts.
- [x] Review every detected action receipt and evaluate two contiguous development sequences.
- [ ] Measure full action/effect recall against dense references not selected only from predictions.
- [ ] Validate complete skill inventory and active concert bonuses; adjudicate fields the source does not expose.
- [ ] Validate non-stat mechanics and uncertain names, including choice evidence and race item identities.
- [x] Evaluate a separate recording before source-driven changes and preserve initial scores separately from later regression results.

The separate-recording evaluation exposed failures; completing that evaluation does not close the remaining reliability gates. Its reviewed intervals and limitations are reported in [measured results](results-gameplay-only.md).

All clips from one video belong to one recording-level evaluation split. Additional sampling does not create an independent test set. Unknown fields are valid outputs; complete mechanic coverage requires its own reference evidence.
