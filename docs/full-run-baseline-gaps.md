# Full-run baseline gap register

**Review cut:** 2026-09-10. **Scope:** all seven reviewed sections of the original 31:06.333 recording, with source-label and observability limits below.

This register consolidates only source-confirmed gaps that remained after the
seven section reviews and their adjudications. A raw strict
matcher miss is not treated as an analyzer gap when an adjudication joins the
same receipt across a boundary, resolves a status synonym, or links a
persistent receipt to the committed action.

## Finished evidence

| Shard | Source interval | Evidence used |
| --- | ---: | --- |
| `source-a` | `0..180000 ms` | [scorecard](../.local/full-run-baseline-v1/source-a/scorecard.md), `adjudications.json`, `adjudicated-grade.json` |
| `middle-a` | `180000..300000 ms` | [scorecard](../.local/full-run-baseline-v1/middle-a/scorecard.md), `adjudicated-grade.json` |
| `tail-a` | `300000..480000 ms` | [scorecard](../.local/full-run-baseline-v1/tail-a/scorecard.md), `adjudicated-grade.json` |
| `source-b` | `480000..960000 ms` | [scorecard](../.local/full-run-baseline-v1/source-b/scorecard.md), `effect-grade.json`, `states-actions-grade.json`, `purchase-grade.json`, `balance-audit.json` |
| `source-c` | `960000..1320000 ms` | [scorecard](../.local/full-run-baseline-v1/source-c/scorecard.md), `adjudications.json`, `balance-audit.json` |
| `tail-c` | `1320000..1440000 ms` | [scorecard](../.local/full-run-baseline-v1/tail-c/scorecard.md), `draft-grade.json` |
| `source-d` | `1440000..1866333 ms` | [scorecard](../.local/full-run-baseline-v1/source-d/scorecard.md), `adjudications.json` |

Evidence files and detailed section artifacts live under the ignored `.local/` directory. The links resolve in this evaluation workspace; recordings and screenshots are not included in the repository.

Every image link below points to an original 810x1080 gameplay frame. The
source-a, source-b, source-c and source-d sealed labels remain unchanged; their later source-QA
amendments are overlays recorded by their adjudication artifacts.

## Source-confirmed effect and detail gaps

These rows have a visible source fact and a missing or incomplete frozen-report
field after adjudication.

Statuses are dimension-specific. **Correct** means the labeled field matches;
**incorrect** means a supplied value contradicts readable evidence (for example,
the `Hey, Guess Vnat!` alternative); **missed** means an expected observable
field/occurrence is absent; **ambiguous** retains competing readings; **partial**
means the occurrence is represented but required details are incomplete.
**Unobservable** means the evidence cannot establish the value, such as skills
below the recorded final page. **Ungraded** means this reference did not label or
adjudicate the dimension. Unavailable report endpoints and unknown outcomes are
reported separately from incorrect values. These categories must not be collapsed
into one accuracy percentage.

