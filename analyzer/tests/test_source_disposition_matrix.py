import copy
import hashlib
import json
from pathlib import Path
import unittest
import uuid

from tools.source_disposition_matrix import (
    MatrixError,
    _actual_view,
    _interval,
    _pointer_get,
    _row_interval,
    build_matrix,
    verify_matrix,
)


SHA_SOURCE = "a" * 64
ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class SourceDispositionMatrixTests(unittest.TestCase):
    def setUp(self):
        # The managed Windows runner denies child-directory creation for
        # Python-created temporary directories.  Keep this fixture flat under
        # the already writable tests directory and remove only our own files.
        self.root = ROOT / "analyzer" / "tests"
        self._owned_paths: set[Path] = set()
        self._token = uuid.uuid4().hex
        self.source_root = self.root
        self.baseline_root = self.root
        self.final_root = self.root
        self.relative_evidence = f"source-disposition-{self._token}-frame.png"
        frame = self.root / self.relative_evidence
        frame.write_bytes(b"source frame")
        self._owned_paths.add(frame)
        self.reference_path = self.root / f"source-disposition-{self._token}-reference.json"
        self.freeze_path = self.root / f"source-disposition-{self._token}-freeze.json"
        self._owned_paths.update((self.reference_path, self.freeze_path))
        self._write_inputs()

    def tearDown(self):
        for path in sorted(self._owned_paths, key=lambda value: len(str(value)), reverse=True):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _write_inputs(self, *, final_amount=5, final_evidence=None,
                      duplicate_baseline=False, source_sha=SHA_SOURCE):
        source_image_hash = _sha(self.source_root / self.relative_evidence)
        reference = {
            "schema_version": "final-reliability-source-reference-v1",
            "run": "synthetic",
            "source_sha256": source_sha,
            "image_sha256": {self.relative_evidence: source_image_hash},
            "cases": [{
                "case_id": "synthetic-case",
                "turn_id": "turn-001",
                "scope_ms": [100, 200],
                "observations": [{
                    "id": "source-speed",
                    "category": "effect",
                    "phase": "applied",
                    "start_ms": 100,
                    "end_ms": 100,
                    "evidence": [self.relative_evidence],
                    "payload": {"kind": "stat_change", "field": "speed", "amount": 5},
                    "expected_turn_id": "turn-001",
                }],
            }],
        }
        _write_json(self.reference_path, reference)
        _write_json(self.freeze_path, {"schema_version": "synthetic-freeze-v1", "run": "synthetic"})

        def report(amount, evidence):
            return {
                "source": {"sha256": source_sha, "duration_ms": 300},
                "gameplay_tracking": {
                    "events": [{
                        "id": "training-1",
                        "kind": "training",
                        "training_option": "speed",
                        "first_seen_ms": 100,
                        "last_seen_ms": 100,
                        "deltas": {"speed": amount},
                        "evidence": evidence,
                        "field_evidence": {"speed": [evidence]},
                    }],
                    "turn_action_receipts": [],
                    "checkpoints": [],
                    "state_observations": [],
                    "status_observations": [],
                    "preview_observations": [],
                },
            }

        baseline_report = report(4, self.relative_evidence)
        final_report = report(final_amount, final_evidence or self.relative_evidence)
        self.baseline_report_path = self.root / f"source-disposition-{self._token}-baseline-report.json"
        self.final_report_path = self.root / f"source-disposition-{self._token}-final-report.json"
        self._owned_paths.update((self.baseline_report_path, self.final_report_path))
        _write_json(self.baseline_report_path, baseline_report)
        _write_json(self.final_report_path, final_report)

        def grade(report_path, *, status, prediction_id, amount, turn_status="correct"):
            row = {
                "source_id": "source-speed",
                "category": "effect",
                "phase": "applied",
                "source_interval_ms": [100, 100],
                "source_evidence": [self.relative_evidence],
                "prediction_id": prediction_id,
                "fields": [
                    {"field": "/kind", "expected": "stat_change", "actual": "stat_change",
                     "status": "correct" if status == "correct" else "missed"},
                    {"field": "/field", "expected": "speed", "actual": "speed",
                     "status": "correct" if status == "correct" else "missed"},
                    {"field": "/amount", "expected": 5, "actual": amount,
                     "status": "correct" if status == "correct" else "missed"},
                ],
                "status": status,
                "expected_turn_id": "turn-001",
                "prediction_turn_id": "turn-001" if prediction_id else None,
                "prediction_candidate_turn_ids": ["turn-001"] if prediction_id else [],
                "turn_status": turn_status,
            }
            cases = [{"case_id": "synthetic-case", "turn_id": "turn-001", "results": [row]}]
            if duplicate_baseline and report_path == self.baseline_report_path:
                cases[0]["results"].append(copy.deepcopy(row))
            return {
                "schema_version": "synthetic-grade-v1",
                "run": "synthetic",
                "source_sha256": source_sha,
                "image_sha256": {self.relative_evidence: source_image_hash},
                "reference_sha256": _sha(self.reference_path),
                "report_sha256": _sha(report_path),
                "freeze_manifest_sha256": _sha(self.freeze_path),
                "cases": cases,
            }

        self.baseline_grade_path = self.root / f"source-disposition-{self._token}-baseline-grade.json"
        self.final_grade_path = self.root / f"source-disposition-{self._token}-final-grade.json"
        self._owned_paths.update((self.baseline_grade_path, self.final_grade_path))
        _write_json(self.baseline_grade_path, grade(
            self.baseline_report_path, status="missed", prediction_id=None, amount=4,
            turn_status="missing"))
        _write_json(self.final_grade_path, grade(
            self.final_report_path, status="correct",
            prediction_id="/gameplay_tracking/events/0/deltas/speed", amount=final_amount))
        self.worker_result_path = self.root / f"source-disposition-{self._token}-worker-result.json"
        self._owned_paths.add(self.worker_result_path)
        _write_json(self.worker_result_path, {
            "exit_code": 0,
            "report_sha256": _sha(self.final_report_path),
            "replay_manifest_unchanged": True,
            "lesson_evidence_unchanged": True,
            "implementation_unchanged": True,
            "hint_cache_unchanged": True,
        })

    def _build(self, **kwargs):
        return build_matrix(
            baseline_grade_path=self.baseline_grade_path,
            baseline_report_path=self.baseline_report_path,
            final_grade_path=self.final_grade_path,
            final_report_path=self.final_report_path,
            source_reference_path=self.reference_path,
            freeze_manifest_path=self.freeze_path,
            baseline_evidence_root=self.baseline_root,
            final_evidence_root=self.final_root,
            worker_result_path=self.worker_result_path,
            **kwargs,
        )

    def test_fixed_core_requires_independent_identity_and_evidence(self):
        matrix = self._build()
        row = matrix["rows"][0]
        self.assertEqual(row["disposition"], "fixed_core")
        self.assertTrue(row["prediction_binding"]["all_passed"])
        self.assertTrue(row["prediction_binding"]["checks"]["source_evidence_identity_matches"])
        self.assertFalse(matrix["acceptance_eligible"], "full-report source binding is still pending")

    def test_frozen_label_review_is_advisory_and_never_fixed_core(self):
        review = self.root / "label-review.json"
        self._owned_paths.add(review)
        _write_json(review, {"dispositions": [{
            "source_id": "source-speed",
            "classification": "frozen_label_mismatch",
            "finding": "The frozen turn is one sample early.",
            "action": "Preserve the strict historical row.",
        }]})
        matrix = self._build(review_paths=[review])
        row = matrix["rows"][0]
        self.assertEqual(row["disposition"], "frozen_label_error_advisory")
        self.assertFalse(row["counts_as_fixed_core"])

    def test_worker_rejection_is_diagnostic_only(self):
        rejected = self.root / "rejected-worker.json"
        self._owned_paths.add(rejected)
        _write_json(rejected, {
            "exit_code": 1,
            "report_sha256": _sha(self.final_report_path),
            "replay_manifest_unchanged": True,
            "lesson_evidence_unchanged": True,
            "implementation_unchanged": True,
            "hint_cache_unchanged": True,
        })
        matrix = build_matrix(
            baseline_grade_path=self.baseline_grade_path,
            baseline_report_path=self.baseline_report_path,
            final_grade_path=self.final_grade_path,
            final_report_path=self.final_report_path,
            source_reference_path=self.reference_path,
            freeze_manifest_path=self.freeze_path,
            baseline_evidence_root=self.baseline_root,
            final_evidence_root=self.final_root,
            worker_result_path=rejected,
        )
        self.assertEqual(matrix["status"], "diagnostic_only_worker_rejected")
        self.assertFalse(matrix["acceptance_eligible"])

    def test_duplicate_baseline_rows_are_rejected(self):
        self._write_inputs(duplicate_baseline=True)
        with self.assertRaisesRegex(MatrixError, "Duplicate baseline/final source_id"):
            self._build()

    def test_verifier_rejects_missing_matrix_row(self):
        matrix = self._build()
        path = self.root / f"source-disposition-{self._token}-matrix.json"
        self._owned_paths.add(path)
        _write_json(path, matrix)
        matrix["rows"].pop()
        _write_json(path, matrix)
        with self.assertRaisesRegex(MatrixError, "row count differs"):
            verify_matrix(path)

    def test_verifier_rejects_tampered_input_hash(self):
        matrix = self._build()
        path = self.root / f"source-disposition-{self._token}-matrix.json"
        self._owned_paths.add(path)
        _write_json(path, matrix)
        matrix["inputs"]["binding"]["artifacts"]["source_reference"]["sha256"] = "0" * 64
        _write_json(path, matrix)
        with self.assertRaisesRegex(MatrixError, "Matrix input source_reference changed"):
            verify_matrix(path)

    def test_wrong_source_is_not_accepted(self):
        self._write_inputs(source_sha="b" * 64)
        # The reports and reference agree with one another, but the grade's
        # original source binding is deliberately changed after generation.
        grade = json.loads(self.final_grade_path.read_text(encoding="utf-8"))
        grade["source_sha256"] = SHA_SOURCE
        _write_json(self.final_grade_path, grade)
        matrix = self._build()
        self.assertFalse(matrix["inputs"]["binding"]["all_passed"])
        self.assertFalse(matrix["acceptance_eligible"])
        with self.assertRaisesRegex(MatrixError, "diagnostic only"):
            path = self.root / "wrong-source-matrix.json"
            self._owned_paths.add(path)
            _write_json(path, matrix)
            verify_matrix(path, require_acceptance=True)

    def test_same_amount_with_wrong_evidence_is_not_fixed(self):
        other = f"source-disposition-{self._token}-other-frame.png"
        path = self.final_root / other
        path.write_bytes(b"different source frame")
        self._owned_paths.add(path)
        self._write_inputs(final_evidence=other)
        # Recreate the final report/grade after changing the evidence path.
        matrix = self._build()
        row = matrix["rows"][0]
        self.assertEqual(row["after"]["status"], "correct")
        self.assertFalse(row["prediction_binding"]["checks"]["source_evidence_identity_matches"])
        self.assertEqual(row["disposition"], "unresolved")
        self.assertFalse(row["counts_as_fixed_core"])

    def test_opposite_same_evidence_cancellation_is_not_fixed(self):
        report = json.loads(self.final_report_path.read_text(encoding="utf-8"))
        report["gameplay_tracking"]["events"].append({
            "id": "cancellation",
            "kind": "training",
            "first_seen_ms": 100,
            "last_seen_ms": 100,
            "deltas": {"speed": -5},
            "evidence": self.relative_evidence,
            "field_evidence": {"speed": [self.relative_evidence]},
        })
        _write_json(self.final_report_path, report)
        grade = json.loads(self.final_grade_path.read_text(encoding="utf-8"))
        grade["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.final_grade_path, grade)
        worker = json.loads(self.worker_result_path.read_text(encoding="utf-8"))
        worker["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.worker_result_path, worker)
        matrix = self._build()
        row = matrix["rows"][0]
        self.assertFalse(row["prediction_binding"]["checks"]["no_conflicting_cancellation"])
        self.assertEqual(row["disposition"], "unresolved")

    def test_unjustified_fixed_core_assertion_is_rejected(self):
        missing = f"source-disposition-{self._token}-missing.png"
        self._write_inputs(final_evidence=missing)
        matrix = self._build()
        matrix["rows"][0]["disposition"] = "fixed_core"
        path = self.root / "forged-fixed-core.json"
        self._owned_paths.add(path)
        _write_json(path, matrix)
        with self.assertRaisesRegex(MatrixError, "disposition"):
            verify_matrix(path)

    def test_json_pointer_rejects_noncanonical_list_indexes(self):
        value = {"items": ["zero", "one"]}
        self.assertEqual(_pointer_get(value, "/items/1"), "one")
        for pointer in ("/items/-1", "/items/+1", "/items/01"):
            with self.assertRaisesRegex(MatrixError, "Noncanonical list index"):
                _pointer_get(value, pointer)

    def test_invalid_intervals_never_become_source_time(self):
        for interval in ([20, 10], [-5, 10], [10, -5], [10, 10.0]):
            self.assertIsNone(_interval(interval))
            self.assertIsNone(_row_interval({"start_ms": interval[0], "end_ms": interval[1]}))

    def test_typed_field_evidence_takes_precedence_over_broad_event_evidence(self):
        report = {
            "gameplay_tracking": {
                "events": [{
                    "first_seen_ms": 100,
                    "last_seen_ms": 100,
                    "deltas": {"speed": 5},
                    "evidence": ["wrong/broad.png"],
                    "field_evidence": {"speed": ["right/field.png"]},
                }],
            },
        }
        actual = _actual_view(report, "/gameplay_tracking/events/0/deltas/speed")
        self.assertEqual(actual["evidence_paths"], ["right/field.png"])

    def test_atom_comparison_binds_before_and_after_report_hashes(self):
        comparison = self.root / f"source-disposition-{self._token}-atom-comparison.json"
        self._owned_paths.add(comparison)
        _write_json(comparison, {
            "source_sha256": SHA_SOURCE,
            "input_sha256": {
                str(self.baseline_report_path.resolve()): _sha(self.baseline_report_path),
                str(self.final_report_path.resolve()): _sha(self.final_report_path),
            },
            "added": [],
            "removed": [],
            "evidence_changes": [],
        })
        matrix = self._build(atom_comparison_path=comparison)
        self.assertEqual(matrix["full_report_changes"]["status"], "no_atom_changes_reported")
        self.assertFalse(matrix["acceptance_eligible"])
        self.assertTrue(matrix["invariants"]["acceptance_eligible_always_false"])
        self.assertEqual(
            matrix["full_report_changes"]["input_binding"]["report_hash_binding_mode"],
            "report_paths",
        )
        comparison_value = json.loads(comparison.read_text(encoding="utf-8"))
        comparison_value["input_sha256"] = {
            "before": _sha(self.final_report_path),
            "after": _sha(self.baseline_report_path),
        }
        _write_json(comparison, comparison_value)
        matrix = self._build(atom_comparison_path=comparison)
        self.assertEqual(matrix["full_report_changes"]["status"], "pending_invalid_comparison_binding")

    def test_final_grade_row_metadata_is_rebound_to_source_reference(self):
        grade = json.loads(self.final_grade_path.read_text(encoding="utf-8"))
        row = grade["cases"][0]["results"][0]
        row["category"] = "state"
        row["source_interval_ms"] = [999, 999]
        row["source_evidence"] = [f"source-disposition-{self._token}-foreign.png"]
        _write_json(self.final_grade_path, grade)
        matrix = self._build()
        result = matrix["rows"][0]
        self.assertFalse(result["final_source_binding"]["all_passed"])
        self.assertIn("category_mismatch", result["final_source_binding"]["errors"])
        self.assertIn("interval_mismatch", result["final_source_binding"]["errors"])
        self.assertIn("source_evidence_alias_mismatch", result["final_source_binding"]["errors"])
        self.assertEqual(result["disposition"], "unresolved")

    def test_broad_report_interval_overlap_is_not_exact_source_ownership(self):
        report = json.loads(self.final_report_path.read_text(encoding="utf-8"))
        event = report["gameplay_tracking"]["events"][0]
        event["first_seen_ms"] = 0
        event["last_seen_ms"] = 1000
        _write_json(self.final_report_path, report)
        grade = json.loads(self.final_grade_path.read_text(encoding="utf-8"))
        grade["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.final_grade_path, grade)
        worker = json.loads(self.worker_result_path.read_text(encoding="utf-8"))
        worker["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.worker_result_path, worker)
        matrix = self._build()
        result = matrix["rows"][0]
        self.assertTrue(result["prediction_binding"]["checks"]["timestamp_overlaps_source"])
        self.assertFalse(result["prediction_binding"]["checks"]["timestamp_exact_source_interval"])
        self.assertIn("prediction_time_not_exact_source_interval",
                      result["prediction_binding"]["errors"])
        self.assertEqual(result["disposition"], "unresolved")

    def test_duplicate_typed_occurrences_are_not_fixed(self):
        report = json.loads(self.final_report_path.read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(report["gameplay_tracking"]["events"][0])
        duplicate["id"] = "same-value-second-event"
        report["gameplay_tracking"]["events"].append(duplicate)
        _write_json(self.final_report_path, report)
        grade = json.loads(self.final_grade_path.read_text(encoding="utf-8"))
        grade["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.final_grade_path, grade)
        worker = json.loads(self.worker_result_path.read_text(encoding="utf-8"))
        worker["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.worker_result_path, worker)
        matrix = self._build()
        row = matrix["rows"][0]
        self.assertFalse(row["prediction_binding"]["checks"]["unique_occurrence"])
        self.assertEqual(row["disposition"], "unresolved")

    def test_same_typed_time_with_disjoint_evidence_is_not_silently_unique(self):
        other = f"source-disposition-{self._token}-duplicate-frame.png"
        other_path = self.final_root / other
        other_path.write_bytes(b"different duplicate frame")
        self._owned_paths.add(other_path)
        report = json.loads(self.final_report_path.read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(report["gameplay_tracking"]["events"][0])
        duplicate["id"] = "disjoint-evidence-second-event"
        duplicate["evidence"] = [other]
        duplicate["field_evidence"] = {"speed": [other]}
        report["gameplay_tracking"]["events"].append(duplicate)
        _write_json(self.final_report_path, report)
        grade = json.loads(self.final_grade_path.read_text(encoding="utf-8"))
        grade["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.final_grade_path, grade)
        worker = json.loads(self.worker_result_path.read_text(encoding="utf-8"))
        worker["report_sha256"] = _sha(self.final_report_path)
        _write_json(self.worker_result_path, worker)
        matrix = self._build()
        result = matrix["rows"][0]
        self.assertFalse(result["prediction_binding"]["checks"]["unique_occurrence"])
        self.assertIn("duplicate_report_occurrence_identity_unproven",
                      result["prediction_binding"]["errors"])
        self.assertEqual(result["disposition"], "unresolved")

    def test_phase_mismatch_is_not_fixed(self):
        grade = json.loads(self.final_grade_path.read_text(encoding="utf-8"))
        grade["cases"][0]["results"][0]["phase"] = "preview"
        _write_json(self.final_grade_path, grade)
        matrix = self._build()
        row = matrix["rows"][0]
        self.assertFalse(row["prediction_binding"]["checks"]["semantic_identity_matches"])
        self.assertEqual(row["disposition"], "unresolved")


if __name__ == "__main__":
    unittest.main()
