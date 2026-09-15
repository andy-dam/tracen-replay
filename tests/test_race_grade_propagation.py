import copy
import json
import unittest
from pathlib import Path

from tests.test_causal_accounting import fixture
from tracen_replay.evaluation_adapters import report_document
from tracen_replay.race_action_receipts import (
    assemble_race_action_receipts,
    bind_race_action_metadata,
)
from tracen_replay.transactions import races
from tracen_replay.vision import _race_result_grade_observation, parse


ROOT = Path(__file__).resolve().parents[1]
SOURCE_NEURAL = (
    ROOT / ".local/full-recording/independent-02/initial-baseline/neural"
)


def source_reading(frame):
    path = SOURCE_NEURAL / f"part-005-frame-{frame:06d}.json"
    if not path.is_file():
        raise unittest.SkipTest("preserved independent-02 source sidecars are unavailable")
    raw = json.loads(path.read_text(encoding="utf-8"))
    reading = parse(raw)
    reading["source_timestamp_ms"] = raw["source_timestamp_ms"]
    reading["evidence"] = raw["evidence"]
    return reading


def result_row(time, grade="G2", evidence=None, **overrides):
    facts = dict(
        race_name="Example Cup",
        race_grade=grade,
        placing=1,
        fans=12000,
        fans_gained=3000,
        course=dict(
            venue="Nakayama",
            surface="turf",
            distance_m=2000,
            distance_category="medium",
            direction="right",
            variant="inner",
        ),
        course_condition="firm",
    )
    facts.update(overrides.pop("facts", {}))
    return dict(
        source_timestamp_ms=time,
        evidence=evidence or f"{time}.png",
        screen="race_result",
        facts=facts,
        **overrides,
    )


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
    def test_actual_yayoi_source_flows_through_race_receipt_and_report_adapter(self):
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

        report = fixture()
        data = report["gameplay_tracking"]
        data["readings"] = [first, second]
        data["races"] = records
        data["turn_action_receipts"] = actions
        data["events"] = []
        data["checkpoints"] = []
        data["performance_accounting"] = {"checkpoints": []}

        adapted = report_document(report)
        action = next(
            row for row in adapted["observations"]
            if row["id"] == "/gameplay_tracking/turn_action_receipts/0"
        )
        self.assertEqual(action["payload"]["kind"], "race")
        self.assertEqual(action["payload"]["name"], "Yayoi Sho")
        self.assertEqual(action["payload"]["grade"], "G2")

    def test_conflicting_or_malformed_record_grades_stay_unknown(self):
        conflicting = [result_row(0), result_row(250, grade="G3")]
        found = races(conflicting)[0]
        self.assertIsNone(found["race_grade"])
        self.assertEqual(found["conflicting_readings"]["race_grade"], ["G2", "G3"])

        malformed = [result_row(0), result_row(250, grade="G4")]
        found = races(malformed)[0]
        self.assertIsNone(found["race_grade"])
        self.assertIn("race_grade", found["conflicting_readings"])

    def test_malformed_optional_grade_does_not_split_observed_dialog_continuation(self):
        rows = [
            result_row(0, evidence="0.png"),
            result_row(250, evidence="250.png"),
            dict(source_timestamp_ms=500, evidence="500.png", screen="unknown", facts={}),
            dict(source_timestamp_ms=750, evidence="750.png", screen="playback_confirmation", facts={}),
            dict(source_timestamp_ms=1000, evidence="1000.png", screen="playback_confirmation", facts={}),
            dict(source_timestamp_ms=1250, evidence="1250.png", screen="playback_confirmation", facts={}),
            result_row(1500, grade="G4", evidence="1500.png"),
            result_row(1750, grade="G4", evidence="1750.png"),
        ]

        found = races(rows)

        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0]["race_grade"])
        self.assertIn("race_grade", found[0]["conflicting_readings"])

    def test_receipt_grade_requires_the_same_source_bound_record(self):
        record = result_row(
            0,
            evidence=["result.png"],
            id="race-001",
            first_seen_ms=0,
            last_seen_ms=250,
        )
        record["race_grade"] = "G2"
        candidate = dict(
            kind="race",
            race_id="race-001",
            source_timestamp_ms=0,
            evidence=["result.png"],
        )
        found = bind_race_action_metadata(candidate, [record])
        self.assertEqual(found["race_grade"], "G2")

        malformed = copy.deepcopy(record)
        malformed["race_grade"] = "G4"
        found = bind_race_action_metadata(candidate, [malformed])
        self.assertNotIn("race_grade", found)


if __name__ == "__main__":
    unittest.main()
