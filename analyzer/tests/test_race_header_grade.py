import unittest

from tracen_replay.vision import _race_result_grade_observation, _split_race_grade


def header_line(text, confidence=99):
    return dict(text=text, confidence=confidence, box=[300, 430, 700, 452])


class RaceHeaderGradeTests(unittest.TestCase):
    def test_a_digit_grade_glued_to_the_title_splits(self):
        self.assertEqual(_split_race_grade('G1Hopeful Stakes'), ('G1', 'Hopeful Stakes'))
        self.assertEqual(_split_race_grade('G1 Hopeful Stakes'), ('G1', 'Hopeful Stakes'))
        self.assertEqual(_split_race_grade('G3Kyoto Cup'), ('G3', 'Kyoto Cup'))

    def test_a_letter_grade_still_needs_its_space(self):
        # OPEN and EXtra are words, not a grade glued to a title.
        self.assertEqual(_split_race_grade('OPEN Cup'), (None, 'OPEN Cup'))
        self.assertEqual(_split_race_grade('EXtra Stakes'), (None, 'EXtra Stakes'))
        self.assertEqual(_split_race_grade('EX Race'), ('EX', 'Race'))
        self.assertEqual(_split_race_grade('PRE-OP Sprint'), ('PRE-OP', 'Sprint'))

    def test_no_grade_and_lower_case_after_the_grade_are_left_alone(self):
        self.assertEqual(_split_race_grade('Hopeful Stakes'), (None, 'Hopeful Stakes'))
        self.assertEqual(_split_race_grade('G1hopeful'), (None, 'G1hopeful'))
        self.assertEqual(_split_race_grade('G3'), (None, 'G3'))

    def test_glued_and_spaced_headers_read_the_same_race(self):
        glued = _race_result_grade_observation([header_line('G1Hopeful Stakes')])
        spaced = _race_result_grade_observation([header_line('G1 Hopeful Stakes')])
        self.assertEqual(glued[0], 'G1')
        self.assertEqual(spaced[0], 'G1')
        self.assertEqual(glued[1]['title']['text'], 'Hopeful Stakes')
        self.assertEqual(spaced[1]['title']['text'], 'Hopeful Stakes')


if __name__ == '__main__':
    unittest.main()