| Shard and time | Expected source fact | Frozen report actual | Classification | Evidence |
| --- | --- | --- | --- | --- |
| `source-a`, 00:12.000 (`12000 ms`) | The inheritance receipt shows **Inspiration Complete** / “Their legacies live on!”. | The frozen inheritance event has caps, stats, aptitudes and hints, but no `inspiration_complete` effect. | **Later analyzer improvement** | [Inspiration completion](../.local/full-recording/v1/gameplay/part-000-frame-000049.png) |
| `source-a`, 00:30.500–00:30.750 (`30500..30750 ms`) | Nishino Flower friendship **+7**. | The frozen receipt contains Kitasan Black **+7**, but no Nishino Flower friendship atom. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-000-frame-000123.png) |
| `source-a`, 00:39.500 (`39500 ms`) | Super Creek friendship **+7**. | The frozen receipt contains Agnes Tachyon **+7** and Fine Motion **+7**, but no Super Creek friendship atom. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-000-frame-000159.png) |
| `source-a`, 00:48.500 (`48500 ms`) | Fine Motion friendship **+7**. | The frozen receipt contains Energy **-19** and Agnes Tachyon **+7**, but no Fine Motion friendship atom. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-000-frame-000195.png) |
| `source-a`, 02:10.250 (`130250 ms`) | Breaststroke training energy decreases by **20**. | The frozen receipt contains four friendship atoms but no `energy_change` atom. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-001-frame-000042.png) |
| `source-a`, 02:25.750 (`145750 ms`) | Kitasan Black friendship **+7**. | The frozen receipt contains Energy **-17** and Light Hello **+4**, but no Kitasan Black friendship atom. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-001-frame-000104.png) |
| `source-a`, 02:35.250 (`155250 ms`) | Nishino Flower friendship **+7**. | The frozen receipt contains Energy **-20**, Kitasan Black **+7**, Agnes Tachyon **+7** and Director Akikawa **+2**, but no Nishino Flower friendship atom. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-001-frame-000142.png) |
| `middle-a`, 03:39.500 (`219500 ms`) | Wit training level becomes **2**. | `outcome-0039` identifies a wit level increase but has no resulting level. | **Later analyzer improvement** | [Wit level 2](../.local/full-recording/v1/gameplay/part-001-frame-000399.png) |
| `middle-a`, 03:46.000 (`226000 ms`) | Kitasan Black friendship **+7**. | `outcome-0040` keeps energy **-20** and Super Creek **+7**, but omits Kitasan Black **+7**. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-001-frame-000425.png) |
| `middle-a`, 03:51.000 (`231000 ms`) | Light Hello friendship **+4** and Agnes Tachyon **+7**. | `outcome-0041` contains energy **-18**, but neither friendship change. | **Later analyzer improvement** | [Training receipt](../.local/full-recording/v1/gameplay/part-001-frame-000445.png) |
| `tail-a`, 05:00 (`300000 ms`) | Winter Runner **○** hint **+2**. | `outcome-0053` has the base name and **+2**, but no verified circle tier. | **Later analyzer improvement; Go must preserve partial identity** | [Winter Runner hint](../.local/full-recording/v1/gameplay/part-002-frame-000241.png) |
| `tail-a`, 05:10 (`310000 ms`) | Agnes Tachyon friendship **+5**. | `outcome-0055` retains `Agnes` and `Ágnes` candidates as ambiguous; no accepted exact recipient. | **Later OCR/name improvement; Go must expose ambiguity and evidence** | [Agnes friendship](../.local/full-recording/v1/gameplay/part-002-frame-000281.png) |
| `tail-a`, 05:11.500 (`311500 ms`) | Speed training level after the receipt is **3**. | `outcome-0056` recognizes a speed level-up but omits the resulting level. | **Later analyzer improvement; Go must retain unknown numeric detail** | [Speed training level 3](../.local/full-recording/v1/gameplay/part-002-frame-000287.png) |
| `tail-a`, 07:28 (`448000 ms`) | Wit training level after the receipt is **3**. | `outcome-0077` recognizes a wit level-up but omits the resulting level. | **Later analyzer improvement** | [Wit training level 3](../.local/full-recording/v1/gameplay/part-003-frame-000353.png) |
| `source-c`, 16:02.750 (`962750 ms`) | Passion performance **+10**. | `outcome-0156` contains the other source-confirmed cap/performance rows but omits Passion **+10**. The corrected source evidence is 962750, not the earlier 962000 citation. | **Later analyzer improvement** | [Passion receipt](../.local/full-recording/v1/gameplay/part-008-frame-000012.png) |
| `source-c`, 16:20.250 (`980250 ms`) | Swing Maestro hint **+1**. | No matching report effect remains after event-boundary joins. | **Later analyzer improvement** | [Swing Maestro hint](../.local/full-recording/v1/gameplay/part-008-frame-000082.png) |
| `source-c`, 16:37.250 (`997250 ms`) | Agnes Tachyon friendship reaches **maximum**. | No matching friendship-status effect. | **Later analyzer improvement; absence must not imply a reset** | [Agnes maximum notice](../.local/full-recording/v1/gameplay/part-008-frame-000150.png) |
| `source-c`, 16:59.250 (`1019250 ms`) | Super Creek friendship reaches **maximum**. | No matching friendship-status effect. | **Later analyzer improvement; absence must not imply a reset** | [Super Creek maximum notice](../.local/full-recording/v1/gameplay/part-008-frame-000238.png) |
| `source-c`, 17:44.000 (`1064000 ms`) | Mood remains **Great**. | The mood value matches, but structured `direction: unchanged` is absent. | **Later metadata improvement; do not invent a mood increase** | [Unchanged mood](../.local/full-recording/v1/gameplay/part-008-frame-000417.png) |
| `source-c`, 17:54.750 (`1074750 ms`) | Acquired **Charming ○**. | `outcome-0175/0` supplies Charming without the circle tier. The base condition acquisition is correct. | **Later identity-detail improvement; preserve partial condition identity** | [Condition receipt](../.local/full-recording/v1/gameplay/part-008-frame-000460.png) |
| `source-c`, 21:56.000 (`1316000 ms`) | Super Creek friendship reaches **maximum**. | No matching friendship-status effect. | **Later analyzer improvement; absence must not imply a reset** | [Super Creek maximum notice](../.local/full-recording/v1/gameplay/part-010-frame-000465.png) |
| `tail-c`, 22:02.500 (`1322500 ms`) | Light Hello and Agnes Tachyon friendship reach **maximum**; Kitasan Black and Super Creek notices are also visible. | The two Light Hello/Agnes notices are absent while the other notices are present. | **Later analyzer improvement; Go must preserve status absence distinctly from reset** | [Friendship notices](../.local/full-recording/v1/gameplay/part-011-frame-000011.png) |
| `tail-c`, 22:32.250 (`1352250 ms`) | Wit training level becomes **5**. | The level-up kind and field are present, but the resulting level is null. | **Later analyzer improvement** | [Wit level receipt](../.local/full-recording/v1/gameplay/part-011-frame-000130.png) |
| `tail-c`, 22:55.250 (`1375250 ms`) | Super Creek and Nishino Flower friendship reach **maximum**; Light Hello is also shown. | The Super Creek and Nishino Flower maximum notices are absent. | **Later analyzer improvement; absence must not imply a reset** | [Friendship notices](../.local/full-recording/v1/gameplay/part-011-frame-000222.png) |
| `tail-c`, 23:42.500 (`1422500 ms`) | Light Hello friendship reaches **maximum**; the other three notices are shown. | The Light Hello maximum notice is absent. | **Later analyzer improvement; absence must not imply a reset** | [Friendship notices](../.local/full-recording/v1/gameplay/part-011-frame-000411.png) |
| `tail-c`, 23:50.000 (`1430000 ms`) | Mood is **Great** and remains unchanged. | Mood value is `great`, but the structured `direction: unchanged` field is absent. | **Schema/detail improvement; do not invent a mood increase** | [Outing receipt](../.local/full-recording/v1/gameplay/part-011-frame-000443.png) |
| `tail-c`, 23:57.250–23:57.750 (`1437250..1437750 ms`) | Festive Miracle changes **3 → 4**, with level amount **+1**. | Skill-level change and name are recognized, but before/after levels and amount are null. | **Later analyzer improvement; keep this distinct from a hint or purchase** | [Unique skill level 3 to 4](../.local/full-recording/v1/gameplay/part-011-frame-000472.png) |
| `source-d`, 25:58.250 (`1558250 ms`) | Concert preview shows **21 songs** and hype **max**, alongside the six numeric before/after bonus values. | Numeric bonus changes are present; song total and hype are not supplied. | **Later analyzer improvement; Go must preserve missing preview fields** | [Concert bonus preview](../.local/full-recording/v1/gameplay/part-012-frame-000474.png) |
| `source-d`, 26:15.500 (`1575500 ms`) | One purchase named **Hey, Guess What!**, with the acquisition conflict represented as `name_conflicted=true`. | The low-level effects include the alternative `Hey, Guess Vnat!`; the acquisition object consolidates one purchase, but the ledger says `conflicts_present=false`. | **Go integration blocker for conflict-free presentation; later name canonicalization** | [Song name conflict](../.local/full-recording/v1/gameplay/part-013-frame-000063.png) |
| `source-d`, 26:43.000 (`1603000 ms`) | The result is **Grand Concert — Great Success**. | Great Success is adjudicated correct from the earlier result, but the report's concert title remains null. | **Later analyzer improvement** | [Grand Concert result](../.local/full-recording/v1/gameplay/part-013-frame-000173.png) |
| `source-d`, 30:31.500 (`1831500 ms`) | The final bundle's prerequisite is **Corner Adept ○**. | Bundle cost and base skill name match, but the prerequisite circle tier is absent. | **Later analyzer improvement; Go must preserve partial skill identity** | [Final skill request](../.local/full-recording/v1/gameplay/part-015-frame-000122.png) |

