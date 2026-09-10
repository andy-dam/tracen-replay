# Gameplay-only development results

The original English Our Grand Concert recording was processed from 00:00 through 31:06.333. The source coverage and accounting below use development source SHA-256 `a75008eb095a2d37f9d0b49d09f1a54be8285bd9ef69e243037440a580193174`. A separate-recording evaluation appears below. Media, weights and generated reports remain local.

## Latest regression checks

The current reports retain 143 balanced six-field intervals in the original
recording and 130 in the second. The latest working-tree test run passes 894 automated tests.
All 12 preserved-reference checks in
`sp-receipt-validation-v1/preserved-regression.json` pass against the
published second report. These are development regressions; the original
separate-recording evaluation remains preserved.

A subsequent complete source reparse produced development candidates with 8,387
merged observations for the original recording and 8,346 for the second. Source
video hashes and the base, training, native, and receipt inspection layers were
checked. Both candidates retain the observed stat checkpoints and match all 28
labeled visible inventory names, all 12 labeled inventory details, and all 48
historical concert-panel values. The second still matches 78 action labels,
15 positive/negative race checks, and all 12 preserved regression checks.

The second candidate is now the active local report. A bounded 30 FPS inspection
found five clear SP +105 receipt frames between the base samples; the existing
receipt pipeline counts them as one award and restores all 130 balanced stat
intervals. Its 35 remaining effect diagnostics are preserved. Source-backed
hint-cache revalidation also accepts Winter Runner and Straightaway Recovery
without changing reference labels. Exact reconstruction and the existing
reference checks pass. A semantic comparison confirms unchanged actions, lesson
and song transactions, concerts, and races; shifted event IDs do not represent
new transactions. The previous report and inspection manifests remain archived.

A retrospective trial of the automatic receipt planner starts from the preserved
second-run candidate before that SP recovery. A six-second footage budget and
100-frame cap select four windows totaling 5.801 seconds, without expected
receipt names or values as OCR input. The decoder produces 88 frames. After
source-image and cursor checks, reconstruction recovers one SP +105 award and
all 130 stat intervals balance. Repeated observations do not create duplicate
awards. Exact model-free reconstruction passes, and the next plan excludes the
attempted windows. The trial leaves the active report unchanged; it demonstrates
bounded recovery on development footage, not independent accuracy.

The original recording's stricter candidate is now the active local report. It retains all 143 balanced
stat intervals but withholds Passion +10 under the stricter cursor check, leaving
one performance interval unresolved. A bounded 30 FPS source probe found no
clear duplicate receipt. Other withheld non-stat receipts still need source
adjudication or supported recovery. Full semantic recall remains unproven.

A subsequent source-bound hint-card pass recovers Unstoppable +3 from two
adjacent observations in the original recording. Each observation has a
separately recognized amount prefix tied to its exact crop and OCR model;
the skill's suffix rank remains undetermined. Model-free full reassembly
adds that one effect without changing numeric accounting, actions, or
transactions. Tampered prefix names, crop hashes and model bindings are
rejected by cache replay. Before activation, the report passed the five preserved
opening-action, fixed-effect, lesson/concert, full-action-receipt and gameplay-point
checks with exactly the same results as the prior report. Exact model-free
reconstruction passes; checkpoints, actions, fans, owned inventory, lessons,
songs, concerts, skill purchases, choices and races remain unchanged. The prior
report and viewer are archived locally. Of 137 performance intervals, 136 balance
and one remains unresolved because the cursor-covered Passion receipt is withheld.

The second recording's `action-corpus-v10` selects 50 reference entries with
non-overlapping scopes within each action kind. All 78 detected actions match
source labels: 60 training actions, one rest, five outings and twelve races.
No predicted action remains outside its kind's selected scopes. The last three
outing references include 108 hash-verified consecutive 250 ms source samples.
Training reference windows cover the full 1,776.717 seconds; training previews
remain excluded. The reusable corpus scorer verifies reference hashes, rejects
same-kind scope overlaps and reports predictions outside selected scopes.

The selected training references now explicitly declare completeness within
their reviewed sampling scopes. Eighteen replacements bind existing completed
reviews and a newly completed dense review of 08:40–09:40, preserving action
identities and timing labels. No selected reference retains unspecified
completeness metadata. Other action kinds still have gaps in their declared
scopes, and seven race references are explicitly incomplete. Their twelve
matching race labels therefore do not produce a passing race agreement gate.
None of these sampled development checks establishes native-frame recall.

The third recording's initial 4 FPS baseline is frozen before source-guided
changes: 10,099 sampled frames across 42:04.583, with source, code, model and
output hashes sealed. Five contiguous evaluation windows were selected from
duration alone before viewing its frames or predictions. The opening 90-second
window has now been source-labeled from all 360 samples and compared with that
baseline. It produces no false run actions, effect receipts, lesson/concert
transactions, skill purchases, race results, stat checkpoints, or owned-skill
attributions while the user browses setup menus, support skill descriptions,
and an older character's inventory. This is a negative-window result.

The 10:31–12:01 source review exposes additional failures: the frozen predictions
match one of three labeled actions and 17 of 41 labeled effects. A Wit result
loses action identity when its heading/effect observations fragment; a race is
reported at its later reward panel rather than within the reviewed entry/result
sequence. Three planned concert values are also missed. These are scoped counts,
not full-recording accuracy.

