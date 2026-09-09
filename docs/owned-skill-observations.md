# Owned skills in the final summary

`gameplay_tracking.owned_skill_inventory` records repeated visible skill-card text from the final summary. This is separate from selected cart items, committed purchase bundles and inheritance sparks. A skill already owned does not become another purchase when it appears in the summary.

The supported layout requires the Skills, Inspiration and Career Info tabs plus at least three recognized final attributes. Cards occupy two columns. Wrapped names are joined within one card; a low-confidence fragment rejects the whole card rather than producing a confident partial name. Two distinct observations no more than 500 ms apart must agree before a name enters the inventory observations. Evidence retains timestamps, card position, original text, confidence and boxes.

`name_text` is the observed text, not a verified full identity. Circle suffixes and unique-skill levels remain unverified, even when OCR drops them. The report always keeps inventory completeness false: this implementation does not establish scrolling coverage or off-screen ownership.

## Source review

The two supplied recordings each briefly show the first page with 14 skill cards. Their scrollbars visibly continue below that page. The current reader retains 12 repeated card names in the original recording and 13 in the separate recording. The original lacks repeated accepted readings for Pace Chaser Straightaways and Triple 7s; the separate run lacks repeated complete Front Runner Straightaways text. These are recognition gaps on visible cards, distinct from the off-screen inventory gap.

The separate run's two summary frames at 29:19.000 and 29:19.250 are preserved in `tests/fixtures/final-owned-cards.json`, with source identity and screenshot hashes. A readable Front Runner prefix in the second frame is not accepted because the following Straightaways fragment has low confidence. The earlier complete reading stays in per-frame evidence but cannot alone meet the repeat requirement.

This review does not establish complete inventory from the entire video. Earlier acquisition receipts and purchased bundles remain complementary evidence; automatic merging of prerequisites, upgrades and circle variants into a final complete inventory still requires validation.
