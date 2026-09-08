# Tracking award labels through animation

An award number and its field label can be readable in different frames. `award_tracking.tracked_stats` associates visible stat awards between two confident label observations without using checkpoint differences or expected rewards.

The association requires:

- Two label reads at confidence 97 or higher, separated by 50–250 ms. Whitespace variation is allowed in their names.
- Consecutive event-outcome frames no more than 50 ms apart, with the same recognizable field label and stationary label bounds. Each coordinate may differ by at most eight pixels.
- At least three distinct, confident number observations spanning at least 50 ms, inside the label anchors. Each number must lie directly above the label and meet the large-award geometry checks.
- One matching award caption. Cap and bonus captions, ambiguous numbers, conflicting values, missing labels, changed fields, moving labels and screen changes prevent that association.

Weak intermediate label reads constrain spatial continuity; they are not counted as confident field identifications. Field identity comes from the two anchors. Number values still require their own confidence threshold. The proof records both anchor timestamps, screenshots, original label text and boxes. Different crops of a frame do not supply independent timestamps.

The resulting observations enter the existing receipt/animation reconciliation. They do not automatically override a receipt conflict. Resolving one contradictory receipt still requires repeated complete agreeing receipts and a repeated visible award; the contradictory observation remains recorded.

The source-guided Arima regression contains two confident skill-point label anchors and seven `+57` readings within the anchor interval. This supports the recorded award without deriving it from the remaining SP difference. The method applies only to event outcomes; training previews and training result gains use their existing separate evidence rules.

## Simultaneous stat and cap awards

Inheritance can display a normal stat award and a cap increase together. A separate check accepts their coexistence only when the frame has confident, complete labels for both the stat and that stat's cap, separated by at least 30 pixels along one axis. Overlapping OCR fragments cannot establish two badges. The normal award retains the existing number, label and alignment thresholds.

The same event must also contain at least three distinct matching normal-stat receipt observations, spanning at least 50 ms and appearing within one second after the animation frame. Their text and evidence timestamps are recorded separately as `receipt_anchors`; a later receipt is never presented as text visible on an earlier animation frame. A cap-only display or cap receipt cannot satisfy this requirement.

These candidates still pass the existing repeated-animation and receipt-conflict checks. In the inheritance regression, seven `+54 Stamina` observations are distinct from the `Stamina cap` label and corroborated by later stamina receipts. The single `4` OCR alternative remains recorded; neither checkpoint differences nor the cap's `+7` value supplies the normal stamina award.