That window's transaction-reference provenance required correction. A source
observation for Run n' Run! was initially omitted from evaluator registration,
then added after the first prediction comparison. The reference, seal and scores
were overwritten during that amendment. A separate chronology audit now records
the prior hashes, unavailable original bytes, unchanged channels and amended
transaction result. Its three matching transactions are an amended-reference
comparison and must not be presented as an untouched initial score. The frozen
baseline report itself remains unchanged.

The 21:02.250–22:32.250 window matches 33 of 40 labeled receipt effects. Its
action and effect references explicitly remain incomplete; an additional training
prediction without a source-labeled commitment is unassessed, not a demonstrated
false action. A completed race matches its observed identity/course/result fields,
while item quantities remain unresolved. The source shows eight named skill
purchases totaling 1,234 SP; the baseline captures that debit and candidate names
without verifying each purchase. Missing optional panels do not supply zero
values or complete inventory claims.

The preselected 31:33.250–33:03.250 window now has source-first labels for all
360 quarter-second samples, with full-width checks where the cinematic changes
the gameplay layout. Its frozen baseline matches 13 of 14 explicit receipt
effects and emits two extras: a duplicate hint with an OCR `O` suffix and a song
name missing its visible musical note. All three completed lesson purchases are
found, with correct reviewed stat awards, but one readable cost remains unknown
and the song identity lacks its note. Each expected cost is independently bound
to visible before/after balances for all five performance currencies. The
unknown cost follows a missing Dance reading in the confirmation even though
the surrounding observed balances are readable.

A later component-only replay recovers that Visual 24 debit from repeated actual
balances and the named purchase receipt, while preserving the initial score.
The same rule also recovers two earlier costs in the third recording: Passion 21
plus Visual 21 for Run for Our Dream!, and Vocal 16 for Vocal Training Intermediate
Class. Post-prediction review of all 41 quarter-second samples from 14:08.750 to
14:18.750 supports those two additional transactions. This is development
adjudication, not another independent result. All 139 lesson records in the first
two reports remain unchanged. The selected third-run transaction check still
fails on the missing musical note in Present March's identity.

A bounded development probe inspects 23 frames over 750 ms around the duplicated
hint. Multiple clear symbol observations let the existing reconstruction rule
retain one circle-marked award and preserve the alternate OCR spelling as
uncertain evidence. This improves the window's component score from two extra
effects to one, with 13 of 14 labels still matched. The remaining discrepancy is
the song's musical note. The source labels and frozen baseline are unchanged.

The automatic receipt planner now detects this unresolved symbol/OCR identity
pair without sending either spelling or an expected amount to OCR. Replaying
the planner on the frozen baseline selects its interval within the default
30-second/480-frame budget: nine windows total 29.002 seconds and an estimated
475 frames. This is a scheduling check; the successful 750 ms probe was selected
manually before the planner change, not executed by that plan. Existing dense
coverage remains excluded from subsequent work.

A full cached-source reparse, followed by both source-bound dense inspections
and complete reconstruction, confirms the hint recovery and all three lesson
costs together. The third-run candidate has 53 balanced performance intervals
and eight unresolved, compared with 50 balanced and eleven unresolved in the
baseline. Stat accounting remains 89 balanced and 42 unresolved. Dense training
inspection restores the Wit action using repeated result identities, improving
the quarter-window action match from one of three to two of three. Its SP gain
readings remain conflicted; recovering an action does not verify all its rewards.
The replay preserves the frozen baseline and passes exact reconstruction. These
counts describe a development candidate, not a fully verified recording or a
passing Go readiness gate.

A subsequent automatic pass starts from that reconstructed candidate and selects
nine different uncertain windows, totaling 29.253 seconds with a 480-frame cap.
It decodes 442 new frames, rechecks their source evidence, and reconstructs a
separate candidate. Neither ledger improves. Additional OCR name variants and
duplicate hint identities appear, so this output is retained as a diagnostic
candidate. The quarter-window effect match drops from 17/41 to 9/41 when the
inheritance receipt's start shifts 33 ms earlier; the twenty effect identities
in that receipt are unchanged. This exposes a mismatch between event-start
scoring and sampled reference bounds, alongside real identity/grouping problems
elsewhere. The original references and all prior results remain preserved;
denser sampling alone is not an accuracy guarantee.

The extra hint copies revealed a general grouping bug: high-confidence malformed
receipt text was classified as narrative and split one continuous award into
four events. Damaged hint-receipt grammar can preserve continuity only when its
full visible name and amount match an unconflicted accepted award. Plausible
companion receipt lines remain uncertainty; they supply no accepted names or
amounts. Different names or amounts cannot establish continuity. Narrative, other
screens and gaps over 500 ms still end the sequence. Replaying the same dense
observations counts Pace Strategy +3 once instead of four times. A remaining
friendship-name variant still needs identity review, and neither numeric ledger
changes; this correction does not make the diagnostic candidate ready to publish.

The complete automatic training inspection captures 5,878 observations across
57 windows (195.5 seconds of footage), reusing the earlier Wit probe. Source
revalidation and reconstruction improve stat accounting from 89 balanced and
42 unresolved intervals to 127 balanced and four unresolved. Checkpoint bounds
remain identical, and none of the matched intervals has a larger absolute
residual in any field. Performance accounting stays at 53 balanced and eight
unresolved. These are accounting diagnostics, not a full event-recall score.

