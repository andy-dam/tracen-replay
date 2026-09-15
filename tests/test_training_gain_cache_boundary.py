import copy
import hashlib
import unittest

import numpy as np
from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.crop_provenance import resolve_gain_regions
from tracen_replay.full_recording import _load_training_gain_refinement, parse_receipt_pixels
from tracen_replay.pipeline import PipelineError
from tracen_replay.training_gain_source_refinement import refine_training_gain_regions
from tracen_replay.vision import parse


class TrainingGainCacheBoundaryTests(unittest.TestCase):
    def source(self, root):
        pane = Image.fromarray(np.zeros((1080, 810, 3), dtype=np.uint8))
        image = Image.new('RGB', (1920, 1080), 'black')
        image.paste(pane, (148, 0))
        path = root / 'source.png'
        image.save(path)
        refined = refine_training_gain_regions(
            pane, recognize=lambda crops: [('+32', .97), ('+32', .96)], fields=('speed',))
        raw = {'header': 'Training', 'current_grid': False, 'result_grid': True,
               'lines': [{'text': 'Training', 'box': [155, 0, 250, 29], 'confidence': 99}],
               'gameplay_sha256': hashlib.sha256(pane.tobytes()).hexdigest(),
               'regions': copy.deepcopy(refined['regions']),
               'training_gain_source_refinement': refined}
        return path, raw

    def test_cached_refinement_roundtrip_requires_real_source_pixels(self):
        with workspace_temp() as root:
            path, raw = self.source(root)
            accepted = _load_training_gain_refinement(raw, path)
            self.assertEqual(parse(accepted)['facts']['training_gains']['speed'], 32)
            with Image.open(path) as image:
                changed = image.copy()
            changed.putpixel((310, 830), (255, 255, 255))
            changed.save(path)
            with self.assertRaises(PipelineError):
                _load_training_gain_refinement(raw, path)

    def test_unlisted_or_unbound_refinement_cannot_bypass_cache_validator(self):
        with workspace_temp() as root:
            path, raw = self.source(root)
            missing = copy.deepcopy(raw)
            missing.pop('training_gain_source_refinement')
            with self.assertRaises(PipelineError):
                _load_training_gain_refinement(missing, path)
            unlisted = copy.deepcopy(raw)
            unlisted['regions']['expanded_gain.inner.wit'] = copy.deepcopy(
                raw['regions']['expanded_gain.inner.speed'])
            with self.assertRaises(PipelineError):
                _load_training_gain_refinement(unlisted, path)

    def test_common_fresh_and_inspection_parser_enforces_refinement_binding(self):
        with workspace_temp() as root:
            path, raw = self.source(root)
            with Image.open(path) as image:
                image.crop((148, 0, 958, 1080)).save(root / 'gameplay.png')
            raw.update(evidence='gameplay.png', source_timestamp_ms=100,
                       source_frame_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            frame = {'evidence': 'source.png', 'source_timestamp_ms': 100}
            result = parse_receipt_pixels(raw, root, frame)
            self.assertEqual(result['facts']['training_gains']['speed'], 32)
            for region in raw['training_gain_source_refinement']['regions'].values():
                region['confidence'] = 101
            with self.assertRaises(PipelineError):
                parse_receipt_pixels(raw, root, frame)

    def test_stored_one_percent_is_not_reinterpreted_as_perfect_confidence(self):
        with workspace_temp() as root:
            path, raw = self.source(root)
            for name in raw['regions']:
                raw['regions'][name]['confidence'] = 1
                raw['training_gain_source_refinement']['regions'][name]['confidence'] = 1
            accepted = _load_training_gain_refinement(raw, path)
            self.assertTrue(all(region['confidence'] == 1 for region in accepted['regions'].values()))
            self.assertIsNone(resolve_gain_regions(accepted['regions'], 'speed')['canonical_amount'])

    def test_invalid_confidence_and_geometry_never_supply_canonical_amount(self):
        for confidence in (True, '99', 101, float('inf'), float('nan'), 10 ** 1000):
            with self.subTest(confidence_type=type(confidence).__name__):
                result = resolve_gain_regions({'gain.speed': {
                    'text': '+3', 'confidence': confidence, 'box': [300, 832, 414, 890]}}, 'speed')
                self.assertIsNone(result['canonical_amount'])
        for box in (None, [0, 0, 1, 1], [300, float('nan'), 414, 890]):
            result = resolve_gain_regions({'gain.speed': {
                'text': '+3', 'confidence': 99, 'box': box}}, 'speed')
            self.assertIsNone(result['canonical_amount'])


if __name__ == '__main__':
    unittest.main()