The tail-a Passion **+10** and Visual **+10** at 07:46.000 are deliberately
not in this table as analyzer misses. Their strict rows were joined by the
adjudicator to the same `training-0029` performance effects after the badge
outlasted the detected stat-result span. The same rule removes the middle-a
boundary-spanning rewards, persistent receipts, and tail-c status synonyms
from the unresolved-gap list.

The source-a scripted-song guardrail is a representation requirement rather
than a missing report effect. At 01:05.500 (`65500 ms`), the source shows
`Make Debut!` learned as part of the scenario event, with no paid cost or
committed transaction. The frozen report also records a song effect with
`cost=null` and `committed_transaction=false`. Go must keep this out of the
purchase ledger; classify it as a **Go representation requirement**, not as a
purchase-recall failure. Evidence: [scripted Make Debut song](../.local/full-recording/v1/gameplay/part-000-frame-000263.png).

Source-a therefore has six amount-bearing omissions (five friendship **+7**
rows and one energy decrease **-20**) plus the separate, non-amount
`inspiration_complete` omission.

## Missing state snapshots and checkpoint coverage

These are separate from effect recognition. A source-visible state can be
absent at one literal timestamp while a later accepted checkpoint or consumer
ledger still has the same values.

Source-a contributes 18 normalized state-channel rows: exact timestamp
readback is 8/18 and accepted checkpoints are 14/18. The four rows below with
`accepted checkpoint: absent` are the accepted-state gaps; the other source-a
exact misses are shown separately where an accepted checkpoint recovered the
same values.

