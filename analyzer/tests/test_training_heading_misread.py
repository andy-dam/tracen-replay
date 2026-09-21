"""A misread "Lvl" or a weak heading still names the training option.

The spellings are the ones a recorded career produced: the option stayed
unknown for three turns although the heading and the training's name were on
screen.
"""
import unittest

from tracen_replay.training_identity import heading_option, parse_heading, read_identity


def lines(heading, confidence, name='Quiz', name_confidence=100):
    return [dict(text=heading, confidence=confidence, box=[233, 169, 311, 193]),
            dict(text=name, confidence=name_confidence, box=[228, 200, 280, 228])]


class HeadingMisreadTests(unittest.TestCase):
    def test_confident_heading_with_a_misread_lvl(self):
        self.assertEqual(heading_option(lines('Speed Lvi 3', 96, 'Exercise Bike')), 'speed')
        self.assertEqual(heading_option(lines('Wit LvI 3', 92)), 'wit')
        self.assertEqual(heading_option(lines('Wit Lvl 3', 94)), 'wit')

    def test_weak_heading_needs_the_known_name_of_the_same_option(self):
        self.assertEqual(heading_option(lines('Wit Lvl 3', 83)), 'wit')
        self.assertEqual(heading_option(lines('WitLvI 3', 78)), 'wit')
        self.assertEqual(heading_option(lines('Wil LvI 3', 81)), 'wit')
        # An unknown name, a name of another option or a weak name prove nothing.
        self.assertIsNone(heading_option(lines('Wil LvI 3', 81, 'Pop Quiz')))
        self.assertIsNone(heading_option(lines('Wil LvI 3', 81, 'Treadmill')))
        self.assertIsNone(heading_option(lines('Wil LvI 3', 81, 'Quiz', 90)))
        self.assertIsNone(heading_option(lines('Wil LvI 3', 60)))

    def test_a_wrong_letter_alone_is_never_enough(self):
        self.assertIsNone(heading_option(lines('Wil Lvl 3', 99, 'Pop Quiz')))
        self.assertIsNone(parse_heading('Wil Lvl 3'))
        self.assertEqual(parse_heading('Wil Lvl 3', exact=False), ('Wit', 3))
        self.assertIsNone(parse_heading('Level 3', exact=False))

    def test_two_different_headings_name_nothing(self):
        both = lines('Speed Lvl 3', 96, 'Turf') + [dict(text='Wit Lvl 3', confidence=96, box=[240, 170, 320, 194])]
        self.assertIsNone(heading_option(both))

    def test_the_name_is_attached_on_the_weak_heading(self):
        identity = read_identity(lines('Wil LvI 3', 81), 'training_result', 'Wit')
        self.assertEqual((identity['training_name'], identity['training_level']), ('Quiz', 3))
        self.assertEqual(identity['training_identity_evidence']['basis'],
                         'weak_heading_corroborated_by_known_training_name')
        self.assertEqual(read_identity(lines('Wil LvI 3', 81, 'Treadmill'), 'training_result', 'Wit'), {})


if __name__ == '__main__':
    unittest.main()
