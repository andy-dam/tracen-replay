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
from tracen_replay.weak_state_recovery import fingerprint, load, recover


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


if __name__ == '__main__':
    unittest.main()
