import copy
import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image

from tracen_replay.source_state_observations import build_observations
from tracen_replay.stat_state_details import read_result_card_occlusion
from tracen_replay.vision import parse
from tests import localdata
from tracen_replay.weak_state_recovery import apply, fingerprint, load, recover


REPO = Path(__file__).resolve().parents[2]
T063_ROOT = localdata.root("prepared_snapshot_initial", "independent-01")
T063_RAW = T063_ROOT / 'neural/part-011-frame-000114.json'
T063_EVIDENCE = T063_ROOT / 'gameplay/part-011-frame-000114.png'
READABLE_RAW = T063_ROOT / 'neural/part-011-frame-000198.json'
READABLE_EVIDENCE = T063_ROOT / 'gameplay/part-011-frame-000198.png'
T063_SOURCE_FRAME = T063_ROOT / 'part-011/frames/000114.jpg'
T063_SIDECAR = localdata.root("weak_state_recovery_inputs", 'independent-01-t063-training-success.json')
SECOND_ANIMATION_RAW = T063_ROOT / 'neural/part-011-frame-000048.json'
SECOND_ANIMATION_EVIDENCE = T063_ROOT / 'gameplay/part-011-frame-000048.png'
READABLE_SOURCE_FRAME = T063_ROOT / 'part-011/frames/000198.jpg'


def _line(text, box, confidence=99.0):
    return {'text': text, 'confidence': confidence, 'box': list(box)}


def _synthetic_raw(image):
    pixels = image.convert('RGB').tobytes()
    return {
        'lines': [
            _line('Training', (150, 1, 227, 32)),
            _line('Speed', (348, 793, 416, 825)),
            _line('??/1600', (326, 832, 454, 877), 40.0),
        ],
        'regions': {
            'result.speed': _line('??/1600', (322, 834, 448, 876), 40.0),
        },
        'header': 'Training',
        'result_grid': True,
        'current_grid': False,
        'gameplay_sha256': hashlib.sha256(pixels).hexdigest(),
    }


class _RecoveryReader:
    models = {'fake.onnx': 'f' * 64}
    fingerprint = 'fake-engine'

    def recognize_crops(self, _pane, requests):
        def text_for(request):
            kind = request['kind']
            if kind == 'current_stat':
                return '221'
            if kind == 'panel_component':
                return '89' if request['pattern'] == 'panel_current' else '+14'
            if kind == 'panel_anchor_cap':
                return '/250'
            if kind == 'panel_anchor_points':
                return 'Points'
            if kind == 'training_result_banner':
                return 'SUCCESS!'
            return '53'

        return [[
            {'variant': 'view-a', 'text': text_for(request), 'confidence': 99.0},
            {'variant': 'view-b', 'text': text_for(request), 'confidence': 98.0},
        ] for request in requests]


