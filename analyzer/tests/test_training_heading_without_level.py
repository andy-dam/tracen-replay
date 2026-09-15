"""The training heading counts as identity when its level digit was not read."""
import unittest

from tracen_replay.training_identity import read_identity


def lines(heading='Guts Lvl', name='Incline'):
    return [dict(text=heading, confidence=100, box=[230, 168, 304, 194]), dict(text=name, confidence=100, box=[226, 200, 296, 227])]


class HeadingWithoutLevelTests(unittest.TestCase):
    def test_level_is_optional_on_a_result_frame(self):
        identity = read_identity(lines(), 'training_result', 'Guts')
        self.assertEqual((identity['training_name'], identity['training_level']), ('Incline', None))

    def test_level_is_kept_when_read(self):
        identity = read_identity(lines('Guts Lvl 1'), 'training_result', 'Guts')
        self.assertEqual(identity['training_level'], 1)

    def test_other_option_headings_are_still_rejected(self):
        self.assertEqual(read_identity(lines(), 'training_result', 'Speed'), {})


if __name__ == '__main__':
    unittest.main()
