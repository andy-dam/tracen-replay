"""Tests of ``tests.test_race_grade_propagation`` that need locally preserved evidence; they run only where it is."""
import copy
import json
import unittest
from tracen_replay.race_action_receipts import assemble_race_action_receipts
from tracen_replay.transactions import races
from tracen_replay.vision import _race_result_grade_observation, parse
from tests.test_race_grade_propagation import SOURCE_NEURAL, source_reading


class VisionRaceGradeTests(unittest.TestCase):
    def test_actual_yayoi_result_binds_grade_to_same_header_title(self):
        raw = json.loads(
            (SOURCE_NEURAL / "part-005-frame-000179.json").read_text(
                encoding="utf-8"
            )
        ) if (SOURCE_NEURAL / "part-005-frame-000179.json").is_file() else None
        if raw is None:
            self.skipTest("preserved independent-02 source sidecars are unavailable")

        parsed = parse(raw)

        self.assertEqual(parsed["screen"], "race_result")
        self.assertEqual(parsed["facts"]["race_grade"], "G2")
        self.assertEqual(
            parsed["facts"]["race_grade_provenance"]["basis"],
            "result_header_grade_title_geometry",
        )
        self.assertEqual(parsed["facts"]["race_grade_provenance"]["title"]["text"], "Yayoi Sho")

    def test_competing_result_header_grades_remain_unknown(self):
        raw = json.loads(
            (SOURCE_NEURAL / "part-005-frame-000179.json").read_text(
                encoding="utf-8"
            )
        ) if (SOURCE_NEURAL / "part-005-frame-000179.json").is_file() else None
        if raw is None:
            self.skipTest("preserved independent-02 source sidecars are unavailable")

        competing = copy.deepcopy(raw)
        competing["lines"].append(
            dict(text="G2", confidence=99.9, box=[760, 430, 790, 450])
        )

        parsed = parse(competing)

        self.assertEqual(parsed["screen"], "race_result")
        self.assertIsNone(parsed["facts"]["race_grade"])

    def test_grade_without_same_header_title_geometry_remains_unknown(self):
        raw = json.loads(
            (SOURCE_NEURAL / "part-005-frame-000179.json").read_text(
                encoding="utf-8"
            )
        ) if (SOURCE_NEURAL / "part-005-frame-000179.json").is_file() else None
        if raw is None:
            self.skipTest("preserved independent-02 source sidecars are unavailable")

        distant = copy.deepcopy(raw)
        for line in distant["lines"]:
            if line["text"].strip().upper() == "G2":
                line["box"] = [770, 430, 800, 450]
                break

        parsed = parse(distant)

        self.assertEqual(parsed["screen"], "race_result")
        self.assertIsNone(parsed["facts"]["race_grade"])

    def test_malformed_grade_remains_unknown(self):
        raw = json.loads(
            (SOURCE_NEURAL / "part-005-frame-000179.json").read_text(
                encoding="utf-8"
            )
        ) if (SOURCE_NEURAL / "part-005-frame-000179.json").is_file() else None
        if raw is None:
            self.skipTest("preserved independent-02 source sidecars are unavailable")

        malformed = copy.deepcopy(raw)
        for line in malformed["lines"]:
            if line["text"].strip().upper() == "G2":
                line["text"] = "G4"
                break

        parsed = parse(malformed)

        self.assertEqual(parsed["screen"], "race_result")
        self.assertIsNone(parsed["facts"]["race_grade"])

    def test_malformed_header_geometry_or_confidence_is_ignored(self):
        raw = json.loads(
            (SOURCE_NEURAL / "part-005-frame-000179.json").read_text(
                encoding="utf-8"
            )
        ) if (SOURCE_NEURAL / "part-005-frame-000179.json").is_file() else None
        if raw is None:
            self.skipTest("preserved independent-02 source sidecars are unavailable")

        malformed_lines = (
            dict(text="G3", confidence=99, box=None),
            dict(text="G3", confidence=99, box=[1, 2, 3]),
            dict(text="G3", confidence=99, box=[-1, 432, 603, 451]),
            dict(text="G3", confidence=99, box=["500", 432, 535, 451]),
            dict(text="G3", confidence=float("nan"), box=[500, 432, 535, 451]),
            dict(text="G3", confidence="99", box=[500, 432, 535, 451]),
            dict(text="G3", confidence=99, box=[500, 432, float("inf"), 451]),
        )
        for malformed in malformed_lines:
            with self.subTest(malformed=malformed):
                grade, provenance = _race_result_grade_observation(
                    copy.deepcopy(raw["lines"]) + [malformed]
                )
                self.assertEqual(grade, "G2")
                self.assertEqual(provenance["title"]["text"], "Yayoi Sho")


class RaceGradePropagationTests(unittest.TestCase):
    def test_actual_yayoi_source_flows_through_race_receipt(self):
        first = source_reading(179)
        second = source_reading(180)
        records = races([first, second])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["race_name"], "Yayoi Sho")
        self.assertEqual(records[0]["race_grade"], "G2")
        self.assertEqual(records[0]["race_grade_provenance"][0]["source_timestamp_ms"], 644500)

        actions = assemble_race_action_receipts(records)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["race_grade"], "G2")
