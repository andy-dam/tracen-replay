"""Source-bound regressions for the leading-digit Shogi result obstruction."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from PIL import Image

from tracen_replay.full_recording import cached_readings, parse_receipt_pixels
from tracen_replay.source_state_observations import build_observations
from tracen_replay.stat_state_details import (
    read_result_card_occlusion,
    read_training_result_values,
)
from tests import localdata
from tracen_replay.vision import parse


ROOT = Path(__file__).resolve().parents[2]
REPLAY = localdata.root("fourth_recording_retest_newer")
# An earlier preserved fourth-recording worker output was removed from this
# checkout; these source-bound checks skip (not pass) when it is absent.
_FIXTURE_AVAILABLE = (REPLAY / "capture.json").is_file() and (REPLAY / "neural").is_dir()


def _raw(frame: str) -> dict:
    return json.loads(
        (REPLAY / "neural" / f"part-015-frame-000{frame}.json")
        .read_text(encoding="utf-8")
    )


def _capture_and_frame(frame: str) -> tuple[dict, dict]:
    capture = json.loads((REPLAY / "capture.json").read_text(encoding="utf-8"))
    target = next(
        item for item in capture["frames"]
        if item["id"] == f"part-015-frame-000{frame}"
    )
    return capture, target


if __name__ == "__main__":
    unittest.main()
