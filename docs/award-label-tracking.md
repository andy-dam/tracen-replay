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