| Shard and time | Expected source snapshot | Frozen report actual | Classification | Evidence |
| --- | --- | --- | --- | --- |
| `source-a`, 00:30.250 (`30250 ms`) | Stats **150/190/193/112/150**, SP **125**. | Exact report reading omits Speed **150**; no accepted checkpoint is attached. | **Go must distinguish an absent field from zero/unchanged; later state-reader improvement** | [Training result state](../.local/full-recording/v1/gameplay/part-000-frame-000122.png) |
| `source-a`, 00:39.000 (`39000 ms`) | Stats **170/190/202/112/152**, SP **133**. | Exact report reading omits Stamina **190**; no accepted checkpoint is attached. | **Go must distinguish an absent field from zero/unchanged; later state-reader improvement** | [Training result state](../.local/full-recording/v1/gameplay/part-000-frame-000157.png) |
| `source-a`, 01:19.000 (`79000 ms`) | Stats **185/202/207/129/168**, SP **170**. | Exact report reading omits Speed **185** and Stamina **202**; no accepted checkpoint is attached. | **Go must distinguish an absent field from zero/unchanged; later state-reader improvement** | [Training result state](../.local/full-recording/v1/gameplay/part-000-frame-000317.png) |
| `source-a`, 01:56.250–01:56.500 (`116250..116500 ms`) | Stats **203/227/233/153/177**, SP **233**. | All six exact stats/SP fields are absent and no accepted checkpoint is attached. | **Go must expose an incomplete snapshot; later state-reader improvement** | [Training result state](../.local/full-recording/v1/gameplay/part-000-frame-000466.png) |
| `source-a`, 01:43.500 (`103500 ms`) and performance openings at 01:26.750, 01:39.500, 02:05.000, 02:19.750 and 02:30.500 | Stats at 01:43.500 are **198/227/233/153/168**, SP **224**; performance values are, in order, **10/25/10/10/10**, **10/25/27/10/10**, **30/25/27/10/38**, **30/42/27/10/38**, and **45/42/27/10/38**. | The exact report frames omit Stamina at 01:43.500 and omit all five performance fields at each listed opening; accepted checkpoints recover these values. | **Later analyzer exact-timestamp recovery; Go must retain the recovered state and its provenance** | [01:26.750 performance](../.local/full-recording/v1/gameplay/part-000-frame-000348.png), [01:39.500 performance](../.local/full-recording/v1/gameplay/part-000-frame-000399.png), [02:05.000 performance](../.local/full-recording/v1/gameplay/part-001-frame-000021.png), [02:19.750 performance](../.local/full-recording/v1/gameplay/part-001-frame-000080.png), [02:30.500 performance](../.local/full-recording/v1/gameplay/part-001-frame-000123.png) |
| `middle-a`, 03:46.500 (`226500 ms`) | Early-September opening performance: Dance **59**, Passion **42**, Vocal **27**, Visual **19**, Composure **74**. | `turn-016` has no accepted performance opening at that checkpoint. | **Go representation requirement; later checkpoint recognition improvement** | [Early September opening](../.local/full-recording/v1/gameplay/part-001-frame-000427.png) |
| `tail-a`, 05:20.500 (`320500 ms`) | Hopeful Stakes race-entry attributes: Speed **377**, Stamina **269**, Power **311**, Guts **195**, Wit **285**. | The five fields are visible but absent from the exact readback/accepted checkpoint for the turn opening. | **Go representation requirement; later state-reader improvement** | [Race entry totals](../.local/full-recording/v1/gameplay/part-002-frame-000323.png) |
| `tail-a`, 06:15 (`375000 ms`) | Post-purchase stats: **407/277/314/198/305**, SP **502**. | Literal values are read correctly, but no accepted state exists in the narrowly bounded pre-reward window. | **Checkpoint coverage improvement; not an action or cost failure** | [Purchase confirmation totals](../.local/full-recording/v1/gameplay/part-003-frame-000061.png) |
| `tail-a`, 07:59.500 (`479500 ms`) | Late-February stats **458/287/361/231/358**, SP **664**, performance **65/49/30/36/92**. | Performance fields are absent at this exact frame; the consumer ledger recovers all five at 08:00.250. | **Go must distinguish exact-frame absence from recovered consumer state** | [Late-February totals](../.local/full-recording/v1/gameplay/part-003-frame-000479.png) |
| `source-c`, exact source anchors `970000..1308000 ms` | Six turn-opening snapshots have source values at 970000, 992000, 1001000, 1026000, 1053000 and 1308000 ms. | Several literal readings land at nearby report timestamps (for example 970250/970750 and 1309750/1310250); accepted checkpoints still carry the same values. | **Source-label/timestamp coverage limit; not an analyzer value error** | [970000 anchor](../.local/full-recording/v1/gameplay/part-008-frame-000041.png), [1308000 anchor](../.local/full-recording/v1/gameplay/part-010-frame-000433.png) |
| `tail-c`, 22:03.000–23:43.000 (`1323000..1423000 ms`) | Performance openings include, respectively, **93/89/76/47/73**, **117/89/89/87/130**, **117/89/89/104/147**, **117/135/109/128/169**, and **147/135/139/128/169**. | 25 performance fields are absent at their exact labeled frames; the consumer ledger's 20/20 opening snapshots recovers these values. | **Go must retain exact-frame missingness separately from consumer state; later timestamp coverage improvement** | [22:03.000 opening](../.local/full-recording/v1/gameplay/part-011-frame-000013.png), [22:37.500 opening](../.local/full-recording/v1/gameplay/part-011-frame-000151.png), [22:50.250 opening](../.local/full-recording/v1/gameplay/part-011-frame-000202.png), [23:36.750 opening](../.local/full-recording/v1/gameplay/part-011-frame-000388.png), [23:43.000 opening](../.local/full-recording/v1/gameplay/part-011-frame-000413.png) |
| `source-d`, 24:30.500 (`1470500 ms`) | Transaction state: stats **1369/749/960/565/1002**, SP **1676**. | The exact reading supplies five fields but leaves Speed null; no accepted checkpoint is attached to that exact source interval. | **Go must distinguish absent field from zero/unchanged; later state-reader improvement** | [Transaction balance](../.local/full-recording/v1/gameplay/part-012-frame-000123.png) |
| `source-d`, 25:03.750 (`1503750 ms`) | Transaction state: stats **1375/749/966/597/1014**, SP **1676**. | All six literal fields are absent at the selected frame, while an accepted checkpoint at 25:04.000 recovers the same values. | **Source timestamp coverage limit; not a lost consumer state** | [Transaction balance](../.local/full-recording/v1/gameplay/part-012-frame-000256.png) |
| `source-d`, 27:00.750 (`1620750 ms`) | Pre-qualifier state: stats **1390/815/1008/646/1044**, SP **1915**; performance **19/55/19/14/15**. | Neither numeric channel has an accepted checkpoint in the corresponding source interval. | **Go must expose incomplete state history; later checkpoint coverage improvement** | [Pre-qualifier balances](../.local/full-recording/v1/gameplay/part-013-frame-000244.png) |
| `source-d`, 30:48.000 (`1848000 ms`) | The visible final summary contains stats **1482/868/1090/713/1147** and a visible owned-skill list. | Those visible fields match, but rows below the page are unobservable; the source does not establish a complete inventory. | **Go representation requirement: mark the inventory partial; no complete-inventory claim** | [Final visible inventory](../.local/full-recording/v1/gameplay/part-015-frame-000193.png) |

