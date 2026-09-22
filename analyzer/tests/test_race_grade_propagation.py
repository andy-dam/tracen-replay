import copy
import unittest

from tracen_replay.race_action_receipts import bind_race_action_metadata
from tracen_replay.transactions import races


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


class RaceGradePropagationTests(unittest.TestCase):

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
