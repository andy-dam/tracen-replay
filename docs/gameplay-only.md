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

Cursor checks cover numeric receipts and nonnumeric friendship statuses. When a cursor overlaps a status receipt and character alignment cannot establish which text is obscured, the parser abstains on the whole line. Raw OCR remains available for review. Malformed supporter-join receipts are also surfaced as review candidates; they do not count as confirmed joins.

Receipt crop agreement is not sufficient to accept a text replacement: the agreed text must also parse as a supported receipt. Repeated malformed text, truncated receipts and ordinary dialogue do not qualify. These crops remain correlated views of one frame, not independent temporal evidence. Cached outcome confidence promotions likewise recheck the original text, reread score and eligible receipt location instead of trusting a stored acceptance flag.

Validation follows the output type: race fan gains are checked in race receipts, goal status in screen observations, and dialogue awards in event effects. A missing value in the wrong evaluator is not a recognition failure. Moving a reference between these checks must preserve its source evidence and original observation count.

Lesson transaction references can check the exact observed `name`, `requested_name`, and `receipt_name` alongside costs and awards. A correct numeric reward cannot compensate for a mismatched name. Evaluation output lists `evaluated_fields`; older numeric-only references do not establish lesson identity, and an unreadable name must not be labeled as an empty or null identity.

## Gates before the application

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