The source-d concert preview at 25:58.250 also has a narrower state gap:
numeric before/after bonus fields are present, while total songs and max hype
are missing. It is kept in the effect/detail table above because it is a
preview metadata omission rather than a missing career stat checkpoint.

## Training outcomes: one grouped gap

Across the finished shards there are **54** source-visible completed training
receipts whose source result is success, while the frozen report leaves
`training_outcome=unknown` for each: source-a **9**, middle-a **9**, tail-a
**4**, source-b **16**, source-c **6**, tail-c **7**, and source-d **3**. This is one
outcome-metadata gap, not 54 missed action identities. The source action
arrays/entries are:

- source-a `labels.json#/labels` plus the nine `source-a-training-outcome-*` entries in `adjudications.json#/decisions`, represented by [the first training result](../.local/full-recording/v1/gameplay/part-000-frame-000122.png);
- middle-a `adjudicated-grade.json#/actions` (nine training rows among ten accepted actions), represented by [a source training receipt](../.local/full-recording/v1/gameplay/part-001-frame-000281.png);
- tail-a `adjudicated-grade.json#/results` and `labels.json#/labels` for `at-0004`, `at-0107`, `at-0120`, and `at-0131`, represented by [at-0004](../.local/full-recording/v1/gameplay/part-002-frame-000257.png);
- source-b `states-actions-grade.json#/actions/rows` (16 training results), represented by [the training receipt](../.local/full-recording/v1/gameplay/part-004-frame-000373.png);
- source-c `labels.json#/labels` plus `adjudications.json#/adjudicated/action` (`result_success_source_rows=6`), represented by [the 972000 training](../.local/full-recording/v1/gameplay/part-008-frame-000058.png);
- tail-c `draft-grade.json#/results` for its seven training action rows, represented by [ct-0013](../.local/full-recording/v1/gameplay/part-011-frame-000026.png);
- source-d `labels.json#/labels` and `adjudications.json#/decisions/3` (`d-0093`, `d-0116`, `d-0140`), represented by [the stamina training receipt](../.local/full-recording/v1/gameplay/part-013-frame-000223.png).

Classification: **Later analyzer result recognition improvement. Go must
preserve `unknown` separately from success and failure.** The source-c Speed
Running commitment at 21:59.000 (`1319000 ms`) is owned by source-c; its
result, post-state and receipt are in the sealed tail-c supplement. It is not a
second training outcome in the count above.

## 08:00–16:00 effect and state gaps

The final section contributes eight missing scalar/status effects, three partial race-reward lists, and the metadata/state gaps below. Its 282 normalized effect rows are adjudicated as 253 correct, eight missed, 13 partial and eight ungraded; none is adjudicated incorrect after source QA. These counts include contextual and grouped rows, so they must not be added to other sections' differently defined effect denominators.