That candidate retained stat gaps at 660250–686000 ms (SP +11),
1300000–1359250 ms (SP +67), 1645750–1658500 ms (SP −4), and
2226000–2282500 ms (Speed +8, Power +5, Guts +32, SP +18).
Residuals are recorded for investigation; they are not invented awards.
The combined candidate remains separate from the frozen baseline and published
reports. Inventory, effect identity, timing conventions and coverage still
require review before the Go gate can pass.

The subsequent `stat-currency-replay-v1` development candidate has 129 balanced
stat intervals and two unresolved intervals. Complete fixed-label receipt
normalization recovers the race's 67 SP while retaining the actual OCR text.
A separate continuity check suppresses the repeated 4-SP receipt only across
distinct, ordered source frames with a stationary stat line and compatible
visible values. The later friendship receipt remains counted. Reordered frames,
reused evidence, contradictory amounts and scene boundaries reject that bridge.

The existing wide currency crops were also applied to all 133 incomplete base
menu and confirmation observations. The pass took 6.85 seconds after reader
initialization, recovered 134 previously unknown field observations, and changed
no already accepted balance value. Four lesson costs now have matching observed
debits: Composure Training Intermediate Class (Composure 16), Rap Basics
(Vocal 8 and Composure 8), Precious Treasure Box (Dance 42 and Visual 26), and
Vocal Theory Intermediate Class (Vocal 14 and Composure 10). Performance
accounting now has 59 balanced and four unresolved intervals; the extra readable
checkpoints also change its interval count from 61 to 63.

The two remaining stat gaps are SP +11 at 660250–686000 ms and the unobserved
training-result gains at 2226000–2282500 ms. Repeated full-versus-partial OCR
field sets are retained as diagnostic training-phase candidates, without
resolving the SP +11 conflict: missing OCR fields do not prove a separate
animation phase. Remaining currency gaps concern one lesson debit and three
training intervals. Sampled effect-recall and identity checks still fail, so
this accounting improvement does not close the Go gate or establish whole-run
semantic verification. Frozen references and the initial baseline are unchanged.
The candidate SHA-256 is
`b8fde6565ee78e213e1b0fc7f6ef69ecae6ceaeee84d08d16f8d0d0d4ad04cd4`.

Complete source reparses of the first two recordings produce byte-identical
candidates to their preceding inventory integration. They retain 143/143 and
130/130 balanced stat intervals, all 14 reviewed visible inventory names each,
and the previously verified level and circle details. All non-inventory
gameplay collections also match the active local reports exactly. These
regressions and the 894-test suite pass; the three candidate reports remain
separate from the active reports.

The same window has no false training, rest or outing actions. Its Arima Kinen
goal-completion screen does not locate the earlier race action, and its Grand
Concert lobby does not establish a completed concert or rewards. The last lesson
confirmation crosses the window boundary, so the closed-transaction score ends
at 33:02.500 and leaves that purchase unassessed. The source-visible song bonus
is queued until the concert; optional Concert Info and owned-skill panels are
not opened. These limitations remain separate from the readable recognition
failures. Other selected windows and whole-recording accuracy are not yet fully
validated; these initial failures are preserved before further fixes.

Race references match all twelve results, all 118 labeled identity, course,
placing and fan fields, and all twelve exact-frame item-quantity comparisons.
All three reviewed negative race windows also pass. The latest crop policy
recovers four previously incomplete quantity snapshots; 55 source-bound
refinement artifacts passed strict validation. This does not establish item
identities or reward-list completeness. All six bounded choice labels match:
three doctor-event choices and three additional source-reviewed choices. Two missing selections
were recovered through exact-text native card rereads and native-frame selection
marks. Shrinking cards retain the established option list, unreadable menus
invalidate identity, and conflicting text or geometry clears tentative history.
Every card still requires support from distinct source timestamps. Correlated
crop provenance is retained in the report. This is not full-recording choice recall.
All 48 labeled values across eight reviewed concert panels match their references. Unseen owned-skill pages
and unexposed concert values remain unknown.

The development effect-reference index now has 45 disjoint sections, with no
overlap or revision-selection tie. It preserves 254 selected groups containing
876 effect labels and all 7,107 quarter-second sample timestamps. Original
references, parent hashes and label provenance remain separate from the derived
development slices. Two references explicitly declare incomplete labels; 43
legacy references leave completeness unspecified.

The current second report has 35 missing or extra effect entries in these diagnostics.
Including the cursor's white outline removes a false hint identity and allows
clear neighboring evidence to resolve a friendship recipient. The earlier
separator rule still removes one duplicate hint while retaining its unresolved
rank suffix. Numeric interval accounting is unchanged. Reference completeness,
recognition failures and unobservable fields still need adjudication. Neither
full semantic recall nor Go readiness is established.

A bounded energy-receipt check examines 30 existing native frames from 176.5
to 177 seconds in the second recording. All 19 readable receipt views retain
cursor overlap over the second digit and sentence terminator. Comparing visible
pixels with same-font digit templates favors zero, but the similarity has not
been validated as a recovery rule. The analyzer therefore retains an unknown
amount, and the expected -20 remains a recognition miss in the reference score.
No additional OCR or reference relabeling was used. The local
`energy-native-inspection-v2/energy-glyph-template-audit-v3.json` records this
method's limits; it does not rule out future recovery from partial glyphs.

