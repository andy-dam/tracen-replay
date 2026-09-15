"""Regression coverage for same-frame result-card state snapshots."""

import copy
import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.source_state_observations import build_observations
from tracen_replay.stat_state_details import read_training_result_values
from tracen_replay.vision import parse


REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOT = localdata.root("prepared_snapshot_final", "independent-02/initial-baseline")
SOURCE_RAW = SOURCE_ROOT / "neural/part-010-frame-000077.json"


class TrainingResultStateSnapshotTests(unittest.TestCase):
    def _source_raw(self):
        if not SOURCE_RAW.is_file():
            self.skipTest("T049 source OCR cache is unavailable")
        return json.loads(SOURCE_RAW.read_text(encoding="utf-8"))

    def test_actual_t049_keeps_stamina_from_same_result_frame(self):
        raw = self._source_raw()

        values, proof = read_training_result_values(raw)

        # The preserved OCR already contains the source-bound 382/1355 ratio;
        # the regression is parser promotion, so this test must not re-run OCR.
        self.assertEqual(values["stamina"], 382)
        self.assertEqual(proof["stamina"]["cap_status"], "readable")
        self.assertEqual(proof["stamina"]["cap"], 1355)

        parsed = parse(copy.deepcopy(raw))
        self.assertEqual(parsed["facts"]["result_values"]["stamina"], 382)

        reading = dict(
            parsed,
            source_timestamp_ms=raw["source_timestamp_ms"],
            evidence=raw["evidence"],
        )
        state = next(
            item
            for item in build_observations([reading])
            if item["payload"]["channel"] == "stats"
        )
        self.assertEqual(state["payload"]["values"]["stamina"], 382)
        self.assertFalse(state["cross_frame_values_merged"])

    def test_wrong_grid_does_not_promote_result_cards(self):
        raw = self._source_raw()
        raw["result_grid"] = False

        self.assertEqual(read_training_result_values(raw), ({}, {}))

    def test_current_menu_result_overlap_does_not_promote_result_cards(self):
        raw = self._source_raw()
        raw["current_grid"] = True

        # A selectable menu may retain stale result-card OCR during a visual
        # transition.  The two layouts are ambiguous until the result reader
        # has an applied-result-only gate, so preserve unknown here.
        self.assertEqual(read_training_result_values(raw), ({}, {}))

    def test_complete_ratio_reader_is_not_tied_to_t049_amount(self):
        raw = self._source_raw()
        raw["regions"]["result.stamina"]["text"] = "401/1500"
        for line in raw["lines"]:
            if line.get("text") == "382/1355":
                line["text"] = "401/1500"

        values, _ = read_training_result_values(raw)

        self.assertEqual(values["stamina"], 401)

    def test_conflicting_detector_cap_keeps_numerator_but_withholds_cap(self):
        raw = self._source_raw()
        for line in raw["lines"]:
            if line.get("text") == "382/1355":
                line["text"] = "382/1500"

        values, proof = read_training_result_values(raw)

        self.assertEqual(values["stamina"], 382)
        self.assertEqual(
            proof["stamina"]["cap_status"],
            "unresolved_conflicting_or_partial_detector_cap",
        )
        self.assertNotIn("cap", proof["stamina"])
        self.assertEqual(proof["stamina"]["detector_caps"], [1500])

    def test_partial_detector_cap_keeps_numerator_but_withholds_cap(self):
        raw = self._source_raw()
        for line in raw["lines"]:
            if line.get("text") == "382/1355":
                line["text"] = "382/1.0"

        values, proof = read_training_result_values(raw)

        self.assertEqual(values["stamina"], 382)
        self.assertEqual(
            proof["stamina"]["cap_status"],
            "unresolved_conflicting_or_partial_detector_cap",
        )
        self.assertNotIn("cap", proof["stamina"])
        self.assertEqual(
            proof["stamina"]["detector_cap_status"], "partial_or_malformed"
        )

    def test_strict_parser_publishes_cap_only_after_detector_agreement(self):
        raw = self._source_raw()
        raw["regions"]["result.stamina"]["confidence"] = 99.0

        parsed = parse(copy.deepcopy(raw))

        self.assertEqual(parsed["facts"]["result_values"]["stamina"], 382)
        self.assertEqual(parsed["facts"]["stat_caps"]["stamina"], 1355)

    def test_strict_parser_withholds_conflicting_detector_cap(self):
        raw = self._source_raw()
        raw["regions"]["result.stamina"]["confidence"] = 99.0
        for line in raw["lines"]:
            if line.get("text") == "382/1355":
                line["text"] = "382/1500"

        parsed = parse(copy.deepcopy(raw))

        self.assertEqual(parsed["facts"]["result_values"]["stamina"], 382)
        self.assertNotIn("stamina", parsed["facts"]["stat_caps"])

    def test_strict_parser_withholds_partial_detector_cap(self):
        raw = self._source_raw()
        raw["regions"]["result.stamina"]["confidence"] = 99.0
        for line in raw["lines"]:
            if line.get("text") == "382/1355":
                line["text"] = "382/1.0"

        parsed = parse(copy.deepcopy(raw))

        self.assertEqual(parsed["facts"]["result_values"]["stamina"], 382)
        self.assertNotIn("stamina", parsed["facts"]["stat_caps"])

    def test_malformed_geometry_and_confidence_fail_closed(self):
        raw = self._source_raw()
        raw["regions"]["result.stamina"]["confidence"] = 10**10000
        next(line for line in raw["lines"] if line.get("text") == "Stamina")[
            "box"
        ] = [10**10000, 796, 618, 823]

        values, proof = read_training_result_values(raw)
        self.assertNotIn("stamina", values)
        self.assertNotIn("stamina", proof)


if __name__ == "__main__":
    unittest.main()
