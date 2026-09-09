# Source-ordered effect review

Full-source sampling and balanced ledgers do not measure missed effects. Review must also include intervals where no effect was predicted. The coverage audit accepts explicitly supplied effect references, checks each sample timestamp and screenshot hash, merges overlapping intervals without double counting, and lists every remaining gap.

Run:

```powershell
python -m tracen_replay.review_coverage RUN_DIRECTORY REFERENCE_1 REFERENCE_2 --output COVERAGE_JSON
```

Reference paths are relative to the run directory. The tool never reads predictions to choose the next window. Its next window is the first uncovered source interval, limited to ten seconds for review. Generating the coverage file does not create labels or certify that existing labels are correct. Sample spacing and individual reference scope remain in the output; even complete interval coverage would not establish native-frame recall.

## Separate recording

The four preserved effect references cover 115 seconds. A new source-ordered review covers 0:00–0:10: forty consecutive 250 ms gameplay samples show support-card browsing, setup confirmation, projected TP costs and strategy selection, with no explicit gameplay award receipts. The empty effect reference passes with zero predictions; precision and recall remain undefined because there are no positives. This does not score support-deck configuration or account-resource transactions.

Combined declared sampled-effect coverage is now 125 seconds, or 7.035% of the 1776.717-second recording. The next source-ordered window is 0:10–0:20. The untouched initial evaluation remains unchanged; new reviews are development evidence.

For each new window, inspect source screenshots before predictions, preserve all sample timestamps and hashes, record explicit effect groups or an empty negative reference, then score missing and extra predictions. View ambiguous text at full resolution and inspect intervening frames where necessary. Contact sheets assist navigation; they do not excuse skipping unreadable frames. Keep between-sample limitations explicit. Continue through gaps rather than skipping ahead to interesting predicted events.