The outlined-star pass recovers the complete Full Speed Ahead! Umadol Power☆
receipt in seven original-run frames and nine second-run frames. Both reports
reproduce from the cached evidence without OCR. The change removes one missing
song label and its extra truncated-name prediction from the second-run diagnostic.
Both lesson purchases retain their observed costs and original request names;
the receipt spelling changes without claiming that the request's suffix was
readable. All 77 original-run and 62 second-run lesson purchases, all performance
accounting, and concert quantities and activation flags remain unchanged. The
run-local `song-star-candidate-v2` artifacts preserve the previous reports,
source-row changes, replay checks and publication hashes.

A bounded inheritance review checks all 28 quarter-second samples from 519.5
through 526.25 seconds. It confirms the Straightaway Recovery spelling and
that the reference already includes Sympathy's separate +2 hint award. A new
reference variant records that the full T.M. Opera O inspiration name is not
proven in its two visible sampled receipts. All labels and the denominator
remain unchanged: this 60-second reference still matches 38 of 42 effects and
does not pass. The variant is separate from the current reference index.

A subsequent 105-frame native probe from 520.35 to 522.1 seconds supplies
several visually clear readings of Mejiro Ryan and Mihono Bourbon. Source-frame,
gameplay-pixel and OCR hashes were checked for every probe frame; 12 selected
frames received visual review. The same probe also emits many confident
misspellings while confetti crosses the names. It remains separate from the
canonical report: more OCR frames alone do not safely resolve those identities.
The local `inheritance-reference-review-v1` and `inspiration-native-probe-v1`
directories preserve the adjudication, candidate readings and limitations.

Repeated readable skill cards and receipt-prefix observations now recover
`Winter Runner ○` with hint amount 2 in the second recording. Source-bound
preparation stores card, circle, prefix crop and raw OCR evidence; normal report
replay validates the cache without OCR. A cache prepared against older cursor
bounds correctly fails after reparsing, while a cache prepared from the refreshed
rows passes. Invalid caches stop report publication. Conflicting candidates are
checked together so their order cannot select a skill amount or rank.

Inheritance spark identity tracking retains `Straightaway Recovery` in the
second recording and `Straightaway Acceleration` in the original. Truncated
alternatives remain recorded as ambiguous observations. Adjacent receipt
geometry and a matching neighboring line establish continuity; a longer string
or a skill catalog does not decide the identity. This does not establish a hint
award from a spark. The two fixes reduce the second report's effect diagnostics
from 42 to 40 without changing any labels, actions or numeric ledgers.

The identity batch changes one outcome in the original recording and two in the
second. Both reports reproduce exactly through model-free reconstruction.
Baselines, candidates, source-row changes and publication hashes are preserved
under each run's `identity-integration-v2`. The second report's race, action and
preserved-reference checks are in `identity-validation-v2`. Skill-card cache
preparation and stale-cache checks are retained in `hint-card-recovery-v1`.

Source-pixel song refinement now recovers the musical note in `Hoppity Sunny
Days ♪` across seven original-run and three second-run frames. The raw OCR
suffix `D` remains in provenance. Strict note/quote geometry, a separate title
crop read, and letter-column coverage must all agree. An adversarial extra real
letter is rejected even when the title OCR confidently omits it. Unsupported
punctuation, joined glyphs and wrapped star suffixes still abstain.

The corrected receipt stays linked to the original request through its preserved
raw spelling and repeated debit evidence. Both request spellings remain recorded
when they alternate. All 77 original-run and 62 second-run lesson purchases,
their costs, and both complete performance-accounting outputs are preserved.
One song outcome changes per recording, along with its linked name metadata;
no mechanic quantity changes. Both final reports replay exactly without OCR.
Candidates and publication checks are in each run's `song-refinement-v4`, and
the second report's preserved-reference scores are in `song-validation-v2`.
This reduces its diagnostics from 40 to 38 without rewriting reference labels.

The post-fifth-concert review decoded every frame in two bounded spans, but
visually reviewed overviews and transition representatives. A later coverage
audit found that those representatives do not establish exhaustive frame-level
absence. No current-bonus panel was found in the reviewed views; final totals
remain unknown. The corrected scope is preserved in
`post-fifth-active-bonus-native-adjudication-v2.json`. Neither this review nor
the earlier first-page inventory checks closes the complete-mechanics gate.

Both published reports now carry the validated producer/consumer schema version.
The latest cursor check reparsed 24 potentially affected timestamps across both
recordings through the source-bound production loaders. Replaying the old policy
first reproduced all 24 existing rows exactly. Checkpoints, action receipts,
purchases, concert records and numeric balances remain unchanged. The original
run loses one cursor-covered friendship name as well as a false OCR name; another
friendship identity resolves from clearer neighboring evidence. These changes
preserve uncertainty instead of treating high OCR confidence as visibility.
Previous report bytes are retained in each run's `cursor-padding-probe-v1`;
refinement sidecars, historical crop proofs and the initial separate-recording
evaluation remain preserved. Legacy unversioned capture adaptation is restricted
to the producer; generic consumers require an explicit supported schema.

