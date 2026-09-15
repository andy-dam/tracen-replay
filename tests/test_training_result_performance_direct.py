import copy
import unittest

from tracen_replay.vision import parse
from tests.test_performance_panel_recovery import panel_lines


def _line(text, box, confidence=99):
    return {'text': text, 'confidence': confidence, 'box': list(box)}


def _raw(*, direct='+13', confidence=99.947, box=(245, 464, 319, 501),
         result_grid=True, current_grid=False, panel=True):
    lines = panel_lines() if panel else []
    if panel:
        visual = next(item for item in lines if item['text'] == '19')
        visual.update(text='5+13', confidence=93.365,
                     box=[224, 460, 316, 498])
    lines.append(_line('Training', (155, 0, 250, 29)))
    lines.append(_line('SUCCESS!', (368, 691, 724, 771)))
    regions = {
        'performance_gain.visual': _line(direct, box, confidence),
    }
    return dict(lines=lines, regions=regions, header='Training',
                current_grid=current_grid, result_grid=result_grid)


class TrainingResultPerformanceDirectTests(unittest.TestCase):
    def test_clean_dedicated_visual_award_requires_result_panel_proof(self):
        parsed = parse(_raw())
        self.assertEqual(parsed['screen'], 'training_result')
        facts = parsed['facts']
        self.assertEqual(facts['training_outcome'], 'success')
        self.assertEqual(facts['awarded_performance_gains']['visual'], 13)
        proof = facts['performance_gain_source_provenance']['visual']
        self.assertEqual(proof['basis'],
                         'same_frame_dedicated_result_performance_region')
        self.assertEqual(proof['panel_observation']['text'], '5+13')

    def test_dedicated_crop_without_panel_identity_stays_unawarded(self):
        parsed = parse(_raw(panel=False))
        facts = parsed['facts']
        self.assertNotIn('awarded_performance_gains', facts)
        self.assertNotIn('performance_gain_source_provenance', facts)

    def test_preview_geometry_and_failure_cannot_use_result_crop(self):
        preview = _raw(current_grid=True, result_grid=False)
        preview['lines'].append(_line('Failure', (736, 770, 798, 794)))
        preview_facts = parse(preview)['facts']
        self.assertNotIn('awarded_performance_gains', preview_facts)

        failure = _raw()
        failure['lines'][-1] = _line('FAILURE!', (368, 691, 724, 771))
        failure_facts = parse(failure)['facts']
        self.assertNotIn('awarded_performance_gains', failure_facts)

    def test_conflicting_panel_and_dedicated_amount_remains_unresolved(self):
        raw = _raw(direct='+14')
        # Keep the panel's source line at 5+13 while the dedicated crop says
        # +14.  The crop is still physically bound, but the two values cannot
        # be silently reconciled.
        parsed = parse(raw)
        facts = parsed['facts']
        self.assertNotIn('visual', facts.get('awarded_performance_gains', {}))
        conflict = facts['performance_gain_source_conflicts']['visual']
        self.assertEqual(conflict['candidate_values'], [13, 14])

    def test_low_confidence_or_out_of_panel_crop_stays_unawarded(self):
        for kwargs in (
            {'confidence': 96.99},
            {'box': (500, 700, 574, 737)},
            {'box': (100, 464, 319, 501)},
        ):
            with self.subTest(kwargs=kwargs):
                facts = parse(_raw(**kwargs))['facts']
                self.assertNotIn('visual', facts.get('awarded_performance_gains', {}))


if __name__ == '__main__':
    unittest.main()
