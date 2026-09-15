import copy
import hashlib
import json
from pathlib import Path
import shutil
import unittest
import uuid

from PIL import Image

from tests import localdata
from tracen_replay.full_recording import cached_readings
from tracen_replay.race_identity_refinement import apply, build
from tracen_replay.vision import parse


def digest(value):
    return hashlib.sha256(value).hexdigest()


class RaceIdentityRefinementTests(unittest.TestCase):
    def setUp(self):
        self.test_root = localdata.local('test-runs').resolve()
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(self.cleanup)
        (self.root / 'neural').mkdir()
        self.model = {'model.onnx': 'a' * 64}
        self.raws, self.observations, frames = [], [], []
        for index, timestamp in enumerate((0, 250)):
            image = Image.new('RGB', (1920, 1080), 'white')
            image.putpixel((0, 0), (index, 0, 0))
            source = self.root / f'f{index}.png'
            image.save(source)
            pane = image.crop((148, 0, 958, 1080))
            proof = self.root / f'p{index}.png'
            pane.save(proof)
            lines = [
                dict(text='Example Final', confidence=94.6, box=[328, 427, 486, 453]),
                dict(text='Fans 12000 (+3000)', confidence=99, box=[340, 680, 640, 730]),
                dict(text='Example Turf 2000m (Medium) Left', confidence=99, box=[277, 461, 595, 492]),
                dict(text='2nd', confidence=99, box=[330, 200, 430, 260]),
            ]
            raw = dict(lines=lines, regions={}, header='', current_grid=False, result_grid=False,
                       source_timestamp_ms=timestamp, source_frame_sha256=digest(source.read_bytes()),
                       gameplay_sha256=digest(pane.tobytes()), model_sha256=self.model,
                       engine_fingerprint='b' * 64, evidence=proof.name)
            self.write(f'neural/f{index}.json', raw)
            frames.append(dict(id=f'f{index}', source_timestamp_ms=timestamp, evidence=source.name))
            views = []
            for padding in (4, 10, 20):
                box = [328 - 148 - padding, 421, 486 - 148 + padding, 459]
                views.append(dict(index=0, original=copy.deepcopy(lines[0]), crop_box=box,
                                  crop_pixel_sha256=digest(pane.crop(box).tobytes()),
                                  text='Example Final', confidence=98,
                                  model_sha256=copy.deepcopy(self.model), engine_fingerprint='d' * 64))
            self.raws.append(raw)
            self.observations.append(dict(frame_id=f'f{index}', views=views))
        self.capture = dict(source={'sha256': 'c' * 64}, frames=frames)
        self.write('capture.json', self.capture)

    def cleanup(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.test_root))
        shutil.rmtree(self.root)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def candidates(self, observations=None):
        return build(self.root, 'race_name', self.observations if observations is None else observations)

    def test_source_bound_promotion_preserves_text_and_flows_through_cache(self):
        before = copy.deepcopy(self.raws)
        extras = self.candidates()
        (self.root / 'race-identity-refinement').mkdir()
        for raw, extra in zip(self.raws, extras):
            self.assertIsNone(parse(raw)['facts']['race_name'])
            refined = apply(raw, extra, self.root)
            self.assertEqual(refined['lines'][0]['text'], raw['lines'][0]['text'])
            self.assertEqual(refined['lines'][0]['original_confidence'], 94.6)
            self.assertEqual(parse(refined)['facts']['race_name'], 'Example Final')
            self.write('race-identity-refinement/' + extra['target_frame_id'] + '.json', extra)
        found = cached_readings(self.capture, self.root)
        self.assertEqual([r['facts']['race_name'] for r in found], ['Example Final', 'Example Final'])
        self.assertIn('race_identity_refinement', found[0]['facts'])
        self.assertEqual(self.raws, before)

    def test_one_frame_or_duplicate_frame_cannot_supply_temporal_support(self):
        for observations in (self.observations[:1], [self.observations[0]] * 2):
            with self.assertRaises(ValueError):
                self.candidates(observations)

    def test_conflicting_crop_at_field_threshold_blocks_promotion(self):
        observations = copy.deepcopy(self.observations)
        observations[0]['views'][1].update(text='Another Final', confidence=95)
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            self.candidates(observations)

    def test_low_confidence_truncation_is_retained_without_replacing_text(self):
        observations = copy.deepcopy(self.observations)
        observations[0]['views'][1].update(text='Example Fi', confidence=40)
        extra = self.candidates(observations)[0]
        self.assertEqual(apply(self.raws[0], extra, self.root)['lines'][0]['text'], 'Example Final')
        self.assertEqual(extra['observations'][0]['views'][1]['text'], 'Example Fi')

    def test_stale_raw_frame_proof_crop_and_capture_are_rejected(self):
        extra = self.candidates()[0]
        changes = [
            ('neural/f0.json', b'{}'), ('f0.png', b'changed frame'),
            ('p0.png', b'changed proof'), ('capture.json', b'{}'),
        ]
        for name, content in changes:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(content)
                try:
                    with self.assertRaises((ValueError, KeyError, OSError)):
                        apply(self.raws[0], extra, self.root)
                finally:
                    path.write_bytes(original)
        extra['observations'][0]['views'][0]['crop_pixel_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Crop pixels'):
            apply(self.raws[0], extra, self.root)

    def test_shifted_crop_and_prior_field_changes_are_rejected(self):
        extra = self.candidates()[0]
        modified = copy.deepcopy(extra)
        modified['observations'][0]['views'][0]['crop_box'][0] += 1
        with self.assertRaisesRegex(ValueError, 'geometry'):
            apply(self.raws[0], modified, self.root)
        raw = copy.deepcopy(self.raws[0])
        raw['lines'][0]['confidence'] = 0
        with self.assertRaisesRegex(ValueError, 'prior refinement'):
            apply(raw, extra, self.root, original=self.raws[0])

    def test_omitted_intermediate_navigation_prevents_support(self):
        middle = copy.deepcopy(self.raws[0])
        middle['source_timestamp_ms'] = 125
        middle['lines'] = []
        self.write('neural/middle.json', middle)
        self.capture['frames'].insert(1, dict(id='middle', source_timestamp_ms=125, evidence='f0.png'))
        self.write('capture.json', self.capture)
        with self.assertRaisesRegex(ValueError, 'race-result panel'):
            self.candidates()

    def test_wrong_source_and_unbound_model_metadata_are_rejected(self):
        for field in ('source_sha256', 'engine_fingerprint'):
            extra = self.candidates()[0]
            extra[field] = 'invalid'
            with self.assertRaises(ValueError):
                apply(self.raws[0], extra, self.root)

    def test_valid_but_wrong_reread_identity_cannot_detach_from_crop_observations(self):
        for field, wrong in (('engine_fingerprint', 'e' * 64),
                             ('model_sha256', {'model.onnx': 'e' * 64})):
            extra = self.candidates()[0]
            extra[field] = wrong
            with self.assertRaisesRegex(ValueError, 'Crop reread model identity'):
                apply(self.raws[0], extra, self.root)
            observations = copy.deepcopy(self.observations)
            observations[1]['views'][2][field] = wrong
            with self.assertRaisesRegex(ValueError, 'Crop reread model identity'):
                self.candidates(observations)


if __name__ == '__main__':
    unittest.main()