Current report SHA-256 values are
`23aabf34aedb735fab7f54eec3db56de4427977fc6cfe7f760c1f1e5e1310a4d`
(original) and
`95e117aedf222fdfdf89cdb351f7c08100d9df626846724913e32d2af39e8846`
(second). The action corpus index SHA-256 is
`8189172fb21291b1cc5761a4a6e17239f3985ad5b42db3f1bd68b3d137be25c5`
and its report-bound score is in
`cursor-padding-validation-v1/action-score.json`. The disjoint effect index is
`reference-index-v6/index.json`, with SHA-256
`c9538df54b33ff40f0002d20562507c54a8bb87510227131e21a1fea178c5919`;
its current report-bound score is in `cursor-padding-validation-v1/effect-score.json`.

## Earlier 473-test snapshot

The race-day totals layout supplies eight additional stable checkpoints across the two recordings. The current reports contain 143 balanced six-field intervals in the original recording and 130 in the second. This measures agreement between observed states and recognized numeric effects, not complete event recall.

Hint identity recovery now preserves intrinsic punctuation, reads omitted or wrapped circle suffixes from source pixels, and retains receipt continuity across one explicitly observed cursor occlusion. Both report comparisons preserve numeric checkpoints and interval balances. The final continuity rebuild changes only one event, removing a duplicate hint spelling while retaining the obscured frame as continuity evidence. All 473 automated tests and all 12 preserved initial-reference regression checks pass.

The source-review manifest covers 1,445 seconds (81.33%) of the second recording, continuously from its beginning through 24:05. This is sampled review coverage, not accuracy or native-frame recall. Corrected reference boundaries and counts remain separately versioned and disclose prediction exposure. In particular, a training receipt and a later support-event receipt may each report the same maximum friendship status; neither asserts an additional numeric gain.

These report snapshots have SHA-256 values `95db47ff81e504c2936007e52e0f32c84365e3faf9a8d5707c1211d62a4c3da5` (original) and `536b279281740be4ebe19ff87467b95f99023c27266a5b1e689d9ee7ce8a45e3` (second). Full-recording semantic recall and Go readiness remain unproven.

## Earlier regression comparison

The subsequent friendship-status identity check preserves these checkpoints and numeric intervals. It reports conflicting names as uncertain and distinguishes them from different recipients passing through the same row as dialogue scrolls. All 442 automated tests and all 12 checks against the preserved initial evaluation references pass.

A broader before/after regression checks all 29 registered sampled effect references for the second recording. Only the 13:10–14:10 reference changes: matched effects fall from 28/29 to 27/29 because one previously accepted recipient is now unresolved; its false extra spelling is also removed. This remains a recognition gap. The declared reviewed duration is 985 seconds (55.439%), including overlapping references counted once. These are development measurements; neither full-recording effect recall nor Go readiness is established.

Report SHA-256 values for this comparison are `3ea28a8b7e5c33a3a5c24016de1119a06332d56935927e1b6f75f7b91507a6ff` (original recording) and `729e12ae018629bd4729db1df0362649da557a4b27c94be69f731273a141019d` (second recording). Earlier benchmark results below retain their original scope and counts.

## Source coverage

- 7,466 base frames at 4 FPS, no sampling gap above 250 ms, final sample 83 ms before the end.
- Detected training windows inspected at 30 FPS; six difficult animations additionally inspected at native 60 FPS. A decoder interval rounding bug that previously skipped alternate native frames was fixed and tested against a generated 60 FPS video.
- All 11,569 base and supplemental observations passed the source-frame, crop-pixel and original-OCR hash audit. All 8,526 present refinements matched their original observations and proof images.
- Recognition uses only gameplay rectangle `(148, 0, 958, 1080)`. The auxiliary log is excluded.

These checks establish provenance and sampled coverage. They do not prove that a brief effect between samples was recognized.

## Accounting and actions

| Check | Result | Scope |
|---|---|---|
| Six-field state comparisons | 141/141 balanced | Five attributes and skill points |
| Performance resource comparisons | 137/137 balanced | Dance, Passion, Vocal, Visual and Composure |
| Fan comparisons | 11/11 balanced | Between 12 race-result totals, including concert awards |
| Turn actions | 78 | 59 training, 12 races, 5 outings and 2 rests |
| Dated career turns | 60/60 with exactly one action | No interior calendar gaps |
| Other action phases | 12 pre-debut, 6 finale | Generic phase labels do not individually identify turns |
| Lesson purchases | 77/77 observed debits | Named receipt, confirmation and matching balances |
| Skill batches | 803 SP and 2,237 SP | Both charges assigned across committed cart bundles |
| Concerts | 5/5 reward associations and update receipts | Stat/SP/fan aftermath and activation step |
| Song acquisitions | 20 paid, 2 story event receipts | Alternative OCR names grouped within a receipt |

All 78 detected action receipt screenshots were visually reviewed for identity and training option. The source-linked review is in `tests/fixtures/full-action-receipt-review-v1.json`. Images were selected from analyzer output, so this is **not an independent missed-action recall measurement**. The calendar cross-check supplies an additional omission check: it found five outings that arithmetic alone had not exposed.

Two Speed gains, +12 and +11, were automatically selected from visible complete-number candidates using surrounding state constraints. Separate visual review confirms the displayed gains. The report retains `independent_effect_verification: false` for the automatic resolution because the reconciled state is not independent numeric evidence. Reviewed annotations never become OCR inputs.

