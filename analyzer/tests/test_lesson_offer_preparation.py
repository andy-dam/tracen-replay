from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest

from tests import localdata
from tests.test_gameplay import workspace_temp
from tracen_replay.lesson_offer_preparation import (
    LessonOfferPreparationError,
    discover,
    group_lesson_offer_candidates,
    prepare,
    select_lesson_offer_occurrences,
)


SOURCE_ROOT = localdata.root("development_third_recording_baseline")
RAW_PATH = SOURCE_ROOT / "neural/part-005-frame-000094.json"
GAMEPLAY_PATH = SOURCE_ROOT / "gameplay/part-005-frame-000094.png"
SOURCE_FRAME_PATH = SOURCE_ROOT / "part-005/frames/000094.jpg"


class _TextRecInput:
    def __init__(self, *, img):
        self.img = img


class _SourceCropReader:
    def __init__(self):
        self.models = {"det": "det-digest", "rec": "rec-digest"}
        self.fingerprint = "lesson-offer-preparation-test-engine"
        self.TextRecInput = _TextRecInput
        self.engine = SimpleNamespace(text_rec=self._read)

    def _read(self, request):
        values = [
            "0", "10", "0", "0", "0",
            "0", "15", "0", "0", "0",
            "0", "0", "10", "0", "0",
        ]
        return SimpleNamespace(txts=values, scores=[0.99] * len(values))


if __name__ == "__main__":
    unittest.main()
