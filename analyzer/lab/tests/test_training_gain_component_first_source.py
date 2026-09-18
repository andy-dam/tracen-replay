"""Tests of ``tests.test_training_gain_component_first_source`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from pathlib import Path
from tests import localdata
from tracen_replay.training_gain_phases import (
    resolve_full_component_phase,
    source_gain_observations,
)
from tracen_replay.transactions import training_events


class ActualComponentFirstSourcePhaseTests(unittest.TestCase):
    def _actual(self, relative_path, start_ms, end_ms, field, expected):
        path = Path(relative_path)
        if not path.is_file():
            self.skipTest(f"prepared report is unavailable: {path}")
        report = json.loads(path.read_text(encoding="utf-8"))
        rows = [
            row
            for row in report["gameplay_tracking"]["readings"]
            if row.get("screen") == "training_result"
            and start_ms <= row.get("source_timestamp_ms", -1) <= end_ms
        ]
        result = resolve_full_component_phase(
            source_gain_observations(rows, field)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["accepted_amount"], expected)
        from tracen_replay.evaluation_adapters import report_document
        events = training_events(rows, report['gameplay_tracking']['checkpoints'])
        projected = report_document(dict(
            source=report['source'],
            gameplay_tracking=dict(readings=rows, events=events,
                                   turn_action_receipts=[])))
        applied = [row for row in projected['observations']
                   if row['phase'] == 'applied'
                   and row['payload'].get('kind') == 'stat_change'
                   and row['payload'].get('field') == field]
        self.assertEqual([row['payload']['amount'] for row in applied], [expected])
        self.assertTrue(applied[0]['evidence'])

    def test_v1_t028_wit_source_component_then_full(self):
        self._actual(
            localdata.root("full_worker_candidate_first_recording_prepared_root_report", "prepared-root-report.json"),
            531000,
            532000,
            "wit",
            21,
        )

    def test_v1_t028_skill_points_single_full_dual_crop(self):
        self._actual(
            localdata.root("full_worker_candidate_first_recording_prepared_root_report", "prepared-root-report.json"),
            531000,
            532000,
            "skill_points",
            13,
        )

    def test_independent_t031_wit_single_full_dual_crop(self):
        self._actual(
            localdata.root("full_worker_candidate_batch_reports", "combined-grading-v2/independent-01-report.json"),
            534000,
            536000,
            "wit",
            25,
        )

    def test_independent_t043_speed_single_full_dual_crop(self):
        self._actual(
            localdata.root("full_worker_candidate_batch_reports", "combined-grading-v2/independent-01-report.json"),
            792000,
            794000,
            "speed",
            16,
        )