Final attributes agree between the completion hub and summary: **1482 Speed, 868 Stamina, 1090 Power, 713 Guts and 1147 Wit**. The completion hub shows **40 skill points**. Whole-career accounting from the first observed checkpoint leaves zero unexplained change in all six fields.

## Mechanics distinctions

The first skill batch has six committed cart targets, the second twelve. Selecting and removing a skill changes a projected counter; only the acquisition receipt establishes the batch. Prerequisite and upgrade prices are not added twice. Circle variants are read separately so an offered next rank is not mistaken for another purchase. Complete owned-skill inventory remains unverified.

All twelve races have readable identity, course and placing; all show first place. Entry screens are not completed races. Item icon identities remain unsupported.

All five concerts have linked stat/SP/fan aftermath and an explicit bonus-update receipt. This verifies activation, not every resulting value. Repeated later Concert Info values are recorded where visible; planned values stay separate. Story song acquisition does not imply a paid lesson or an invented bonus amount. Maximum energy, aptitude changes, acquired conditions, full-energy status and unchanged friendship are separate effects. A review list exposes receipt-like OCR fragments that the parser did not accept; their numbers never enter a ledger.

## Evaluation

249 automated tests pass, covering auxiliary-pixel isolation, source tampering, PTS continuity, native sampling, animated digit conflicts, cursor-obscured receipt digits, delayed lesson balances, canceled outings, cart removals/upgrades, circle variants, calendar omissions/duplicates and evaluation penalties. Skill confirmations accept singular and plural wording. Repeated completion-hub SP counters can support a skill charge even when the five-attribute grid is not visible; projected menu counters remain separate. Direct counter crops recover small digits missed by text detection, abstain on disagreement, and retain source hashes. Finish-confirmation balances are observations, not a completed action or a points deduction.

Consecutive skill receipts can be reconciled jointly between observed balances when repeated final-cart counters explain the full debit and no other SP transaction intervenes. Intermediate counters retain their projected status. Account fan totals after career completion are separate from race results. Repeated completed-training identity remains an action even when its reward digits are unreadable; reward completeness stays false.

All 58 reviewed unparsed receipt candidates now have matching accepted evidence in their nearby receipt. Four actual gaps required denser inspection: energy deductions of 18 and 14, unchanged friendship with Sirius Symboli, and maximum friendship with Light Hello. The source-linked fixture preserves the manual statements and screenshot hashes. This candidate-selected check does not measure effects that never became candidates. Recreation unlocks and inheritance inspiration/spark triggers are typed separately without inventing their rewards.

Two contiguous development references pass:

- Opening 00:25-01:22: 5/5 training/rest actions, correct options, no extras.
- Lessons and first concert 05:41-06:54: 11/11 transactions (10 lessons and one concert), exact reviewed costs and stat/fan awards, no extras. A canceled dialog is excluded. Decorative name symbols, choices, hype and complete non-stat effect recall are outside this score.

A fixed 01:00-01:16 source review checks all explicit bottom-dialogue effects visible in 64 consecutive 250 ms samples. The initial report matched 16 of 20 effects and emitted one false extra song name. Training-appearance unlocks, generic supporter announcements and conservative single-character song-name resolution bring this development reference to 20/20 with no extras. Generic announcements do not add to named-supporter counts. This is sampled interval recall, not full-recording or native-frame recall.

The earlier 15 reviewed point examples remain narrow fixtures, not a full-run accuracy score.

```powershell
python -m unittest discover -s tests -v
python -m tracen_replay.receipt_review tests/fixtures/full-action-receipt-review-v1.json .local/full-recording/run-01/report.json --evidence-root .local/full-recording/run-01 --output .local/full-action-receipt-evaluation.json
python -m tracen_replay.candidate_review tests/fixtures/unparsed-receipt-review-v1.json .local/full-recording/run-01/report.json --evidence-root .local/full-recording/run-01 --output .local/receipt-candidate-evaluation.json
python -m tracen_replay.transaction_evaluate tests/fixtures/lesson-concert-sequence-v1.json .local/full-recording/run-01/report.json --output .local/lesson-concert-evaluation.json
python -m tracen_replay.verify_evidence "C:/path/to/recording.mp4" --output .local/full-recording/run-01
```

## Separate-recording evaluation

A separate 29:36.717 recording was evaluated with revision `40fd1387d83edce0e42d2dde68989999783a51d2` before source-driven code changes. The base and fully refined initial reports, source/model hashes, source archive, environment and manual references are preserved locally under `independent-01/initial-base` and `independent-01/initial-refined`. The initial labels were reviewed directly from video before reading predictions.

The frozen pipeline processed 7,107 base frames, 60 dense training windows, and native-rate gap inspections. Its evidence audit verified 11,274 observations and 9,634 refinements with zero provenance errors. This checks evidence integrity, not interpretation accuracy.

| Frozen refined check | Result | Scope |
|---|---|---|
| Dialogue effects | 74/75 matched, one extra | Four dense intervals totaling 115 seconds; the mismatch is a missing musical-note symbol in a song name |
| Action identities | 4/4 matched | Three trainings and one race, including selected training options |
| Declared action details | Passed | Reviewed training gains, performance gains and race identity/course/fans |
| Lesson transactions | 7/7 found; one cost unresolved | Seven paid lessons in 19:55–20:30 |
| Concert transaction and aftermath | Passed | One concert and 19 aftermath effects in 21:45–22:05 |
| Final attributes | Five matched | Numeric result summary |
| Final skill purchase | Failed | The frozen classifier missed the singular confirmation wording and did not reconstruct the batch |

