import unittest

from tests.test_neural_transactions import raw, line
from tracen_replay.vision import parse


class FriendshipStatusSpacingTests(unittest.TestCase):
    def test_correct_separator_does_not_change_recipient_or_receipt(self):
        text = "Friendship with Trainer Kiryuin didn't go up."
        effect, = parse(raw([line(text, (300, 820, 760, 850))]))['effects']
        self.assertEqual(effect['name'], 'Trainer Kiryuin')
        self.assertEqual(effect['raw_text'], text)
        self.assertNotIn('original_text', effect)
        self.assertEqual((effect['kind'], effect['value'], effect['amount']),
                         ('friendship_status', 'unchanged', 0))

    def test_missing_separator_is_repaired_without_changing_name(self):
        text = "Friendship with Trainer Kiryuindidn't go up."
        effect, = parse(raw([line(text, (300, 820, 760, 850))]))['effects']
        self.assertEqual(effect['name'], 'Trainer Kiryuin')
        self.assertEqual(effect['original_text'], text)
        self.assertEqual(effect['raw_text'], "Friendship with Trainer Kiryuin didn't go up.")


if __name__ == '__main__':
    unittest.main()
