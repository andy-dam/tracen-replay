"""Tests of ``tests.test_training_gain_t028_source_phase`` that need locally preserved evidence; they run only where it is."""
import unittest
from copy import deepcopy
from tracen_replay.training_gain_phases import (
    resolve_source_temporal_phase,
    source_gain_observations,
)
from tracen_replay.transactions import training_events
from tests.test_training_gain_t028_source_phase import _t028_rows


class T028SourcePhaseTests(unittest.TestCase):
    def test_actual_source_promotes_13_and_retains_18_alternative(self):
        report, rows = _t028_rows()
        observations = source_gain_observations(rows, "skill_points")
        resolution = resolve_source_temporal_phase(observations, "skill_points")

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution["accepted_amount"], 13)
        self.assertEqual(resolution["observed_amounts"], [1, 13, 18])
        self.assertEqual(
            resolution["source_phase_rule"],
            "canonical_source_pair_with_isolated_late_refinement_diagnostic",
        )
        self.assertEqual(resolution["ignored_unproven_values"], [18])
        self.assertEqual(resolution["ignored_source_refinement_values"], [18])
        self.assertIn(
            "training-inspection/531000/frame-000017.png",
            [item["evidence"] for item in resolution["full_observations"]],
        )
        self.assertIn(
            "gameplay/part-004-frame-000207.png",
            [item["evidence"] for item in resolution["ignored_unproven_observations"]],
        )
        self.assertEqual(
            {
                item["source_proof_kind"]
                for item in resolution["full_observations"]
            },
            {"bounded_training_gain_recovery", "canonical_source_crop"},
        )

        event = training_events(rows, report["gameplay_tracking"]["checkpoints"])[0]
        self.assertEqual(event["deltas"].get("skill_points"), 13)
        self.assertNotIn("skill_points", event["conflicting_readings"])
        phase = event["gain_phase_candidates"]["skill_points"]
        self.assertEqual(phase["value"], 13)
        self.assertEqual(phase["observed_values"], [1, 13, 18])
        self.assertIn(
            "gameplay/part-004-frame-000207.png",
            [item["evidence"] for item in phase["source_resolution"]["ignored_unproven_observations"]],
        )

    def test_repeated_refined_alternative_stays_unresolved(self):
        _report, rows = _t028_rows()
        refined = next(
            row
            for row in rows
            if row.get("evidence") == "gameplay/part-004-frame-000207.png"
        )
        duplicate = deepcopy(refined)
        duplicate["source_timestamp_ms"] = 531583
        duplicate["evidence"] = "synthetic/t028-expanded-alternative.png"
        rows.append(duplicate)

        resolution = resolve_source_temporal_phase(
            source_gain_observations(rows, "skill_points"), "skill_points"
        )
        self.assertIsNone(resolution)

    def test_without_earlier_complete_frame_phase_is_not_inferred(self):
        _report, rows = _t028_rows()
        rows = [
            row
            for row in rows
            if row.get("source_timestamp_ms") != 531417
        ]

        resolution = resolve_source_temporal_phase(
            source_gain_observations(rows, "skill_points"), "skill_points"
        )
        self.assertIsNone(resolution)

    def test_recovery_owner_or_evidence_mismatch_invalidates_full_phase(self):
        _report, rows = _t028_rows()
        recovered = next(
            row
            for row in rows
            if row.get("source_timestamp_ms") == 531517
        )
        recovered["facts"]["training_gain_recovery"]["owner_id"] = "other-window"

        resolution = resolve_source_temporal_phase(
            source_gain_observations(rows, "skill_points"), "skill_points"
        )
        self.assertIsNone(resolution)