The effect reference covers approximately 6.47% of this recording, not whole-recording recall. Choice reconstruction now has a separate development evaluation; it does not change the frozen results above. Final inventory, active bonuses and race item identities are outside these passing checks. The initial failures remain preserved; changes guided by this recording and later scores on it are regression work, not a fresh independent evaluation.

Subsequent regression fixes bring the final-state/skill reference from 6/20 to 20/20 checks: final attributes, the two checked remaining-SP observations, the 1,234-SP charge, and twelve skill targets with costs and declared circle variants. This does not establish complete owned-skill inventory. All 62 reconstructed lesson costs are now observed, and all three skill charges (1,455, 60 and 1,234 SP) are assigned to cart bundles. The first two charges use joint receipt accounting, without claiming an independently observed intermediate balance.

The separate recording has all 124 stat intervals balanced, all 113 performance intervals balanced, and all 11 fan intervals balanced. Its consolidated evidence audit verifies 11,631 observations and 14,500 refinements with zero provenance errors, including the added training-currency refinements, native receipt inspections, 3,859 base choice observations and 30 dense choice frames. A subsequent scoped audit also verifies 75 new inheritance frames and 75 receipt refinements with zero errors; these are additional to the consolidated counts. These improvements are regression results. The original recording retains its balanced ledgers and both complete cart-charge assignments.

All 60 dated turns now have one action. The recording contains 60 training attempts, 12 races, five outings and one rest across dated, pre-debut and finale phases. One Wit attempt has a directly reviewed `FAILURE` banner: the action remains counted, while the lingering +17 Dance/+17 Passion display is retained as a projection rather than awarded resources. A support outing without energy recovery is supported by its confirmation, matching narrative, mood/friendship receipt and observed next date. Neither case is inferred solely from arithmetic.

A song receipt with a single uncertain trailing character can be associated with a purchase only when repeated request frames and repeated before/after resource balances support the debit. Both observed names remain in the report, with name identity unverified. This recovers a 42-Passion/21-Vocal purchase without declaring the ambiguous suffix correct. Standalone currency numbers are excluded from confirmation title candidates.

Native receipt frames recover the remaining Composure +10 event reward, corroborated by repeated large labeled award animations. A missing currency-refinement pass on newly sampled training frames exposed Composure +17; its one shorter-prefix OCR outlier is retained beside the repeated complete readings. Failed training still contributes no performance award.

Repeated labeled stat animations now corroborate event receipts. The Mile Championship aftermath recovers 57 SP where the cursor produced shorter numeric readings; both the animation proof and discarded receipt alternatives remain available. The concert reward is additionally supported by repeated 105-SP receipt readings and repeated labeled animation readings; one 135-SP OCR outlier remains recorded. The Arima aftermath now recovers 57 SP using two confident, stationary skill-point label reads bracketing seven confident number reads. Their source timestamps and label boxes remain in the animation evidence, alongside the repeated 57-SP receipts and the discarded 37-SP alternative. Inheritance now distinguishes the visible 54-stamina award from its separate cap increase, using complete spatially distinct labels and repeated nearby normal-stat receipts. Its lone 4-stamina OCR alternative remains recorded. Multiple conflicting receipt observations or an uncorroborated animation still prevent resolution. The 32-wit training award is now corroborated by current-value readings with an obscured cap and a later complete result counter. The partial reads do not establish a cap value or use the later career-hub checkpoint.

The optional receipt-symbol pass now recovers the visible musical-note suffix directly from gameplay pixels in both recordings. It requires stable note geometry and closing quotation marks, preserves the original OCR text, and does not consult a song catalog. Seven original-run frames and five separate-run frames pass scoped source-pixel and refinement-provenance checks. Against the unchanged preserved references, all 75 dialogue effects now match with no extras, including 17/17 in the scenario interval. All other preserved regression checks still pass. This remains a score over the same 115 seconds, not whole-recording recall.

## Remaining boundary

`fully_verified` and `go_ready` remain false. Full non-stat effect recall, complete choice coverage, complete skill inventory, uncertain song symbols, race item identities and all active concert bonus values are not yet validated. Some fields need additional visible UI evidence rather than more arithmetic. Remaining gaps concern full semantic coverage and uncertain identities. See [active gates](gameplay-only.md) and [reproduction guide](full-recording-analysis.md).

## Dialogue choice development

The optional choice-refinement pass stores gameplay-only card and selection-mark observations with raw OCR and screenshot hashes. Source review of the original eight predictions found a missing wrapped option and two selections incorrectly attached to an earlier menu. Version 2 joins text within each card and rejects associations contradicted by readable selection-card text. Six reviewed base candidates remain; the two stale associations now abstain. A bounded 60 FPS inspection adds the brief single response, bringing the report to seven reviewed candidates. The preserved doctor-event reference now scores 3/3 with no extra predictions in its evidence window. Dense frames feed only choice reconstruction and cannot change stat accounting. All 3,859 version 2 observations pass their raw/proof hash checks; version 1 artifacts remain archived. This is bounded regression development, not full choice recall. See [choice reconstruction](choice-reconstruction.md).

