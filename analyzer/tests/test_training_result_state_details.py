import unittest
from pathlib import Path

from tracen_replay.stat_state_details import (
    read_goal_turns,
    read_training_result_values,
)
from tracen_replay.source_state_observations import build_observations
from tracen_replay.transactions import training_events
from tests import localdata
from tracen_replay.vision import parse


REPO = Path(__file__).resolve().parents[2]
T049_RAW = localdata.root(
    "development_third_recording_baseline", "neural/part-010-frame-000077.json"
)
FIRST_RECORDING_GOAL_RAW = localdata.root(
    "prepared_snapshot_final", "v1/neural/part-007-frame-000290.json"
)


def _line(text, box, confidence=99.0):
    return {'text': text, 'confidence': confidence, 'box': list(box)}


def _result_raw(result_text='556/1.0', result_box=(322, 834, 448, 876)):
    return {
        'lines': [
            _line('Training', (150, 1, 227, 32)),
            _line('SUCCESS!', (366, 687, 727, 777)),
            _line('Speed', (296, 790, 416, 828)),
            _line(result_text, (314, 830, 455, 876), 90.861),
        ],
        'regions': {
            'result.speed': _line(result_text, result_box, 94.169),
        },
        'header': 'Training',
        'result_grid': True,
        'current_grid': False,
    }


class TrainingResultStateDetailsTests(unittest.TestCase):
    def test_same_frame_labeled_crop_recovers_numerator_with_occluded_cap(self):
        values, proof = read_training_result_values(_result_raw())

        self.assertEqual(values, {'speed': 556})
        self.assertEqual(proof['speed']['basis'],
                         'same_frame_labeled_result_card_numerator')
        self.assertEqual(proof['speed']['cap_status'],
                         'unresolved_occluded_or_malformed')
        self.assertEqual(proof['speed']['cap_text'], '556/1.0')

    def test_result_numerator_requires_field_geometry_and_label(self):
        moved = _result_raw(result_box=(100, 834, 226, 876))
        self.assertEqual(read_training_result_values(moved), ({}, {}))

        missing_label = _result_raw()
        missing_label['lines'] = [
            line for line in missing_label['lines'] if line['text'] != 'Speed'
        ]
        self.assertEqual(read_training_result_values(missing_label), ({}, {}))

    def test_plain_short_cap_and_unreadable_numerator_remain_unknown(self):
        short_cap = _result_raw('556/160')
        self.assertEqual(read_training_result_values(short_cap), ({}, {}))

        unreadable = _result_raw('??/1.0')
        self.assertEqual(read_training_result_values(unreadable), ({}, {}))

    def test_conflicting_same_frame_detector_line_does_not_choose_a_value(self):
        sample = _result_raw()
        sample['lines'].append(_line('557/1.0', (314, 830, 455, 876), 99.0))

        self.assertEqual(read_training_result_values(sample), ({}, {}))

    def test_reader_supports_a_different_result_column_and_amount(self):
        sample = _result_raw()
        sample['lines'].extend([
            _line('Stamina', (525, 796, 618, 823)),
            _line('731/1.0', (507, 826, 652, 877), 91.0),
        ])
        sample['regions']['result.stamina'] = _line(
            '731/1.0', (518, 834, 644, 876), 92.0)

        values, proof = read_training_result_values(sample)

        self.assertEqual(values, {'speed': 556, 'stamina': 731})
        self.assertEqual(proof['stamina']['region']['text'], '731/1.0')

    def test_other_column_geometry_cannot_be_reassigned_to_a_field(self):
        sample = _result_raw()
        sample['lines'] = [
            line for line in sample['lines'] if line['text'] != 'Speed'
        ]
        sample['lines'].extend([
            _line('Speed', (525, 796, 618, 823)),
            _line('731/1.0', (507, 826, 652, 877), 91.0),
        ])
        sample['regions']['result.speed'] = _line(
            '731/1.0', (518, 834, 644, 876), 92.0)

        self.assertEqual(read_training_result_values(sample), ({}, {}))

    def test_agreeing_numeric_result_refinement_keeps_one_provenance(self):
        sample = _result_raw()
        sample['regions']['numeric_result.speed'] = _line(
            '556/1600', (322, 834, 448, 876), 96.0)

        parsed = parse(sample)

        self.assertEqual(parsed['facts']['result_values']['speed'], 556)
        self.assertEqual(parsed['facts']['stat_caps']['speed'], 1600)
        self.assertEqual(parsed['facts']['result_value_provenance']['speed']['region']['text'],
                         '556/1.0')
        self.assertNotIn('speed', parsed['facts'].get('result_value_conflicts', {}))

    def test_conflicting_numeric_result_refinement_leaves_value_unresolved(self):
        sample = _result_raw()
        sample['regions']['numeric_result.speed'] = _line(
            '557/1600', (322, 834, 448, 876), 96.0)

        parsed = parse(sample)
        facts = parsed['facts']

        self.assertNotIn('speed', facts['result_values'])
        self.assertNotIn('speed', facts.get('result_value_provenance', {}))
        self.assertNotIn('speed', facts['result_value_candidates'])
        self.assertNotIn('speed', facts['stat_caps'])
        self.assertNotIn('speed', facts.get('result_snapshot_refinement', {}))
        conflict = facts['result_value_conflicts']['speed']
        self.assertEqual(conflict['status'], 'unresolved')
        self.assertEqual(conflict['candidate_values'], [556, 557])
        self.assertEqual(conflict['canonical']['source'], 'numeric_result.speed')

    def test_conflicting_field_is_excluded_from_source_state_observations(self):
        sample = _result_raw()
        sample['regions']['numeric_result.speed'] = _line(
            '557/1600', (322, 834, 448, 876), 96.0)
        sample['lines'].extend([
            _line('Stamina', (525, 796, 618, 823)),
            _line('731/1.0', (507, 826, 652, 877), 91.0),
        ])
        sample['regions']['result.stamina'] = _line(
            '731/1.0', (518, 834, 644, 876), 92.0)
        parsed = parse(sample)
        row = dict(parsed, source_timestamp_ms=1000, evidence='conflict.png')

        observations = build_observations([row])

        self.assertEqual(len(observations), 1)
        self.assertNotIn('speed', observations[0]['payload']['values'])
        self.assertEqual(observations[0]['payload']['values']['stamina'], 731)

    def test_conflicting_field_cannot_support_a_training_transaction(self):
        sample = _result_raw()
        sample['regions']['numeric_result.speed'] = _line(
            '557/1600', (322, 834, 448, 876), 96.0)
        parsed = parse(sample)
        parsed.update(source_timestamp_ms=1000, evidence='conflict.png',
                      training_option='speed')
        before = [dict(first_seen_ms=0, last_seen_ms=0,
                       values={'speed': 500}, evidence='before.png')]

        events = training_events([parsed], before)

        self.assertTrue(events)
        self.assertNotIn('speed', events[0]['deltas'])
        self.assertNotIn('speed', events[0].get('result_state_crosschecks', {}))


    def test_goal_countdown_excludes_concert_countdown(self):
        lines = [
            _line('11 turn(s)', (276, 124, 368, 154)),
            _line('Concert in', (283, 110, 355, 131)),
        ]
        self.assertEqual(read_goal_turns(lines), (None, None))


if __name__ == '__main__':
    unittest.main()
