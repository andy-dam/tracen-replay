import unittest

from tracen_replay.training_outcome import (
    CLIPPED_SUCCESS_BASIS,
    banner_facts,
    classify_result_screen,
)

from tests import localdata

SOURCE_RAW = localdata.root(
    "prepared_snapshot_early", "independent-02/initial-baseline/neural/part-001-frame-000142.json"
)


def _line(text, confidence=99, box=(369, 684, 665, 772)):
    return {'text': text, 'confidence': confidence, 'box': list(box)}


def _result_scaffold(prefix='SUCCES'):
    """The gameplay-only OCR layout visible in source frame 142."""

    return [
        _line('Training', box=(150, 3, 227, 30)),
        _line(prefix),
        _line('Speed', box=(332, 794, 414, 825)),
        _line('Stamina', box=(523, 795, 618, 824)),
        _line('152/1600', box=(318, 833, 453, 875)),
        _line('171/1341', box=(710, 831, 844, 876)),
        _line('Guts', box=(347, 912, 402, 938)),
        _line('Wit', box=(547, 909, 596, 940)),
        _line('Skill Pts', box=(702, 910, 795, 941)),
        _line('127/1500', box=(316, 949, 456, 990)),
        _line('159', box=(716, 950, 788, 991)),
        _line('Skip', box=(521, 1037, 611, 1069)),
        _line('Quick', box=(671, 1037, 733, 1065)),
    ]


class ClippedSuccessTests(unittest.TestCase):
    def test_prefix_can_classify_unknown_only_with_complete_result_scaffold(self):
        self.assertEqual(
            classify_result_screen(_result_scaffold(), 'Training', 'unknown'),
            'training_result',
        )
        self.assertEqual(
            classify_result_screen([_line('SUCCES')], 'Training', 'unknown'),
            'unknown',
        )

    def test_prefix_needs_result_scaffold_and_preserves_observation(self):
        facts = banner_facts(_result_scaffold(), 'training_result')

        self.assertEqual(facts['training_outcome'], 'success')
        self.assertEqual(facts['success_banner'][0]['text'], 'SUCCES')
        self.assertEqual(facts['success_banner'][0]['parsed_value'], 'SUCCESS')
        self.assertEqual(facts['success_banner'][0]['clipped_final_glyph'], 'S')
        self.assertEqual(facts['success_banner'][0]['observation_basis'],
                         CLIPPED_SUCCESS_BASIS)

    def test_prefix_without_same_frame_result_geometry_stays_unknown(self):
        for lines in (
            [_line('SUCCES')],
            [_line('SUCCES'), _line('Speed went up by 2', box=(350, 900, 650, 950))],
            [_line('SUCCES', box=(100, 684, 665, 772))],
            [_line('SU', box=(369, 684, 665, 772))] + _result_scaffold()[2:],
        ):
            with self.subTest(lines=lines):
                facts = banner_facts(lines, 'training_result')
                self.assertNotIn('training_outcome', facts)
                self.assertNotIn('success_banner', facts)

    def test_clipped_failure_prefix_is_failure_with_the_same_proof(self):
        lines = _result_scaffold()
        lines.append(_line('FAILUR', box=(370, 685, 664, 771)))
        facts = banner_facts(lines, 'training_result')
        self.assertEqual(facts['training_outcome'], 'failure')
        self.assertEqual(facts['failure_banner'][0]['parsed_value'], 'FAILURE')
        self.assertEqual(facts['failure_banner'][0]['clipped_final_glyph'], 'E')
        self.assertEqual(facts['failure_banner'][0]['observation_basis'], CLIPPED_SUCCESS_BASIS)
        # Beside a clipped success prefix the failure prefix wins; nothing becomes success.
        lines.append(_line('SUCCES', box=(370, 685, 664, 771)))
        facts = banner_facts(lines, 'training_result')
        self.assertEqual(facts.get('training_outcome'), 'failure')
        self.assertNotIn('success_banner', facts)

    def test_failure_banner_wins_over_clipped_positive_prefix(self):
        lines = _result_scaffold()
        lines.append(_line('FAILURE!', box=(370, 685, 664, 771)))

        facts = banner_facts(lines, 'training_result')

        self.assertEqual(facts['training_outcome'], 'failure')
        self.assertNotIn('success_banner', facts)

    def test_malformed_scaffold_geometry_does_not_support_clipped_success(self):
        for box in (
            [611, 1037, 521, 1069],
            [521, 1069, 611, 1037],
            [521, 1037, float('nan'), 1069],
            [521, 1037, float('inf'), 1069],
            [-500, 1037, 1600, 1069],
        ):
            with self.subTest(box=box):
                rows = _result_scaffold()
                next(row for row in rows if row['text'] == 'Skip')['box'] = box
                self.assertNotIn('training_outcome', banner_facts(rows, 'training_result'))
                self.assertEqual(classify_result_screen(rows, 'Training', 'unknown'),
                                 'unknown')

    def test_preview_and_positive_gains_do_not_promote_prefix(self):
        self.assertEqual(banner_facts(_result_scaffold(), 'training_preview'), {})
        self.assertNotIn(
            'training_outcome',
            banner_facts([
                _line('SUCCES'),
                _line('Speed went up by 2', box=(350, 900, 650, 950)),
            ], 'training_result'),
        )


if __name__ == '__main__':
    unittest.main()