## Concert panel development

Eight manually reviewed Concert Info panels provide 48 current/planned field values. The current reports match all 48 with no incorrect values. The initial 46/48 result remains preserved: the two missing values came from one unreadable support-level display in the first recording. A refinement supported by three nearby source frames now recovers that display while retaining the original OCR and proof hashes. This panel-selected review does not establish missed-panel recall or final active bonuses. See [panel evidence and scope](concert-panel-validation.md).

## Final owned-card observations

The final-summary reader retains repeated visible owned-card text independently of skill purchases. Source review finds 14 visible cards on each first page; gameplay-panel refinement now recovers all 14 names in each recording with no extras. The reviewed details also match: four single-circle variants and unique-skill level 4 in the original recording, and six single-circle variants and level 5 in the separate recording. These are first-page observations, not complete inventories. Off-screen cards remain unknown, and unreadable details are not treated as absent. See [ownership evidence and limits](owned-skill-observations.md).

## Source-ordered coverage

The additional source-selected 02:40–04:40 reference contains 480 consecutive 250 ms samples. Its first comparison against the preserved report snapshot matches 50 of 58 labeled effects, with four unmatched predictions and no proof-hash errors. Timing boundaries and incorrect recipient/skill names require separate adjudication; this initial score is retained before those investigations. Together with previous references, the sample-manifest audit covers 365 seconds (20.544%) of the separate recording. This is declared sampled-effect coverage, not full-run accuracy or native-frame recall.

Source inspection of the adjacent frames corrected two receipt onset boundaries without changing any effect labels. The preserved boundary variant scores 52/58 against the same frozen report. A subsequent analyzer fix repairs only the fixed keyword in complete `Friewdship`/`Frendship` receipts, preserving recipient text, amount and confidence. Recipient occlusion checks cover the same spellings. The rebuilt report matches 54/58, with two incorrect-name extras; the two recovered effects are Light Hello friendship changes. All nine source-labeled completed actions match, while browsed options and the blocked race attempt are excluded. Both stat ledgers remain balanced, the frozen `regression-28` checks pass, and 324 unit tests pass. These results do not close full-recording recall or identity-validation gates.

The next reconstruction pass matches 55/58 effects with no extra predictions in this interval. It recovers a hint's single-circle suffix from gameplay pixels and a matching same-frame label. Later contiguous readings of the exact original OCR sentence remain alternate observations of that award, rather than a second hint; only the pixel-backed frames prove the circle. Recipient occlusion now also protects gaps where OCR omitted a hidden letter, removing an unsupported truncated identity.

The three remaining reference misses are energy −20 and two friendship recipient names. Source review finds the energy amount readable despite a cursor-affected glyph; it remains a recognition miss. The two full recipient identities are not source-proven, so a separate reference version records name observability exceptions without changing the 58-effect denominator or making the evaluation pass. All 347 unit tests and all 12 preserved `regression-31` checks pass. Full-recording recall and the Go gate remain open.

A bounded 60 FPS probe of 176500–177000 ms did not recover that energy amount. It decoded 30 frames; all 19 energy-bearing OCR observations still omitted the final zero and sentence terminator. The gameplay pixels show the expected amount, but the automated result remains unresolved. The unsuccessful probe is kept separately and was not added to the canonical recording or counted as improved recall. Repeating the same sampling and OCR method is not a supported next step.

Base frames and native inspection frames now use the same source-bound receipt-symbol path. Rebuilding the separate recording removes another duplicate `O` hint at 335200 ms while retaining its pixel-backed circle award and alternate OCR evidence. The reviewed 2:40–4:40 score remains 55/58 with no extras; all 348 tests and the preserved `regression-32` checks pass. This consistency fix does not add source-review coverage.

A new negative reference reviews all 40 samples in the first 10 seconds of the separate recording: setup menus and no gameplay award receipts. It scores zero extras. Together with the unchanged 115 seconds of earlier references, declared sampled-effect coverage is 125 seconds (7.035%). This is still not full-source or native-frame recall. The coverage auditor checks reference/sample hashes, avoids double-counting overlaps, and identifies 0:10–0:20 as the next unreviewed source interval. See [source review procedure](source-review-coverage.md).

The next two source-ordered intervals, 0:10–0:20 and 0:20–0:30, now pass 8/8 inheritance effects and the 1/1 introduction SP award. Persistent receipt lines across the interval boundary are not counted again. Combined declared sampled-effect coverage reaches 145 seconds (8.161%); the next uncovered window starts at 0:30. No analyzer or threshold changes were required.

The 0:30–0:40 source review exposes a cursor-covered friendship recipient. Initial recognition emitted Super Crek instead of Super Creek. Native sampling repeats the misspelling, so it is not accepted as independent spelling evidence. The occlusion check now protects the recipient-name span as well as the numeric amount. After the fix, the reviewed interval retains two correct effects and one unresolved name, with no extra wrong-name prediction. The source timing bracket was separately adjudicated because native sampling exposed the receipt 50 ms before the first 4 FPS reference sample; both reference versions and scores remain preserved. The Power training action passes 1/1. Both ledgers and the earlier frozen-reference regressions still pass. Declared sampled-effect coverage is 155 seconds (8.724%), including this failing reference; coverage is not accuracy.
