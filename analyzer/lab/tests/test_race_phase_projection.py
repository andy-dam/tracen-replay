"""Tests of ``tests.test_race_phase_projection`` that need locally preserved evidence; they run only where it is."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from tests import localdata
from tracen_replay.evaluation_adapters import report_document
from tracen_replay.observation_evaluate import evaluate
from tests.test_causal_accounting import fixture
from tests.test_race_phase_projection import REFERENCE_PATH, REPORT_PATH


class RacePhaseProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (REFERENCE_PATH.exists() and REPORT_PATH.exists()):
            raise unittest.SkipTest("preserved independent-01 reference and replay report are not present")
        cls.reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    def _single_observation_score(self, case, source_row, prediction):
        reference = {
            "source_sha256": self.reference["source_sha256"],
            "scope_ms": case["scope_ms"],
            "reference_complete": True,
            "observations": [source_row],
        }
        report = {
            "source_sha256": self.reference["source_sha256"],
            "auxiliary_log_used": False,
            "observations": [prediction],
        }
        return evaluate(reference, report)["results"][0]

    def _frozen_race(self, case_id, observation_id):
        case = next(row for row in self.reference["cases"]
                    if row["case_id"] == case_id)
        source = next(row for row in case["observations"]
                      if row["id"] == observation_id)
        action = next(row for row in report_document(self.report)["observations"]
                      if row["category"] == "action"
                      and row["payload"].get("kind") == "race"
                      and row["payload"].get("name") == source["payload"]["name"])
        return case, source, action

    def test_frozen_t033_result_uses_observed_phase_and_keeps_receipt_time(self):
        case, source, action = self._frozen_race(
            "independent-01-turn-033", "t033-race-nhk")
        self.assertEqual(source["phase"], "observed")
        self.assertEqual(action["phase"], "observed")
        self.assertEqual((action["start_ms"], action["end_ms"]), (590250, 590250))
        self.assertEqual(self._single_observation_score(case, source, action)["status"],
                         "correct")

    def test_frozen_t054_result_uses_observed_phase_without_widening(self):
        case, source, action = self._frozen_race(
            "independent-01-turn-054", "t054-race-osaka")
        self.assertEqual(source["phase"], "observed")
        self.assertEqual(action["phase"], "observed")
        self.assertEqual((action["start_ms"], action["end_ms"]), (1083250, 1083250))
        self.assertEqual(self._single_observation_score(case, source, action)["status"],
                         "correct")

    def test_explicit_committed_race_phase_is_preserved_and_does_not_match_observed(self):
        report = deepcopy(self.report)
        receipt = next(row for row in report["gameplay_tracking"]["turn_action_receipts"]
                       if row.get("race_id") == "race-007")
        receipt["phase"] = "committed"
        adapted = report_document(report)
        action = next(row for row in adapted["observations"]
                      if row["category"] == "action"
                      and row["payload"].get("kind") == "race"
                      and row["payload"].get("name") == "Osaka Hai")
        case, source, _ = self._frozen_race(
            "independent-01-turn-054", "t054-race-osaka")
        self.assertEqual(action["phase"], "committed")
        self.assertEqual(self._single_observation_score(case, source, action)["status"],
                         "missed")

    def test_race_receipt_and_result_collection_emit_one_action_and_one_effect_each(self):
        report = fixture()
        data = report["gameplay_tracking"]
        data["readings"] = [{"source_timestamp_ms": 150, "evidence": "race.png"}]
        data["turn_action_receipts"] = [{
            "kind": "race", "race_id": "race-001", "source_timestamp_ms": 150,
            "evidence": ["race.png"],
        }]
        data["races"] = [{
            "id": "race-001", "race_name": "Example Cup", "placing": 1,
            "fans_gained": 10, "first_seen_ms": 150, "last_seen_ms": 170,
            "evidence": ["race.png"],
            "visible_item_reward_snapshots": [{
                "first_seen_ms": 150, "last_seen_ms": 150,
                "evidence": ["race.png"], "list_complete": False,
                "items": [{"quantity": 1, "section": "items"}],
            }],
        }]

        observations = report_document(report)["observations"]
        self.assertEqual(sum(row["category"] == "action"
                             and row["payload"].get("kind") == "race"
                             for row in observations), 1)
        self.assertEqual(sum(row["payload"].get("kind") == "fan_change"
                             for row in observations), 1)
        self.assertEqual(sum(row["payload"].get("kind") == "race_reward"
                             for row in observations), 1)
