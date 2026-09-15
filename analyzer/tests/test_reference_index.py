import hashlib
import json
import shutil
import unittest
import uuid

from pathlib import Path

from tests import localdata
from tracen_replay.reference_index import (
    ReferenceIndexError,
    build_reference_index,
    score_reference_index,
)


class ReferenceIndexTests(unittest.TestCase):
    def setUp(self):
        self.root = localdata.scratch(uuid.uuid4().hex)

    def tearDown(self):
        shutil.rmtree(self.root)

    def write_reference(
        self,
        name,
        start=0,
        end=1000,
        cadence=250,
        source="source",
        *,
        complete=None,
        timing=None,
        groups=None,
        samples=True,
    ):
        data = {
            "source_sha256": source,
            "start_ms": start,
            "end_ms": end,
            "sample_interval_ms": cadence,
            "scope": "test effect scope",
            "groups": [] if groups is None else groups,
        }
        if samples:
            data["reviewed_samples"] = [
                {"source_timestamp_ms": timestamp}
                for timestamp in range(start, end, cadence)
            ]
        if complete is not None:
            data["reference_complete"] = complete
        if timing is not None:
            data["timing_basis"] = timing
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return data, hashlib.sha256(path.read_bytes()).hexdigest()

    def registry(self, source, duration, *items):
        return {
            "source_sha256": source,
            "source_duration_ms": duration,
            "references": [
                {
                    "path": name,
                    "sha256": digest,
                    "start_ms": start,
                    "end_ms": end,
                    "sample_interval_ms": cadence,
                    "samples": len(range(start, end, cadence)),
                }
                for name, digest, start, end, cadence in items
            ],
        }

    def test_duplicate_overlap_uses_one_candidate_and_different_cadence_wins(self):
        _, coarse_hash = self.write_reference("coarse.json", 0, 5000, 1000)
        _, dense_hash = self.write_reference("dense.json", 1000, 2000, 250)
        registry = self.registry(
            "source",
            5000,
            ("coarse.json", coarse_hash, 0, 5000, 1000),
            ("dense.json", dense_hash, 1000, 2000, 250),
            ("dense.json", dense_hash, 1000, 2000, 250),
        )

        first = build_reference_index(registry, self.root)
        second = build_reference_index(registry, self.root)
        self.assertEqual(first["partitions"], second["partitions"])
        self.assertEqual(first["registry"]["reference_count"], 3)
        self.assertEqual(first["registry"]["unique_reference_count"], 2)
        self.assertEqual(first["registry"]["duplicate_paths"], ["dense.json"])

        middle = next(
            partition
            for partition in first["partitions"]
            if partition["partition_ms"] == [1000, 2000]
        )
        self.assertEqual(middle["selected_path"], "dense.json")
        self.assertTrue(middle["overlap"])
        self.assertFalse(middle["tie"])
        self.assertEqual(middle["candidate_paths"], ["coarse.json", "dense.json"])
        self.assertIn("duplicate_registry_paths_preserved_once", first["blockers"])
        self.assertFalse(first["claims"]["full_recording_effect_recall_measured"])

    def test_same_cadence_overlap_is_marked_as_tie(self):
        _, first_hash = self.write_reference("first.json", 0, 2000, 250)
        _, second_hash = self.write_reference("second.json", 1000, 3000, 250)
        result = build_reference_index(
            self.registry(
                "source",
                3000,
                ("first.json", first_hash, 0, 2000, 250),
                ("second.json", second_hash, 1000, 3000, 250),
            ),
            self.root,
        )
        overlap = next(
            partition
            for partition in result["partitions"]
            if partition["partition_ms"] == [1000, 2000]
        )
        self.assertTrue(overlap["tie"])
        self.assertIn("same_cadence_tie_requires_adjudication", overlap["blockers"])
        self.assertEqual(overlap["selected_path"], "first.json")

    def test_version_mapping_preserves_parent_and_validates_variant_hash(self):
        _, base_hash = self.write_reference("base.json", 0, 1000, 250)
        _, variant_hash = self.write_reference("variant.json", 0, 1000, 250, complete=True)
        registry = self.registry(
            "source", 1000, ("base.json", base_hash, 0, 1000, 250)
        )
        result = build_reference_index(
            registry,
            self.root,
            version_map={
                "base.json": {
                    "path": "variant.json",
                    "sha256": variant_hash,
                    "kind": "test-adjudication",
                }
            },
        )
        partition = result["partitions"][0]
        self.assertEqual(partition["selected_path"], "variant.json")
        self.assertEqual(partition["selected_canonical_path"], "base.json")
        self.assertEqual(partition["selected"]["variant_of"], "base.json")
        self.assertEqual(result["version_mappings"][0]["canonical_path"], "base.json")
        self.assertEqual(result["version_mappings"][0]["sha256"], variant_hash)
        self.assertEqual(result["references"][0]["path"], "base.json")
        self.assertEqual(
            result["references"][0]["selected_correction"]["path"],
            "variant.json",
        )

        with self.assertRaisesRegex(ReferenceIndexError, "mapping hash mismatch"):
            build_reference_index(
                registry,
                self.root,
                version_map={
                    "base.json": {
                        "path": "variant.json",
                        "sha256": "0" * 64,
                    }
                },
            )

    def test_missing_reference_and_source_mismatch_fail_closed(self):
        missing = self.registry(
            "source", 1000, ("missing.json", "0" * 64, 0, 1000, 250)
        )
        with self.assertRaisesRegex(ReferenceIndexError, "Unable to read file"):
            build_reference_index(missing, self.root)

        _, digest = self.write_reference("wrong-source.json", source="other")
        mismatched = self.registry(
            "source", 1000, ("wrong-source.json", digest, 0, 1000, 250)
        )
        with self.assertRaisesRegex(ReferenceIndexError, "Source hash mismatch"):
            build_reference_index(mismatched, self.root)

    def test_partial_reference_exposes_completeness_and_boundary_blockers(self):
        groups = [
            {"start_ms": 0, "end_ms": 1000, "effects": [{"kind": "energy_change"}]}
        ]
        _, digest = self.write_reference(
            "partial.json",
            0,
            2000,
            250,
            complete=False,
            timing="first_exact_effect_observation",
            groups=groups,
        )
        result = build_reference_index(
            self.registry("source", 2000, ("partial.json", digest, 0, 2000, 250)),
            self.root,
        )
        partition = result["partitions"][0]
        self.assertEqual(partition["selected"]["completeness"], "incomplete")
        self.assertEqual(
            partition["selected"]["timing_basis"],
            "first_exact_effect_observation",
        )
        self.assertIn("incomplete_reference", partition["blockers"])
        self.assertFalse(result["claims"]["full_recording_effect_recall_measured"])

    def test_score_reports_missing_extra_issues_without_aggregate_claim(self):
        expected = {"kind": "energy_change", "amount": 5}
        _, digest = self.write_reference(
            "effects.json",
            groups=[{"start_ms": 250, "end_ms": 500, "effects": [expected]}],
        )
        index = build_reference_index(
            self.registry("source", 1000, ("effects.json", digest, 0, 1000, 250)),
            self.root,
        )
        report = {"source": {"sha256": "source"}}
        seen = {}

        def fake_evaluator(reference, actual_report, evidence_root):
            seen["root"] = evidence_root
            self.assertEqual(actual_report, report)
            return {
                "scope": reference["scope"],
                "source_sha256": "source",
                "start_ms": 0,
                "end_ms": 1000,
                "expected": 1,
                "predicted": 1,
                "matched": 0,
                "precision": 0.0,
                "recall": 0.0,
                "passed": False,
                "missing": [{"start_ms": 250, "effect": expected}],
                "extra_predictions": [
                    {
                        "event_id": "e1",
                        "time": 750,
                        "effect": {"kind": "fan_change", "amount": 2},
                    }
                ],
                "evidence_errors": [],
                "timing_errors": [],
                "reviewed_samples": 4,
                "evidence_hashes_checked": True,
                "source_onset_windows": 0,
                "reference_observability": "not_adjudicated",
            }

        scored = score_reference_index(
            index,
            report,
            self.root,
            evidence_root=self.root,
            evaluator=fake_evaluator,
        )
        self.assertEqual(seen["root"], self.root.resolve())
        self.assertEqual(len(scored["per_reference"]), 1)
        self.assertEqual(scored["per_reference"][0]["missing_count"], 1)
        self.assertEqual(scored["per_reference"][0]["extra_count"], 1)
        self.assertEqual(len(scored["missing_extra_issues"]), 2)
        self.assertFalse(scored["claims"]["aggregate_accuracy_claimed"])
        self.assertFalse(scored["claims"]["full_recording_effect_recall_measured"])
        self.assertFalse(scored["partitions"][0]["partition_accuracy_claimed"])

    def test_complete_reference_does_not_establish_independent_eligibility(self):
        data, _ = self.write_reference("complete.json", complete=True)
        data["independent_recording"] = False
        path = self.root / "complete.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        index = build_reference_index(
            self.registry("source", 1000, ("complete.json", digest, 0, 1000, 250)),
            self.root,
        )
        scored = score_reference_index(
            index,
            {"source": {"sha256": "source"}},
            self.root,
            evaluator=lambda reference, report, root: {
                "expected": 0,
                "predicted": 0,
                "matched": 0,
                "passed": True,
            },
        )
        self.assertFalse(scored["per_reference"][0]["independent_test_eligible"])
        self.assertFalse(scored["partitions"][0]["score"]["independent_test_eligible"])

    def test_evaluator_runtime_errors_are_not_annotation_blocks(self):
        _, digest = self.write_reference("effects.json")
        index = build_reference_index(
            self.registry("source", 1000, ("effects.json", digest, 0, 1000, 250)),
            self.root,
        )

        def broken_evaluator(reference, report, root):
            raise RuntimeError("implementation bug")

        with self.assertRaisesRegex(RuntimeError, "implementation bug"):
            score_reference_index(
                index,
                {"source": {"sha256": "source"}},
                self.root,
                evaluator=broken_evaluator,
            )

    def test_score_report_source_mismatch_and_reference_change_fail_closed(self):
        _, digest = self.write_reference("effects.json")
        index = build_reference_index(
            self.registry("source", 1000, ("effects.json", digest, 0, 1000, 250)),
            self.root,
        )
        with self.assertRaisesRegex(ReferenceIndexError, "Report source hash"):
            score_reference_index(index, {"source": {"sha256": "other"}}, self.root)

        self.write_reference("effects.json", source="source", complete=True)
        with self.assertRaisesRegex(ReferenceIndexError, "Indexed reference hash mismatch"):
            score_reference_index(
                index,
                {"source": {"sha256": "source"}},
                self.root,
                evaluator=lambda reference, report, root: {},
            )


if __name__ == "__main__":
    unittest.main()
