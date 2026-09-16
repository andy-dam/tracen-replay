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

    def test_an_ordinary_reading_requests_the_same_sidebar_rows_as_read_training(self):
        # The signed sidebar crop is the only way back to an award whose merged
        # current+award line is low confidence.  A result frame read by the
        # ordinary pass must request those rows too, or that award is lost for
        # every result the dense inspection does not reach.
        from types import SimpleNamespace
        from unittest.mock import patch

        import numpy as np
        from PIL import Image, ImageOps

        from tests.test_training_result_layout import _result_lines
        from tracen_replay.gameplay import CURRENCIES
        from tracen_replay.vision import NeuralReader

        detector_lines = _result_lines()
        boxes = [np.asarray([[line['box'][0] - 148, line['box'][1]], [line['box'][2] - 148, line['box'][1]],
                             [line['box'][2] - 148, line['box'][3]], [line['box'][0] - 148, line['box'][3]]],
                            dtype=float) for line in detector_lines]

        class Engine:
            def __call__(self, _image):
                return SimpleNamespace(boxes=boxes, txts=[l['text'] for l in detector_lines],
                                       scores=[l['confidence'] / 100 for l in detector_lines])

            def text_rec(self, request):
                return SimpleNamespace(txts=['?'] * len(request.img), scores=[0.99] * len(request.img))

        reader = object.__new__(NeuralReader)
        reader.np, reader.Image, reader.ImageOps = np, Image, ImageOps
        reader.TextRecInput = lambda *, img: SimpleNamespace(img=img)
        reader.engine, reader.models, reader.fingerprint = Engine(), {}, 'performance-rows-test'
        pane = Image.new('RGB', (810, 1080), (30, 40, 50))

        with patch('tracen_replay.preview_recovery.recover_in_memory',
                   side_effect=lambda raw, _pane, reader: raw):
            raw = reader.read(pane)
        training_raw = reader.read_training(pane)

        self.assertTrue(raw['result_grid'])
        for field in CURRENCIES:
            name = 'performance_gain.' + field
            self.assertIn(name, raw['regions'])
            self.assertEqual(raw['regions'][name]['box'], training_raw['regions'][name]['box'])


if __name__ == '__main__':
    unittest.main()
