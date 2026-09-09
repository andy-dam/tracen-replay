# Source-ordered effect review

Full-source sampling and balanced ledgers do not measure missed effects. Review must also include intervals where no effect was predicted. The coverage audit accepts explicitly supplied effect references, checks each sample timestamp and screenshot hash, merges overlapping intervals without double counting, and lists every remaining gap.

Run:

```powershell
python -m tracen_replay.review_coverage RUN_DIRECTORY REFERENCE_1 REFERENCE_2 --output COVERAGE_JSON
```

Reference paths are relative to the run directory. The tool never reads predictions to choose the next window. Its legacy next-window hint is limited to ten seconds; use the [whole-recording workflow](whole-recording-review.md) for consolidated triage and larger contiguous source-review blocks. Generating the coverage file does not create labels or certify that existing labels are correct. Sample spacing and individual reference scope remain in the output; even complete interval coverage would not establish native-frame recall.

## Separate recording

The four preserved effect references cover 115 seconds. A new source-ordered review covers 0:00–0:10: forty consecutive 250 ms gameplay samples show support-card browsing, setup confirmation, projected TP costs and strategy selection, with no explicit gameplay award receipts. The empty effect reference passes with zero predictions; precision and recall remain undefined because there are no positives. This does not score support-deck configuration or account-resource transactions.

Combined declared sampled-effect coverage is now 245 seconds, or 13.789% of the 1776.717-second recording, including the continuous opening 160 seconds. The next uncovered source begins at 2:40; the whole-recording queue schedules larger blocks from there. The untouched initial evaluation remains unchanged; new reviews are development evidence. Coverage includes the unresolved 0:30–0:40 reference and is not a passing-coverage percentage.

For each new window, inspect source screenshots before predictions, preserve all sample timestamps and hashes, record explicit effect groups or an empty negative reference, then score missing and extra predictions. View ambiguous text at full resolution and inspect intervening frames where necessary. Contact sheets assist navigation; they do not excuse skipping unreadable frames. Keep between-sample limitations explicit. Continue through gaps rather than skipping ahead to interesting predicted events.

The subsequent 0:10–0:20 reference matches eight inheritance effects: stamina +54 and cap +41, power +63 and cap +48, Sprint and Front Runner aptitude increases, and two skill-hint awards. The 0:20–0:30 reference matches the introduction reward of 120 SP. Inheritance receipts still visible at 0:20 are continuations of the preceding event, not new awards. Both intervals were reviewed from all forty samples before scoring, and have no missing or extra predictions. These nine new effects supplement the earlier 75 reviewed effects; their timestamps and screenshot hashes remain with the local references.

The 0:30–0:40 interval exposes a recipient-name error: the source receipt says Super Creek, while the initial report says Super Crek. Its initial effect score is 2/3 with one extra wrong-name prediction. The Power training action passes 1/1; browsing other training options does not create extra actions. A 33-frame native inspection repeats the misspelling under a stationary cursor, so repetition does not independently establish the spelling. Recipient-name occlusion is now checked alongside the amount. The original failed score and expected name remain preserved; review coverage includes failed references and must not be presented as passing coverage.

After the occlusion fix, the new interval scores 2/3 with the recipient unresolved and no extra name. Native sampling moves the receipt onset from the first sampled observation at 36.750 to 36.700 seconds. The reviewed 36.500-second frame contains only the result grid, so a separate reference version brackets onset after 36.500 rather than demanding the exact first 4 FPS sample time. Effect labels remain unchanged and the original score is preserved. No generic timing tolerance or expected-name correction was added to the analyzer.

## Negative completed-action references

Race entry, training browsing and support-event choices may occur without a completed turn action. An empty `actions` list is accepted only with both `no_completed_actions: true` and `independently_reviewed: true`, explicit nonempty `kinds`, and a valid interval. Missing labels are not implicitly a negative reference. A negative reference containing a completed action is rejected.

Any predicted completion of a scoped kind inside the interval is an extra and fails the evaluation. With zero expected actions, recall is undefined; precision is also undefined when there are zero predictions. Neither becomes an artificial 100% score. This evaluator checks action identity and timing, not screenshot hashes or result gains; retain the paired source manifest and observations.

The reviewed 2:20–2:30 interval contains three support-event awards and a debut race entry. Its effect reference matches 3/3 and its explicit negative action reference has zero predictions. The projected race reward is not an earned fan award, and entry confirmation does not establish race completion. Earlier 2:10–2:20 footage separately verifies Speed training and rest after backing out of training browsing.
