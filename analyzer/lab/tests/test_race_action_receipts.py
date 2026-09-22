"""Tests of ``tests.test_race_action_receipts`` that need locally preserved evidence; they run only where it is."""
import unittest
import json
from tests import localdata
from tracen_replay.race_action_receipts import assemble_race_action_receipts
from tests.test_race_action_receipts import race_record


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



    def test_actual_candidate_v1_report_rows_rejoin_without_worker_replay(self):
        report_path = localdata.root("full_worker_candidate_first_recording_report", "report.json")
        if not report_path.exists():
            self.skipTest("preserved candidate report is unavailable")
        with report_path.open(encoding="utf-8") as stream:
            report = json.load(stream)
        records = report["gameplay_tracking"]["races"]
        found = {
            row["race_id"]: row
            for row in assemble_race_action_receipts(records)
            if row.get("race_id") in {"race-003", "race-004", "race-006"}
        }

        self.assertEqual(
            [(found[race_id]["race_name"], found[race_id]["placing"])
             for race_id in ("race-003", "race-004", "race-006")],
            [
                ("NHK Mile Cup", 1),
                ("Mile Championship", 1),
                ("Tenno Sho (Spring)", 1),
            ],
        )
