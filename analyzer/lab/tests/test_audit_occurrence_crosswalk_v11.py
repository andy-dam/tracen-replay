"""Focused contract tests for the parameterized G5 occurrence crosswalk."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import unittest
from pathlib import Path

from tests import localdata

MODULE_PATH = localdata.root("occurrence_crosswalk_module")
if not MODULE_PATH.exists():
    raise unittest.SkipTest("local evidence 'occurrence_crosswalk_module' is not present")
SPEC = importlib.util.spec_from_file_location("audit_occurrence_crosswalk_v11", MODULE_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def _baseline(candidate_turns: list[str]) -> dict:
    return {
        "recordings": [{"recording": "v1", "derived_turn_ids": candidate_turns}],
        "contributions": [{
            "recording": "v1",
            "id": "contribution-1",
            "event_id": "training-1",
            "turn_id": None,
            "candidate_turn_ids": candidate_turns,
            "turn_assignment_basis": "crosses_calendar_boundary",
            "channel": "stats",
            "field": "speed",
            "amount": 7,
            "basis": "state_derived",
        }],
    }


def _canonical_exact_current() -> dict:
    return {
        "changes": {},
        "current_contributions": [{
            "identity": {"recording": "v1", "contribution_id": "contribution-1"},
            "id": "contribution-1",
            "event_id": "training-1",
            "turn_id": None,
            "channel": "stats",
            "field": "speed",
            "amount": 7,
            "basis": "observed_training_gain",
        }],
    }


class CrosswalkV11Tests(unittest.TestCase):
    def test_two_turn_ownership_is_preserved_without_double_counting(self) -> None:
        baseline = _baseline(["turn-039", "turn-040"])
        rows, partition = AUDIT.make_full_baseline_crosswalk(baseline, _canonical_exact_current(), [])

        coverage = AUDIT.assert_crosswalk_coverage(baseline, rows, partition)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_turn_ids"], ["turn-039", "turn-040"])
        self.assertEqual(rows[0]["turn_assignment_basis"], "crosses_calendar_boundary")
        self.assertEqual(rows[0]["normalized_turn_ids"], ["turn-039", "turn-040"])
        self.assertEqual(rows[0]["turn_reference_kind"], "candidate_turns")
        self.assertEqual(coverage["crosswalk_row_count"], 1)
        self.assertEqual(coverage["partition_total"], 1)
        self.assertEqual(coverage["expected_turn_reference_count"], 2)
        self.assertEqual(coverage["observed_turn_reference_count"], 2)
        self.assertTrue(coverage["turn_index_is_non_counting_reference"])


    def test_duplicate_occurrence_fails_closed(self) -> None:
        baseline = _baseline(["turn-039"])
        rows, partition = AUDIT.make_full_baseline_crosswalk(baseline, _canonical_exact_current(), [])

        with self.assertRaisesRegex(ValueError, "baseline contribution coverage failed"):
            AUDIT.assert_crosswalk_coverage(baseline, rows + rows, partition)

    def test_dropped_occurrence_fails_closed(self) -> None:
        baseline = _baseline(["turn-039"])
        rows, partition = AUDIT.make_full_baseline_crosswalk(baseline, _canonical_exact_current(), [])

        with self.assertRaisesRegex(ValueError, "baseline contribution coverage failed"):
            AUDIT.assert_crosswalk_coverage(baseline, [], partition)

    def test_crosswalk_levels_are_explicit_and_never_independent_proof(self) -> None:
        scratch = MODULE_PATH.parent / "diagnostic-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        tmp_path = scratch / f"crosswalk-v11-test-{os.getpid()}"
        shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True)
        try:
            baseline_root = tmp_path / "baseline"
            current_root = tmp_path / "current"
            (baseline_root / "source").mkdir(parents=True)
            (current_root / "source").mkdir(parents=True)
            (baseline_root / "source" / "frame.png").write_bytes(b"same-source-bytes")
            (current_root / "source" / "frame.png").write_bytes(b"same-source-bytes")

            source_bound = AUDIT.classify_absent(
                _base_occurrence(7, "training-1", ["source/frame.png"]),
                [_occurrence(7, "training-1", ["source/frame.png"])],
                {},
                baseline_root,
                current_root,
                {},
                {},
            )
            self.assertEqual(source_bound["verdict"], "matched_current_canonical_fact")
            self.assertEqual(source_bound["match_level"], "source_byte_identity")
            self.assertTrue(source_bound["proof_state"].startswith("crosswalk_only"))

            bounded = AUDIT.classify_absent(
                _base_occurrence(7, "training-1"),
                [_occurrence(7, "training-1")],
                {},
                baseline_root,
                current_root,
                {},
                {},
            )
            self.assertEqual(bounded["match_level"], "bounded_temporal_field_amount")
            self.assertTrue(bounded["proof_state"].startswith("crosswalk_only"))

            same_amount_only = AUDIT.classify_absent(
                _base_occurrence(7, "training-1"),
                [_occurrence(7, "training-2")],
                {},
                baseline_root,
                current_root,
                {},
                {},
            )
            self.assertEqual(same_amount_only["match_level"], "unresolved")
            self.assertNotEqual(same_amount_only["verdict"], "matched_current_canonical_fact")

            missing_field_base = _base_occurrence(7, "training-1")
            missing_field_current = _occurrence(7, "training-1")
            missing_field_base["field"] = None
            missing_field_current["field"] = None
            missing_field = AUDIT.classify_absent(
                missing_field_base,
                [missing_field_current],
                {},
                baseline_root,
                current_root,
                {},
                {},
            )
            self.assertEqual(missing_field["match_level"], "unresolved")
            self.assertNotEqual(missing_field["verdict"], "matched_current_canonical_fact")
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_swapped_source_root_fails_and_capture_only_legacy_root_passes(self) -> None:
        scratch = MODULE_PATH.parent / "diagnostic-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        tmp_path = scratch / f"crosswalk-v11-binding-test-{os.getpid()}"
        shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True)
        try:
            source_one = "a" * 64
            source_two = "b" * 64
            report = {"source": {"sha256": source_one}}
            matching_root = tmp_path / "matching"
            swapped_root = tmp_path / "swapped"
            for root, source_sha in ((matching_root, source_one), (swapped_root, source_two)):
                (root / "shared").mkdir(parents=True)
                (root / "shared" / "frame.png").write_bytes(b"same-plausible-frame")
                (root / "capture.json").write_text(
                    json.dumps({"source": {"sha256": source_sha}}), encoding="utf-8"
                )
                (root / "replay-input-manifest.json").write_text(
                    json.dumps({"source_sha256": source_sha}), encoding="utf-8"
                )

            bound = AUDIT.bind_report_to_source_root("v1", report, matching_root)
            self.assertTrue(bound["all_present_manifests_match"])
            with self.assertRaisesRegex(ValueError, "does not match report"):
                AUDIT.bind_report_to_source_root("v1", report, swapped_root)

            legacy_root = tmp_path / "legacy-capture-only"
            legacy_root.mkdir()
            (legacy_root / "capture.json").write_text(
                json.dumps({"source": {"sha256": source_one}}), encoding="utf-8"
            )
            legacy = AUDIT.bind_report_to_source_root("independent-02", report, legacy_root)
            self.assertTrue(legacy["all_present_manifests_match"])
            self.assertIn("replay-input-manifest.json", legacy["legacy_optional_manifests_absent"])
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)


def _occurrence(amount: int, event_id: str, evidence: list[str] | None = None) -> dict:
    return {
        "recording": "v1",
        "id": "current-1",
        "event_id": event_id,
        "turn_id": "turn-001",
        "channel": "stats",
        "field": "speed",
        "amount": amount,
        "basis": "observed_training_gain",
        "observation_start_ms": 1010,
        "observation_end_ms": 1020,
        "evidence_source_timestamp_ms": [1010],
        "evidence": evidence or [],
        "independent_effect_verification": False,
    }


def _base_occurrence(amount: int, event_id: str, evidence: list[str] | None = None) -> dict:
    return {
        "recording": "v1",
        "id": "baseline-1",
        "event_id": event_id,
        "turn_id": "turn-001",
        "channel": "stats",
        "field": "speed",
        "amount": amount,
        "basis": "state_derived",
        "contribution_observation_window_ms": [1000, 1005],
        "evidence_source_timestamp_ms": [1000],
        "evidence": evidence or [],
    }


if __name__ == "__main__":
    unittest.main()