class ResultCardOcclusionTests(unittest.TestCase):
    def test_actual_source_has_source_bound_speed_occlusion(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result-card source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            proof = read_result_card_occlusion(raw, pane)
        self.assertEqual(proof['fields'], ['speed'])
        speed = proof['proof']['speed']
        self.assertEqual(speed['status'], 'unresolved_occluded_result_field')
        self.assertEqual(speed['label']['text'], 'peed')
        self.assertGreaterEqual(len(speed['pixel']['components']), 2)
        self.assertEqual(proof['gameplay_sha256'], raw['gameplay_sha256'])

    def test_actual_readable_field_without_obstruction_stays_unannotated(self):
        if not READABLE_RAW.is_file() or not READABLE_EVIDENCE.is_file():
            self.skipTest('independent-01 readable result source is unavailable')
        raw = json.loads(READABLE_RAW.read_text(encoding='utf-8'))
        with Image.open(READABLE_EVIDENCE) as pane:
            self.assertEqual(read_result_card_occlusion(raw, pane), {})

    def test_second_actual_animation_uses_same_geometry_detector(self):
        paths = (SECOND_ANIMATION_RAW, SECOND_ANIMATION_EVIDENCE)
        if not all(path.is_file() for path in paths):
            self.skipTest('second result-card animation source is unavailable')
        raw = json.loads(SECOND_ANIMATION_RAW.read_text(encoding='utf-8'))
        with Image.open(SECOND_ANIMATION_EVIDENCE) as pane:
            proof = read_result_card_occlusion(raw, pane)
        self.assertEqual(proof['fields'], ['speed'])
        self.assertGreaterEqual(len(proof['proof']['speed']['pixel']['components']), 2)

    def test_non_numeric_confidence_stays_unannotated(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result-card source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        raw['regions']['result.speed']['confidence'] = float('nan')
        with Image.open(T063_EVIDENCE) as pane:
            self.assertEqual(read_result_card_occlusion(raw, pane), {})

    def test_oversized_label_box_stays_unannotated(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result-card source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        label = next(line for line in raw['lines'] if line['text'] == 'peed')
        label['box'] = [0, 780, 764, 840]
        with Image.open(T063_EVIDENCE) as pane:
            self.assertEqual(read_result_card_occlusion(raw, pane), {})

    def test_malformed_pixel_array_stays_unannotated(self):
        if not T063_RAW.is_file():
            self.skipTest('independent-01 result source is unavailable')
        import numpy as np
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        malformed = np.full((1080, 810, 3), 'not-pixels', dtype=object)
        self.assertEqual(read_result_card_occlusion(raw, malformed), {})

    def test_missing_ocr_without_pixel_obstruction_stays_unknown(self):
        image = Image.new('RGB', (810, 1080), (150, 150, 150))
        raw = _synthetic_raw(image)
        self.assertEqual(read_result_card_occlusion(raw, image), {})

    def test_complete_ratio_is_not_marked_even_with_unrelated_pixels(self):
        image = Image.new('RGB', (810, 1080), (150, 150, 150))
        raw = _synthetic_raw(image)
        raw['regions']['result.speed'] = _line(
            '556/1600', (322, 834, 448, 876), 99.0)
        raw['lines'][-1] = _line('556/1600', (326, 832, 454, 877), 99.0)
        self.assertEqual(read_result_card_occlusion(raw, image), {})

    def test_metadata_propagates_without_filling_unknown_value(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result-card source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            raw['result_card_occlusion'] = read_result_card_occlusion(raw, pane)
        parsed = parse(copy.deepcopy(raw))
        row = dict(parsed, source_timestamp_ms=raw['source_timestamp_ms'],
                   evidence=raw['evidence'])
        states = build_observations([row])
        stats_states = [item for item in states
                        if item['payload']['channel'] == 'stats']
        self.assertEqual(len(stats_states), 1)
        payload = stats_states[0]['payload']
        self.assertEqual(payload['occluded_fields'], ['speed'])
        self.assertNotIn('speed', payload['values'])
        self.assertEqual(payload['values']['stamina'], 623)
        self.assertEqual(payload['occlusion_provenance']['fields'], ['speed'])

    def _stats_payload_for_metadata(self, metadata, gameplay_sha256=None):
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        raw['result_card_occlusion'] = metadata
        parsed = parse(copy.deepcopy(raw))
        row = dict(parsed, source_timestamp_ms=raw['source_timestamp_ms'],
                   evidence=raw['evidence'])
        if gameplay_sha256 is not None:
            row['gameplay_sha256'] = gameplay_sha256
        states = build_observations([row])
        stats_states = [item for item in states
                        if item['payload']['channel'] == 'stats']
        self.assertEqual(len(stats_states), 1)
        return stats_states[0]['payload']

    def test_malformed_field_entries_are_rejected_without_raising(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            metadata = read_result_card_occlusion(raw, pane)
        metadata['fields'] = ['speed', {'malformed': True}]
        payload = self._stats_payload_for_metadata(metadata)
        self.assertNotIn('occluded_fields', payload)

    def test_moved_pixel_proof_is_rejected(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            metadata = read_result_card_occlusion(raw, pane)
        metadata['proof']['speed']['pixel']['box'] = [323, 834, 449, 876]
        payload = self._stats_payload_for_metadata(metadata)
        self.assertNotIn('occluded_fields', payload)

    def test_shifted_component_proof_is_rejected(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            metadata = read_result_card_occlusion(raw, pane)
        component = metadata['proof']['speed']['pixel']['components'][0]
        component['box'][0] += 1
        payload = self._stats_payload_for_metadata(metadata)
        self.assertNotIn('occluded_fields', payload)

    def test_stale_reading_identity_is_rejected(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            metadata = read_result_card_occlusion(raw, pane)
        metadata['source_timestamp_ms'] += 1
        payload = self._stats_payload_for_metadata(metadata)
        self.assertNotIn('occluded_fields', payload)

    def test_readable_replay_clears_stale_occlusion_metadata(self):
        paths = (T063_RAW, T063_EVIDENCE, READABLE_RAW, READABLE_EVIDENCE,
                 READABLE_SOURCE_FRAME)
        if not all(path.is_file() for path in paths):
            self.skipTest('result-card replay sources are unavailable')
        stale = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            stale_metadata = read_result_card_occlusion(stale, pane)
        raw = json.loads(READABLE_RAW.read_text(encoding='utf-8'))
        raw['result_card_occlusion'] = stale_metadata
        sidecar = recover(
            raw, READABLE_EVIDENCE, reader=_RecoveryReader(),
            source_frame_path=READABLE_SOURCE_FRAME,
            source_frame_evidence='independent-01/part-011/frames/000198.jpg',
            source_frame_id='part-011-frame-000198',
        )
        replayed = apply(
            raw, sidecar, evidence_path=READABLE_EVIDENCE,
            source_frame_path=READABLE_SOURCE_FRAME,
            source_frame_evidence='independent-01/part-011/frames/000198.jpg',
            source_frame_id='part-011-frame-000198',
        )
        self.assertNotIn('result_card_occlusion', replayed)
        self.assertNotIn('result_card_occlusion', replayed['weak_state_recovery'])

    def test_row_hash_mismatch_is_rejected(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            metadata = read_result_card_occlusion(raw, pane)
        payload = self._stats_payload_for_metadata(metadata, '0' * 64)
        self.assertNotIn('occluded_fields', payload)

    def test_malformed_nested_proof_is_rejected_without_raising(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            metadata = read_result_card_occlusion(raw, pane)
        metadata['proof']['speed']['pixel']['minimum_local_contrast'] = 'unknown'
        payload = self._stats_payload_for_metadata(metadata)
        self.assertNotIn('occluded_fields', payload)

    def test_non_numeric_proof_confidence_is_rejected_without_raising(self):
        if not T063_RAW.is_file() or not T063_EVIDENCE.is_file():
            self.skipTest('independent-01 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            original = read_result_card_occlusion(raw, pane)
        for value in (True, float('nan'), float('inf'), '90'):
            metadata = copy.deepcopy(original)
            metadata['proof']['speed']['label']['confidence'] = value
            payload = self._stats_payload_for_metadata(metadata)
            self.assertNotIn('occluded_fields', payload)

    def test_prepared_cached_loader_preserves_unknown_and_occlusion(self):
        from tracen_replay.full_recording import cached_readings
        root = localdata.root("prepared_snapshot_initial", "independent-01")
        if not (root / 'capture.json').is_file():
            self.skipTest('Prepared recording cache unavailable')
        capture = json.loads((root / 'capture.json').read_text(encoding='utf-8'))
        capture['frames'] = [frame for frame in capture['frames']
                             if frame['id'] == 'part-011-frame-000114']
        self.assertEqual(len(capture['frames']), 1)
        states = build_observations(cached_readings(capture, root))
        payload = next(row['payload'] for row in states
                       if row['payload']['channel'] == 'stats')
        self.assertEqual(payload['occluded_fields'], ['speed'])
        self.assertNotIn('speed', payload['values'])
        self.assertEqual(payload['values']['skill_points'], 1969)

    def test_cached_replay_derives_same_pixel_proof_without_ocr(self):
        paths = (T063_RAW, T063_EVIDENCE, T063_SOURCE_FRAME, T063_SIDECAR)
        if not all(path.is_file() for path in paths):
            self.skipTest('cached t063 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        replayed = load(
            raw, T063_SIDECAR, evidence_path=T063_EVIDENCE,
            source_frame_path=T063_SOURCE_FRAME,
            source_frame_id='part-011-frame-000114',
            source_frame_evidence='independent-01/part-011/frames/000114.jpg',
        )
        metadata = replayed['result_card_occlusion']
        self.assertEqual(metadata['fields'], ['speed'])
        self.assertEqual(
            replayed['weak_state_recovery']['result_card_occlusion']['fields'],
            ['speed'])
        self.assertNotIn('speed', replayed.get('result_values', {}))

    def test_cached_replay_recomputes_shifted_components_from_source(self):
        paths = (T063_RAW, T063_EVIDENCE, T063_SOURCE_FRAME, T063_SIDECAR)
        if not all(path.is_file() for path in paths):
            self.skipTest('cached t063 result source is unavailable')
        raw = json.loads(T063_RAW.read_text(encoding='utf-8'))
        with Image.open(T063_EVIDENCE) as pane:
            expected = read_result_card_occlusion(raw, pane)
        stale_raw = copy.deepcopy(raw)
        stale_raw['result_card_occlusion'] = copy.deepcopy(expected)
        stale_raw['result_card_occlusion']['proof']['speed']['pixel']['components'][0]['box'][0] += 1
        sidecar = json.loads(T063_SIDECAR.read_text(encoding='utf-8'))
        sidecar['raw_sha256'] = fingerprint(stale_raw)
        replayed = apply(
            stale_raw, sidecar, evidence_path=T063_EVIDENCE,
            source_frame_path=T063_SOURCE_FRAME,
            source_frame_id='part-011-frame-000114',
            source_frame_evidence='independent-01/part-011/frames/000114.jpg',
        )
        self.assertEqual(
            replayed['result_card_occlusion']['proof']['speed']['pixel']['components'],
            expected['proof']['speed']['pixel']['components'])


if __name__ == '__main__':
    unittest.main()
