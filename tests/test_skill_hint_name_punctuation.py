"""Receipt punctuation must not erase punctuation belonging to a skill name."""
import unittest

from tracen_replay.gameplay import effects_from_lines


class SkillHintNamePunctuationTests(unittest.TestCase):
    def test_remove_only_one_receipt_terminator(self):
        cases = [
            ("Let's Pump Some Iron!.", "Let's Pump Some Iron!"),
            ("Another Exciting Skill!.", "Another Exciting Skill!"),
            ("Plain Skill.", "Plain Skill"),
            ("Plain Skill!", "Plain Skill"),
            ("Plain Skill", "Plain Skill"),
        ]
        for receipt_name, expected in cases:
            with self.subTest(receipt_name=receipt_name):
                text = f"Gained 2 hint level(s) for {receipt_name}"
                effects = effects_from_lines([{"text": text, "confidence": 99}])
                self.assertEqual(len(effects), 1)
                self.assertEqual(effects[0]["name"], expected)
                self.assertEqual(effects[0]["amount"], 2)
                self.assertEqual(effects[0]["raw_text"], text)


if __name__ == "__main__":
    unittest.main()
