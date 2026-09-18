from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from tests import localdata
from tracen_replay.lesson_offer_adapter import (
    SCHEMA,
    LessonOfferSourceError,
    adapt_lesson_offer_frame,
)


BASE = localdata.root("development_third_recording_baseline")
RAW_094 = BASE / "neural/part-005-frame-000094.json"
RAW_121 = BASE / "neural/part-005-frame-000121.json"
GAMEPLAY_094 = BASE / "gameplay/part-005-frame-000094.png"
GAMEPLAY_121 = BASE / "gameplay/part-005-frame-000121.png"
SOURCE_094 = BASE / "part-005/frames/000094.jpg"
SOURCE_121 = BASE / "part-005/frames/000121.jpg"
SOURCE_SHA256 = "a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _values(offer):
    return [price["value"] for price in offer["prices"]]


def _offer(result, name):
    return next(item for item in result["offers"] if item["name"] == name)


if __name__ == "__main__":
    unittest.main()