| Source time | Source fact and report discrepancy | Classification | Screenshot evidence |
| --- | --- | --- | --- |
| 08:04.750 (`484750 ms`) | Source receipt visibly says Light Hello +2; outcome-0082 reports Energy -13 and Agnes Tachyon +7 only. | Later analyzer improvement | [part-004-frame-000020](../.local/full-recording/v1/gameplay/part-004-frame-000020.png) |
| 08:58.000 (`538000 ms`) | Source receipt visibly says Fine Motion +5; outcome-0092 reports Speed +5 and Skill Pts +10 only. | Later analyzer improvement | [part-004-frame-000233](../.local/full-recording/v1/gameplay/part-004-frame-000233.png) |
| 09:06.500 (`546500 ms`) | The sealed frame at 546000 is early in the receipt; full-size 546500 shows Light Hello maxed out. outcome-0094 has Guts/SP/Visual only. | Later analyzer improvement | [part-004-frame-000267](../.local/full-recording/v1/gameplay/part-004-frame-000267.png) |
| 09:50.000 (`590000 ms`) | Source frame 590000 shows both Agnes Tachyon +7 and Director Akikawa +2. Report outcome-0100/1 correctly captures Director Akikawa +2 for its own line; Agnes Tachyon +7 is omitted. | Later analyzer improvement | [part-004-frame-000441](../.local/full-recording/v1/gameplay/part-004-frame-000441.png) |
| 10:32.750 (`632750 ms`) | Source receipt shows both Super Creek +7 and Fine Motion +7. Report outcome-0106/1 correctly captures Fine Motion +7 for its own line; Super Creek +7 is omitted. | Later analyzer improvement | [part-005-frame-000132](../.local/full-recording/v1/gameplay/part-005-frame-000132.png) |
| 13:51.750 (`831750 ms`) | Source receipt says Kitasan Black is maxed out; report outcome-0139 has Director +2 and Agnes max only. | Later analyzer improvement | [part-006-frame-000443](../.local/full-recording/v1/gameplay/part-006-frame-000443.png), [part-006-frame-000448](../.local/full-recording/v1/gameplay/part-006-frame-000448.png) |
| 13:53.250 (`833250 ms`) | Source post-training receipt says Director Akikawa +5; report outcome-0140 has Skill Pts +2 only. | Later analyzer improvement | [part-006-frame-000454](../.local/full-recording/v1/gameplay/part-006-frame-000454.png), [part-006-frame-000456](../.local/full-recording/v1/gameplay/part-006-frame-000456.png) |
| 13:59.500 (`839500 ms`) | Source receipt says Sirius Symboli did not gain friendship; report outcome-0141 has Skill Pts +15 only. | Later analyzer improvement | [part-006-frame-000462](../.local/full-recording/v1/gameplay/part-006-frame-000462.png), [part-006-frame-000479](../.local/full-recording/v1/gameplay/part-006-frame-000479.png), [part-007-frame-000001](../.local/full-recording/v1/gameplay/part-007-frame-000001.png) |
| 10:03.000 (`603000 ms`) | Source shows shoes x1, medal x400, NHK Mile Cup trophy x1, and bonus card x20. The report race record has unnamed quantities x1/x400 plus bonus x20 and omits the trophy quantity. | Later item-recognition improvement; Go must preserve incomplete lists and unknown item identities | [part-005-frame-000013](../.local/full-recording/v1/gameplay/part-005-frame-000013.png) |
| 15:02.250 (`902250 ms`) | Source shows shoes x1, medal x400, Mile Championship trophy x1, bonus medal x400, and bonus card x20. The report has unnamed item quantities x1/x400/x1 and bonus medal x400 but omits the bonus card. | Later item-recognition improvement; Go must preserve incomplete lists and unknown item identities | [part-007-frame-000250](../.local/full-recording/v1/gameplay/part-007-frame-000250.png) |
| 15:33.750 (`933750 ms`) | Source shows shoes x1, medal x400, and Arima Kinen trophy x1. The report has unnamed item quantities x400/x1 and omits the shoes quantity. | Later item-recognition improvement; Go must preserve incomplete lists and unknown item identities | [part-007-frame-000376](../.local/full-recording/v1/gameplay/part-007-frame-000376.png) |
| 09:31.750 (`571750 ms`) | Source Director Akikawa +2 is represented as Director Akixawa +2. The amount and occurrence match, but the spelling is wrong. | Later name/metadata improvement; retain partial identity | [part-004-frame-000368](../.local/full-recording/v1/gameplay/part-004-frame-000368.png), [part-004-frame-000373](../.local/full-recording/v1/gameplay/part-004-frame-000373.png) |
| 09:36.750 (`576750 ms`) | The source shows Stamina training level 2; the report records the level-up occurrence and direction but omits the resulting level. | Later name/metadata improvement; retain partial identity | [part-004-frame-000389](../.local/full-recording/v1/gameplay/part-004-frame-000389.png) |
| 10:27.000 (`627000 ms`) | The report records Wit training level-up and direction but omits after_level=4. | Later name/metadata improvement; retain partial identity | [part-005-frame-000109](../.local/full-recording/v1/gameplay/part-005-frame-000109.png) |
| 10:34.500 (`634500 ms`) | The report records Speed training level-up and direction but omits after_level=4. | Later name/metadata improvement; retain partial identity | [part-005-frame-000139](../.local/full-recording/v1/gameplay/part-005-frame-000139.png) |
| 14:10.750 (`850750 ms`) | The report records Speed training level-up and direction but omits the resulting level=5. | Later name/metadata improvement; retain partial identity | [part-007-frame-000044](../.local/full-recording/v1/gameplay/part-007-frame-000044.png) |
| 14:20.750 (`860750 ms`) | Source Practice Perfect ○ is represented as Practice Perfect. Base condition acquisition matches; the tier is not supplied. | Later name/metadata improvement; retain partial identity | [part-007-frame-000084](../.local/full-recording/v1/gameplay/part-007-frame-000084.png), [part-007-frame-000088](../.local/full-recording/v1/gameplay/part-007-frame-000088.png), [part-007-frame-000089](../.local/full-recording/v1/gameplay/part-007-frame-000089.png) |

The race item names above are visual reference annotations of icons, not verified text labels or a validated general icon classifier. Quantity omissions are separable from item identity. The Nakayama Racecourse hint has a circle in the prediction but no tier in the sealed source label: its tier is **ungraded**, not a missing report circle. Six inheritance spark names are represented by six report atoms while the source uses one grouped row; that is a representation difference, not six omissions. Planned song/concert bonuses remain distinct from awarded effects.

