import unittest

from tracen_replay.transactions import training_events
from tracen_replay.vision import parse


def _row(timestamp, evidence, *, candidate=None, amount=None, option='stamina',
         preview=False, turn_id=None):
    facts = {}
    if candidate is not None:
        facts['performance_gain_candidates'] = {'visual': dict(candidate)}
    if amount is not None:
        facts['awarded_performance_gains'] = {'visual': amount}
        facts['performance_panel_provenance'] = {
            'visual': {
                'field': 'visual',
                'status': 'resolved_separate_panel_values',
                'basis': 'labeled_panel_row_geometry',
                'projected': {
                    'value': amount,
                    'observation': {
                        'text': f'+{amount}', 'confidence': 99.2,
                        'box': [245, 461, 323, 497],
                    },
                },
            },
        }
    if preview:
        facts['preview_overlay_proven'] = True
    row = {
        'source_timestamp_ms': timestamp,
        'evidence': evidence,
        'screen': 'training_result',
        'training_option': option,
        'facts': facts,
        'effects': [],
        'stats': {},
    }
    if turn_id is not None:
        row['turn_id'] = turn_id
    return row


def _candidate(amount=24, confidence=92.194):
    return {
        'amount': amount,
        'region': 'performance_gain.visual',
        'text': f'+{amount}',
        'confidence': confidence,
        'box': [245, 464, 319, 501],
    }


class PerformanceGroupCorroborationTests(unittest.TestCase):
    def test_parser_retains_weak_dedicated_region_without_awarding(self):
        parsed = parse({
            'lines': [{'text': 'Training', 'confidence': 99,
                       'box': [155, 0, 250, 29]}],
            'regions': {
                'performance_gain.visual': {
                    'text': '+24', 'confidence': 92.194,
                    'box': [245, 464, 319, 501],
                },
            },
            'header': 'Training', 'current_grid': False, 'result_grid': True,
        })
        self.assertNotIn('awarded_performance_gains', parsed['facts'])
        self.assertEqual(parsed['facts']['performance_gain_candidates']['visual']['amount'], 24)

    def test_two_later_source_frames_corroborate_weak_amount_and_keep_weak_proof(self):
        rows = [
            _row(100, 'inspection-017.png', candidate=_candidate()),
            _row(200, 'gameplay-344.png', amount=24),
            _row(300, 'gameplay-345.png', amount=24),
        ]
        event = training_events(rows)[0]
        self.assertEqual(event['performance_deltas'], {'visual': 24})
        self.assertEqual(event['performance_evidence']['visual'], [
            'inspection-017.png', 'gameplay-344.png', 'gameplay-345.png',
        ])
        resolution = event['performance_reading_resolutions']['visual']
        self.assertEqual(
            resolution['basis'],
            'same_result_group_two_later_physical_frames_corroborate_weak_performance_region',
        )
        self.assertEqual(resolution['weak_observations'][0]['evidence'], 'inspection-017.png')

    def test_one_later_frame_does_not_promote_weak_proof(self):
        event = training_events([
            _row(100, 'inspection-017.png', candidate=_candidate()),
            _row(200, 'gameplay-344.png', amount=24),
        ])[0]
        self.assertEqual(event['performance_deltas'], {'visual': 24})
        self.assertEqual(set(event['performance_evidence']['visual']), {'gameplay-344.png'})
        self.assertNotIn('performance_reading_resolutions', event)

    def test_conflicting_later_amounts_remain_unresolved(self):
        event = training_events([
            _row(100, 'inspection-017.png', candidate=_candidate(24)),
            _row(200, 'gameplay-344.png', amount=23),
            _row(300, 'gameplay-345.png', amount=23),
        ])[0]
        self.assertEqual(event['performance_deltas'], {'visual': 23})
        self.assertNotIn('performance_reading_resolutions', event)
        self.assertEqual(event['performance_reading_candidates']['visual'][0]['value'], 24)

    def test_repeated_path_is_not_two_physical_frames(self):
        event = training_events([
            _row(100, 'inspection-017.png', candidate=_candidate()),
            _row(200, 'gameplay-344.png', amount=24),
            _row(300, 'gameplay-344.png', amount=24),
        ])[0]
        self.assertEqual(set(event['performance_evidence']['visual']), {'gameplay-344.png'})
        self.assertNotIn('performance_reading_resolutions', event)

    def test_preview_candidate_and_turn_mismatch_are_not_corrobated(self):
        preview_event = training_events([
            _row(100, 'preview.png', candidate=_candidate(), preview=True),
            _row(200, 'gameplay-344.png', amount=24),
            _row(300, 'gameplay-345.png', amount=24),
        ])[0]
        self.assertNotIn('performance_reading_resolutions', preview_event)
        turn_event = training_events([
            _row(100, 'inspection-017.png', candidate=_candidate(), turn_id='turn-a'),
            _row(200, 'gameplay-344.png', amount=24, turn_id='turn-b'),
            _row(300, 'gameplay-345.png', amount=24, turn_id='turn-b'),
        ])[0]
        self.assertNotIn('performance_reading_resolutions', turn_event)

    def test_candidate_alone_does_not_infer_an_award(self):
        event = training_events([_row(100, 'inspection-017.png', candidate=_candidate())])[0]
        self.assertEqual(event['performance_deltas'], {})
        self.assertIn('performance_reading_candidates', event)


if __name__ == '__main__':
    unittest.main()
