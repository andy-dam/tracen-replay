import unittest

from tracen_replay.animated_performance import candidates
from tracen_replay.gameplay import effects_from_lines
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import parse


def line(text, confidence=99, box=(315, 899, 544, 932)):
    return dict(text=text, confidence=confidence, box=list(box))


def source_lines(receipt="Skil! Pts went up by 67."):
    return [
        line("+67", box=(472, 565, 624, 641)),
        line("Skill Pts", confidence=98.949, box=(498, 637, 648, 686)),
        line(receipt, confidence=95.781),
    ]


class OsakaHaiSkillPointTests(unittest.TestCase):
    def test_fixed_keyword_receipt_preserves_raw_text(self):
        for raw in ("Skijl Pts went up by 67.", "Skil! Pts went up by 67."):
            with self.subTest(raw=raw):
                effects = effects_from_lines([line(raw, confidence=95.781)])
                self.assertEqual(len(effects), 1)
                self.assertEqual(effects[0]["field"], "skill_points")
                self.assertEqual(effects[0]["amount"], 67)
                self.assertEqual(effects[0]["raw_text"], raw)
                self.assertEqual(effects[0]["normalized_text"], "Skill Pts went up by 67.")
                self.assertEqual(effects[0]["original_text"], raw)
                self.assertEqual(effects[0]["text_normalization"], "fixed_skill_points_label")

    def test_fixed_keyword_requires_a_complete_receipt_sentence(self):
        for raw, confidence in (
            ("Skil Pts went up by 67.", 99),
            ("Skil? Pts went up by 67.", 99),
            ("Skil! Pts cap went up by 67.", 99),
            ("Skil! Pts Bonus went up by 67.", 99),
            ("Skil! Pts went up by ?.", 99),
            ("Skil! Pts went up by 67.", 89.9),
        ):
            with self.subTest(raw=raw, confidence=confidence):
                self.assertEqual(effects_from_lines([line(raw, confidence)]), [])

    def test_animation_candidate_remains_diagnostic_without_repaired_caption(self):
        got = candidates(source_lines(), "event_outcome", stat=True)
        self.assertEqual(len(got), 1)
        candidate = got[0]
        self.assertEqual((candidate["field"], candidate["amount"]), ("skill_points", 67))
        self.assertIsNone(candidate["receipt_text"])
        self.assertNotIn("receipt_text_normalized", candidate)
        self.assertNotIn("receipt_text_normalization", candidate)
        self.assertNotIn("receipt_box", candidate)
        self.assertNotIn("receipt_confidence", candidate)
        self.assertEqual(candidate["gain_box"], [472, 565, 624, 641])
        self.assertEqual(candidate["label_box"], [498, 637, 648, 686])

    def test_repaired_caption_amount_must_match_badge(self):
        got = candidates(source_lines("Skil! Pts went up by 6."), "event_outcome", stat=True)
        self.assertEqual(len(got), 1)
        self.assertIsNone(got[0]["receipt_text"])
        self.assertNotIn("receipt_text_normalization", got[0])

    def test_repaired_caption_direction_must_match_positive_badge(self):
        got = candidates(source_lines("Skil! Pts went down by 67."), "event_outcome", stat=True)
        self.assertEqual(len(got), 1)
        self.assertIsNone(got[0]["receipt_text"])

    def test_multiple_repaired_captions_are_ambiguous(self):
        lines = source_lines()
        lines.append(line("Skijl Pts went up by 67.", confidence=95.1,
                          box=(315, 940, 544, 973)))
        got = candidates(lines, "event_outcome", stat=True)
        self.assertEqual(len(got), 1)
        self.assertIsNone(got[0]["receipt_text"])
        self.assertNotIn("receipt_text_normalization", got[0])

    def test_preview_and_non_outcome_screens_remain_rejected(self):
        lines = source_lines()
        for screen in ("training_preview", "training_result", "unknown", "lesson_confirmation"):
            with self.subTest(screen=screen):
                self.assertEqual(candidates(lines, screen, stat=True), [])

    def test_source_shaped_receipt_reaches_event_without_ocr(self):
        raw = dict(lines=source_lines(), regions={}, header="Career",
                   current_grid=False, result_grid=False)
        parsed = parse(raw)
        self.assertEqual(parsed["screen"], "event_outcome")
        self.assertEqual(parsed["effects"][0]["field"], "skill_points")
        self.assertEqual(parsed["effects"][0]["amount"], 67)
        self.assertEqual(parsed["effects"][0]["raw_text"], "Skil! Pts went up by 67.")
        self.assertEqual(parsed["effects"][0]["normalized_text"], "Skill Pts went up by 67.")
        self.assertEqual(parsed["effects"][0]["original_text"], "Skil! Pts went up by 67.")
        row = dict(parsed, source_timestamp_ms=1328000,
                   evidence="initial-baseline/gameplay/part-011-frame-000033.png")
        events = outcome_events([row])
        self.assertEqual(len(events), 1)
        skill_effects = [e for e in events[0]["effects"]
                         if e.get("kind") == "stat_change" and e.get("field") == "skill_points"]
        self.assertEqual([e["amount"] for e in skill_effects], [67])


if __name__ == "__main__":
    unittest.main()