| Source time | Expected numeric state | Report coverage | Classification | Evidence |
| --- | --- | --- | --- | --- |
| 10:37.000 | Performance Dance/Passion/Vocal/Visual/Composure **89/51/56/52/85**. | An accepted checkpoint recovers the values, but the applicable consumer turn opening is unavailable. | Later opening-state recovery; Go must show unavailable opening | [Opening](../.local/full-recording/v1/gameplay/part-005-frame-000149.png) |
| 14:51.750 | Stats **817/422/599/324/556**, SP **1184**; performance **88/81/29/65/69**. | Both later channel checkpoints are absent. Earlier Power **591** is valid before the intervening Power **+8** lesson award; it is not an incorrect prediction. | Later checkpoint recovery; preserve observation times | [Later state](../.local/full-recording/v1/gameplay/part-007-frame-000208.png), [Power +8](../.local/full-recording/v1/gameplay/part-007-frame-000173.png) |
| 15:23.750 | Performance **88/81/47/65/87**. | Exact reading matches all five values, but an accepted performance checkpoint is absent. | Later checkpoint coverage; do not describe this as missing OCR values | [Career state](../.local/full-recording/v1/gameplay/part-007-frame-000336.png) |

The [state/action grade](../.local/full-run-baseline-v1/source-b/states-actions-gaps.md) also lists all 21 exact-frame performance omissions. Most recover through other checkpoints; missing literal frames are not automatically missing turn states. The three absent accepted snapshots and one absent applicable consumer opening above use distinct denominators.

All 22 purchases have matching names and source debit vectors. All 242 numeric field intervals balance against the frozen report ledger, while the original source-only effect labels leave 14 residual fields. Two are explicit source transcription amendments (+1 → +8 and +52 → +55); the other 12 remain ungraded causal attribution. The [reference residual table](../.local/full-run-baseline-v1/source-b/reference-residuals.md) preserves every interval and amount. None is silently converted into a source-verified effect because report totals balance.

## Required cross-recording representations

### `source_ref` rewards and capped values

Tail-c has two source-confirmed Light Hello performance awards that are
available through references to the original event effects: Visual **+20** at
22:11.750 and Vocal **+20** at 22:57.750. The convenience
`performance_changes` summary omits them, but both `source_ref` resolutions
reproduce the correct amount. Reading the summary alone therefore creates a
false omission; summing the summary and the referenced effect creates a double
count. This is a **Go representation/integration requirement**, not an analyzer
recognition failure. Evidence: [Visual +20](../.local/full-recording/v1/gameplay/part-011-frame-000048.png) and [Vocal +20](../.local/full-recording/v1/gameplay/part-011-frame-000232.png).

Source-c's Precious Treasure Box demonstrates a second amount distinction.
The confirmation preview at 20:15.500 shows nominal Speed **+26**, current
Speed **1195 (+15)** and the note that gains over 1,200 are halved; the later
receipt at 20:19.500 awards **+15**, which is the value used for grading and
balance arithmetic. The source-QA overlay corrects the sealed nominal label
without changing the sealed source file. Go must retain both
`nominal_preview=26` and `awarded=15`, and use the awarded amount for the
ledger. Evidence: [nominal capped preview](../.local/full-recording/v1/gameplay/part-010-frame-000063.png) and [awarded +15 receipt](../.local/full-recording/v1/gameplay/part-010-frame-000079.png).

### Cross-shard ownership at 21:59–22:03

The source-c action selected Speed Lvl 5 / Running at 21:59.000 and was still
Connecting before the 22:00 boundary. Source-c owns the commitment; tail-c
owns the result and following state. The tail result shows Speed **+46**,
Power **+44**, SP **+36**, and Dance/Composure **+32**, followed by opening
stats **1261/694/908/503/744**, SP **1299**, and performance
**93/89/76/47/73**. Go must preserve the two owners and link them rather than
drop the action at the boundary or duplicate it. Evidence: [source-c
commitment](../.local/full-recording/v1/gameplay/part-010-frame-000477.png), [tail-c result](../.local/full-recording/v1/gameplay/part-011-frame-000007.png), and [tail-c opening state](../.local/full-recording/v1/gameplay/part-011-frame-000013.png).

### Coarse finale grouping

From 26:50.750 through the recording end, report `turn-072` groups three
trainings and three races under one finale countdown segment. It explicitly
sets `action_status=multiple_actions`, `expects_one_action=false`,
`next_turn_boundary_unobserved=true`, and `complete_event_history=false`.
The expected six source actions are represented by `d-0093`, `d-0106`,
`d-0116`, `d-0130`, `d-0140`, and `d-0171` in
`source-d/labels.json#/labels`; examples are [stamina training](../.local/full-recording/v1/gameplay/part-013-frame-000223.png),
[qualifier](../.local/full-recording/v1/gameplay/part-013-frame-000281.png), and
[finals race](../.local/full-recording/v1/gameplay/part-014-frame-000122.png).

This is a **Go integration blocker** for a consumer that assumes one action per
turn row. Finer segmentation of the finale is a **later analyzer improvement**.
The UI must show the coarse group, the six observed actions, and the unobserved
next boundary without claiming six independently verified turn boundaries.

## Source-label coverage versus analyzer omissions

Source-label coverage limits are source-side facts: targeted cadence and
repeated-frame review, absent exact-timestamp fields with later accepted
checkpoints, source-a, source-b and source-c residual transitions where matched report
atoms already exist or no source effect label was recorded, source-d
intermediate transaction checkpoints, native-frame recall, absolute continuous
energy, hidden conditions, and ungraded narrative/item details. Those are
coverage or ungraded attribution limits, not analyzer omissions. The analyzer
omissions are the rows above where the screenshot
proves a typed effect or metadata field and the frozen report has no field or
only a partial field. Raw unmatched queues and strict matcher counts are not
precision claims and must not be converted into analyzer-error totals.

