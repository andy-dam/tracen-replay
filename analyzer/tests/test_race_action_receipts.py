import unittest
import json
from pathlib import Path

from tests import localdata
from tracen_replay.race_action_receipts import (
    assemble_race_action_receipts,
    bind_race_action_metadata,
    race_action_binding,
)


def race_record(race_id, first, last, race_name, placing=1, evidence=(), **extra):
    return dict(
        id=race_id,
        first_seen_ms=first,
        last_seen_ms=last,
        race_name=race_name,
        placing=placing,
        evidence=list(evidence),
        **extra,
    )


def action(race_id, timestamp, evidence, **extra):
    return dict(
        kind="race",
        race_id=race_id,
        source_timestamp_ms=timestamp,
        evidence=list(evidence),
        **extra,
    )


class RaceActionReceiptTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            race_record(
                "race-003", 603000, 604000, "NHK Mile Cup", evidence=(
                    "gameplay/part-005-frame-000013.png",
                    "gameplay/part-005-frame-000014.png",
                ),
                completion_first_seen_ms=594250,
                completion_last_seen_ms=594500,
                completion_evidence=["gameplay/part-004-frame-000458.png"],
            ),
            race_record(
                "race-004", 902000, 902750, "Mile Championship", evidence=(
                    "gameplay/part-007-frame-000249.png",
                    "gameplay/part-007-frame-000250.png",
                ),
            ),
            race_record(
                "race-006", 1109500, 1110750, "Tenno Sho (Spring)", evidence=(
                    "gameplay/part-009-frame-000119.png",
                    "gameplay/part-009-frame-000120.png",
                ),
            ),
        ]

    def test_v1_race_rows_copy_name_and_placing_from_same_source_record(self):
        rows = [
            action(
                "race-003", 603000,
                [
                    "gameplay/part-004-frame-000458.png",
                    "gameplay/part-005-frame-000013.png",
                ],
            ),
            action("race-004", 902000, ["gameplay/part-007-frame-000249.png"]),
            action("race-006", 1109500, ["gameplay/part-009-frame-000119.png"]),
        ]

        for found, expected in zip(
            (bind_race_action_metadata(row, self.records) for row in rows),
            ("NHK Mile Cup", "Mile Championship", "Tenno Sho (Spring)"),
        ):
            with self.subTest(name=expected):
                self.assertEqual(found["race_name"], expected)
                self.assertEqual(found["placing"], 1)

    def test_assembly_reproduces_v1_rows_without_worker_replay(self):
        found = assemble_race_action_receipts(self.records)

        self.assertEqual(
            [(row["race_id"], row["source_timestamp_ms"], row["race_name"], row["placing"])
             for row in found],
            [
                ("race-003", 594250, "NHK Mile Cup", 1),
                ("race-004", 902000, "Mile Championship", 1),
                ("race-006", 1109500, "Tenno Sho (Spring)", 1),
            ],
        )


    def test_cross_race_id_time_and_source_mismatches_remain_unknown(self):
        cases = {
            "wrong_id": action(
                "race-004", 603000, ["gameplay/part-005-frame-000013.png"]
            ),
            "wrong_time": action(
                "race-003", 902000, ["gameplay/part-005-frame-000013.png"]
            ),
            "wrong_source": action(
                "race-003", 603000, ["gameplay/part-007-frame-000249.png"]
            ),
        }

        for label, candidate in cases.items():
            with self.subTest(label=label):
                binding = race_action_binding(candidate, self.records)
                self.assertEqual(binding["status"], "unknown")
                found = bind_race_action_metadata(candidate, self.records)
                self.assertNotIn("race_name", found)
                self.assertNotIn("placing", found)

    def test_preview_cannot_promote_result_metadata(self):
        candidate = action(
            "race-003", 603000, ["gameplay/part-005-frame-000013.png"], phase="preview"
        )

        binding = race_action_binding(candidate, self.records)
        self.assertEqual(binding["status"], "unknown")
        self.assertEqual(binding["reason"], "preview_is_not_completed_action")
        self.assertNotIn("race_name", bind_race_action_metadata(candidate, self.records))

    def test_present_invalid_phase_does_not_bind_but_absent_phase_is_legacy_compatible(self):
        record = race_record(
            "race-003", 603000, 604000, "NHK Mile Cup",
            evidence=("gameplay/part-005-frame-000013.png",),
            race_grade="G2",
        )
        candidate = action(
            "race-003", 603000, ["gameplay/part-005-frame-000013.png"]
        )
        self.assertEqual(race_action_binding(candidate, [record])["status"], "bound")
        for invalid_phase in ("PREVIEW", "not-a-phase", None, []):
            with self.subTest(phase=invalid_phase):
                invalid = dict(candidate, phase=invalid_phase)
                binding = race_action_binding(invalid, [record])
                self.assertEqual(binding["status"], "unknown")
                self.assertEqual(binding["reason"], "invalid_phase")
                self.assertNotIn("race_grade", bind_race_action_metadata(invalid, [record]))

    def test_expected_label_without_explicit_source_join_is_ignored(self):
        candidate = action(
            "unknown-race", 603000, ["gameplay/part-005-frame-000013.png"],
            expected_name="NHK Mile Cup", expected_placing=1,
        )

        binding = race_action_binding(candidate, self.records)
        self.assertEqual(binding["status"], "unknown")
        self.assertEqual(binding["reason"], "no_matching_race_id")

    def test_malformed_explicit_interval_does_not_fallback_to_timestamp(self):
        candidate = action(
            "race-003", 603000, ["gameplay/part-005-frame-000013.png"],
            source_interval_ms=[603000, "604000"],
        )

        binding = race_action_binding(candidate, self.records)
        self.assertEqual(binding["status"], "unknown")
        self.assertEqual(binding["reason"], "invalid_source_interval_ms")

    def test_timestamp_outside_explicit_interval_is_contradictory(self):
        candidate = action(
            "race-003", 602000, ["gameplay/part-005-frame-000013.png"],
            source_interval_ms=[603000, 604000],
        )

        binding = race_action_binding(candidate, self.records)
        self.assertEqual(binding["status"], "unknown")
        self.assertEqual(binding["reason"], "timestamp_outside_action_interval")

    def test_duplicate_same_id_source_matches_are_ambiguous(self):
        records = self.records + [
            race_record(
                "race-003", 603000, 604000, "Different Cup", evidence=(
                    "gameplay/part-005-frame-000013.png",
                ),
            )
        ]
        candidate = action("race-003", 603000, ["gameplay/part-005-frame-000013.png"])

        binding = race_action_binding(candidate, records)
        self.assertEqual(binding["status"], "unknown")
        self.assertEqual(binding["reason"], "ambiguous_source_match")
        self.assertNotIn("race_name", bind_race_action_metadata(candidate, records))

    def test_missing_or_contradictory_metadata_stays_field_unknown(self):
        contradictory = race_record(
            "race-003", 603000, 604000, "NHK Mile Cup", evidence=(
                "gameplay/part-005-frame-000013.png",
            ),
            name="Different Cup",
            placing=1,
            conflicting_readings={"race_name": ["NHK Mile Cup", "Different Cup"]},
        )
        candidate = action("race-003", 603000, ["gameplay/part-005-frame-000013.png"])

        found = bind_race_action_metadata(candidate, [contradictory])
        self.assertNotIn("race_name", found)
        self.assertEqual(found["placing"], 1)

        missing = race_record(
            "race-003", 603000, 604000, None, placing=None,
            evidence=("gameplay/part-005-frame-000013.png",),
        )
        found = bind_race_action_metadata(candidate, [missing])
        self.assertNotIn("race_name", found)
        self.assertNotIn("placing", found)

    def test_malformed_or_unscoped_conflict_container_keeps_grade_unknown(self):
        candidate = action(
            "race-003", 603000, ["gameplay/part-005-frame-000013.png"]
        )
        for conflicts in (
            ["race_grade", "G2"],
            "race_grade",
            {"parser_disagreement": ["G2", "G3"]},
        ):
            with self.subTest(conflicts=conflicts):
                record = race_record(
                    "race-003", 603000, 604000, "NHK Mile Cup",
                    evidence=("gameplay/part-005-frame-000013.png",),
                    race_grade="G2", conflicting_readings=conflicts,
                )
                binding = race_action_binding(candidate, [record])
                self.assertEqual(binding["status"], "bound")
                self.assertNotIn("race_grade", binding["metadata"])
                found = bind_race_action_metadata(candidate, [record])
                self.assertNotIn("race_grade", found)
                carried = dict(candidate, race_grade="G2")
                self.assertNotIn(
                    "race_grade", bind_race_action_metadata(carried, [record])
                )

    def test_existing_contradictory_action_fields_are_not_overwritten(self):
        candidate = action(
            "race-003", 603000, ["gameplay/part-005-frame-000013.png"],
            race_name="Different Cup", placing="1",
        )

        found = bind_race_action_metadata(candidate, self.records)
        self.assertNotIn("race_name", found)
        self.assertNotIn("placing", found)


if __name__ == "__main__":
    unittest.main()
