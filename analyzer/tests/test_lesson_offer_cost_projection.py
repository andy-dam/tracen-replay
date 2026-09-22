import json
from types import SimpleNamespace
import unittest


from tests import localdata


BASE = localdata.root("development_third_recording_baseline")
RAW_PATH = BASE / 'neural/part-005-frame-000094.json'
GAMEPLAY_PATH = BASE / 'gameplay/part-005-frame-000094.png'
SOURCE_FRAME_PATH = BASE / 'part-005/frames/000094.jpg'
SOURCE_VALUES = [
    '0', '10', '0', '0', '0',
    '0', '15', '0', '0', '0',
    '0', '0', '10', '0', '0',
]


class _TextRecInput:
    def __init__(self, *, img):
        self.img = img


class _SourceCropReader:
    """Deterministic OCR double; crop hashes still come from the real image."""

    def __init__(self, values=None, scores=None):
        self.models = {'det': 'det-digest', 'rec': 'rec-digest'}
        self.fingerprint = 'lesson-offer-cost-test-engine'
        self.TextRecInput = _TextRecInput
        self.calls = []
        self.values = list(values or SOURCE_VALUES)
        self.scores = list(scores or [0.99] * len(self.values))
        self.engine = SimpleNamespace(text_rec=self._read)

    def _read(self, request):
        self.calls.append(request)
        return SimpleNamespace(txts=self.values, scores=self.scores)


class _VariantSourceCropReader(_SourceCropReader):
    """Leave two source slots weak, then agree on both derived views."""

    def _read(self, request):
        self.calls.append(request)
        values = list(SOURCE_VALUES)
        scores = [0.99] * len(values)
        if len(self.calls) == 1:
            # Group visual/composure are the only weak baseline readings.
            scores[8] = 0.93
            scores[9] = 0.96
        return SimpleNamespace(txts=values, scores=scores)


def _offer(result, name):
    return next(offer for offer in result['offers'] if offer['name'] == name)


if __name__ == '__main__':
    unittest.main()
