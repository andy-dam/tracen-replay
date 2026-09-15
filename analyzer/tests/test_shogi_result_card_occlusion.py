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


@unittest.skipUnless(_FIXTURE_AVAILABLE, "the preserved fourth-recording replay output is not present in this checkout")
class ShogiResultCardOcclusionTests(unittest.TestCase):
    def test_frame_245_normal_receipt_parse_derives_pixel_unknown(self):
        capture, frame = _capture_and_frame("245")
        raw = _raw("245")
        parsed = parse_receipt_pixels(
            raw,
            REPLAY,
            frame,
            raw,
            source_sha256=capture["source"]["sha256"],
        )

        # No weak-state sidecar or injected metadata participates in this
        # path.  The normal source-pixel boundary must still suppress the
        # obstructed OCR candidate and retain only an explicit unknown proof.
        self.assertNotIn("result_card_occlusion", raw)
        values = parsed["facts"]["result_values"]
        self.assertNotIn("speed", values)
        self.assertEqual(
            parsed["facts"]["result_card_occlusion"]["fields"],
            ["speed", "wit"],
        )
        state_rows = build_observations([dict(
            parsed,
            source_timestamp_ms=raw["source_timestamp_ms"],
            evidence=raw["evidence"],
        )])
        stats = next(item for item in state_rows if item["payload"]["channel"] == "stats")
        self.assertNotIn("speed", stats["payload"]["values"])
        self.assertEqual(stats["payload"]["occluded_fields"], ["speed", "wit"])

    def test_frame_245_cached_replay_derives_same_pixel_unknown(self):
        capture, frame = _capture_and_frame("245")
        replay = copy.deepcopy(capture)
        replay["frames"] = [frame]
        rows = cached_readings(replay, REPLAY)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotIn("speed", row["facts"]["result_values"])
        self.assertEqual(
            row["facts"]["result_card_occlusion"]["fields"],
            ["speed", "wit"],
        )
        stats = next(
            item for item in build_observations([row])
            if item["payload"]["channel"] == "stats"
        )
        self.assertNotIn("speed", stats["payload"]["values"])

    def test_frame_245_leading_speed_digit_is_source_unknown(self):
        raw = _raw("245")
        with Image.open(REPLAY / "gameplay" / "part-015-frame-000245.png") as pane:
            metadata = read_result_card_occlusion(raw, pane)

        self.assertEqual(metadata["fields"], ["speed", "wit"])
        speed = metadata["proof"]["speed"]
        self.assertEqual(speed["occlusion_scope"], "leading_digit")
        self.assertEqual(speed["pixel"]["minimum_components"], 1)
        self.assertGreaterEqual(speed["pixel"]["components"][0]["area"], 80)

        # The unannotated legacy raw record exposes the OCR candidate, but the
        # validated source-pixel proof must prevent it becoming a state value.
        self.assertEqual(read_training_result_values(raw)[0].get("speed"), 381)
        checked = copy.deepcopy(raw)
        checked["result_card_occlusion"] = metadata
        parsed = parse(checked)
        self.assertNotIn("speed", parsed["facts"]["result_values"])
        self.assertNotIn("wit", parsed["facts"]["result_values"])

        row = dict(parsed, source_timestamp_ms=raw["source_timestamp_ms"], evidence=raw["evidence"])
        state = next(
            item for item in build_observations([row])
            if item["payload"]["channel"] == "stats"
        )
        self.assertNotIn("speed", state["payload"]["values"])
        self.assertEqual(state["payload"]["occluded_fields"], ["speed", "wit"])

    def test_frame_246_does_not_promote_speed_from_conflicting_views(self):
        raw = _raw("246")
        with Image.open(REPLAY / "gameplay" / "part-015-frame-000246.png") as pane:
            metadata = read_result_card_occlusion(raw, pane)
        self.assertEqual(metadata["fields"], ["wit"])
        values, _proof = read_training_result_values(raw)
        self.assertNotIn("speed", values)
        parsed = parse(raw)
        self.assertNotIn("speed", parsed["facts"]["result_values"])


if __name__ == "__main__":
    unittest.main()