For source-c, the bounded balance audit makes this distinction explicit. The
following residuals are present in both the source checkpoint delta and matched
report atoms, but their source effect labels do not assign the intervening
fields; they are **source-label coverage/ungraded attribution**, not analyzer
errors:

| Source interval | Source checkpoint delta left in the residual after labeled effects and purchases | Matched report residual | Evidence |
| --- | --- | --- | --- |
| `970000..992000 ms` | Speed **+20**, Wit **+47**, SP **+23**, Passion **+31**, Vocal **+18**, Composure **+13** | Same six residual fields and amounts | [970000 state](../.local/full-recording/v1/gameplay/part-008-frame-000041.png) |
| `1001000..1016000 ms` | Speed **+54**, Power **+21**, SP **+21**, Vocal **+32**, Visual **+32** | Same five residual fields and amounts | [1001000 state](../.local/full-recording/v1/gameplay/part-008-frame-000165.png) |
| `1026000..1053000 ms` | Speed **+59**, Dance **+25**, Visual **+25** | Same three residual fields and amounts | [1026000 state](../.local/full-recording/v1/gameplay/part-008-frame-000265.png) |
| `1053000..1142000 ms` | Guts **+5** | Guts **+5** | [1053000 state](../.local/full-recording/v1/gameplay/part-008-frame-000373.png) |
| `1142000..1308000 ms` | Passion **+15**, Vocal **+15** | Passion **+15**, Vocal **+15** | [1142000 state](../.local/full-recording/v1/gameplay/part-009-frame-000249.png) |

Source-a has five analogous source-reference residual fields across its
sealed state pairs. The state deltas are real, but the source review has no
intervening effect label for the residual; the frozen report was not used as a
verified endpoint for this check. They are **source-label coverage/ungraded
attribution**, not analyzer errors:

| Source interval and channel | Source state delta | Intervening source-labeled effect delta | Residual | Evidence |
| --- | --- | --- | --- | --- |
| `86750..99500 ms`, stats | Guts **142→153 (+11)**, Stamina **202→219 (+17)**, SP **200→217 (+17)** | Guts **0**, Stamina **0**, SP **+10** | Guts **+11**, Stamina **+17**, SP **+7** | [86750 state](../.local/full-recording/v1/gameplay/part-000-frame-000348.png), [99500 state](../.local/full-recording/v1/gameplay/part-000-frame-000399.png) |
| `86750..99500 ms`, performance | Vocals **10→27 (+17)** | Vocals **0** | Vocals **+17** | [86750 state](../.local/full-recording/v1/gameplay/part-000-frame-000348.png), [99500 state](../.local/full-recording/v1/gameplay/part-000-frame-000399.png) |
| `99500..125000 ms`, performance | Composure **10→38 (+28)** | Composure **0** | Composure **+28** | [99500 state](../.local/full-recording/v1/gameplay/part-000-frame-000399.png), [125000 state](../.local/full-recording/v1/gameplay/part-001-frame-000021.png) |

Source-a's other limits are also source-side: the 500 ms navigation plus
targeted 250 ms review does not establish exhaustive native-frame
recall; absolute energy, hidden conditions, whole-inventory recall and
text-exhaustive narrative are ungraded; the Junior Make Debut reward quantity
200 is visible but its item identity is not text-identifiable; and the
After-the-Debut receipt crosses the 180000 ms handoff, with only its first
Speed **+3** observation owned by source-a. These limits do not establish
analyzer errors.

The source-c Passion **+10** at 962750 is different: it is a source-confirmed
typed occurrence with no matching report effect, so it remains an analyzer gap
in the first table. A zero balance residual only proves arithmetic closure; it
does not prove exhaustive event attribution.

## Go blockers and later improvements

The representation requirements for the first Go implementation are:

- represent missing, zero, unchanged, and unknown as distinct values;
- preserve ambiguous candidate names with evidence and conflict flags from the acquisition object;
- resolve `source_ref` effects once, without double-counting convenience summaries;
- link actions, results and states across time without duplicating them, and preserve the coarse multi-action finale group with an unobserved boundary (review sections themselves are evaluator bookkeeping, not a required application concept);
- preserve partial skill tiers, partial inventories, component prices, aggregate skill-batch debit, and nominal versus awarded capped amounts.

Later analyzer work covers the missing friendship/hint/performance effects,
training and skill levels, concert title, name canonicalization, training
success recognition, and more complete exact-timestamp/checkpoint recovery.
These requirements are acceptance gates for Go's report presentation, not a
requirement to build Go before starting Go. The evaluation does not require
another recognition-fix cycle before implementation begins.

## Explicit exclusions

- All seven sections are represented; the labeled evidence is not an exhaustive inventory of every visible fact.
- This document does not claim complete native-frame recall or complete event/title precision.
- Absolute energy bars, continuous hidden conditions, unobserved item/account rewards, spark outcomes, exhaustive narrative/dialogue, and whole-inventory recall remain ungraded in the finished shards.
- No raw unmatched prediction queue is counted as an analyzer error without a source-confirmed fact and a checked adjudication.
