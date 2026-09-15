"""Focused G8 checks for the Python worker/report boundary.

These tests keep the producer mocked so they exercise the terminal worker
envelope and report validation without starting OCR.  The real cached runs
and their cache-preserving procedure are recorded in
``.local/final-reliability-v1/worker-contract-review.md``.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_gameplay import workspace_temp
from tests.test_report_contract import valid_report
from tests.test_turn_ledger import checkpoint, report as ledger_report
from tracen_replay.analysis_job import JOB_SCHEMA, _evidence_paths, _validate_evidence_paths, main
from tracen_replay.causal_accounting import build as build_causal_accounting
from tracen_replay.pipeline import PipelineError
from tracen_replay.reconcile import FIELDS
from tracen_replay.turn_ledger import build as build_turn_ledger


def _worker_report(source: Path, *, partial: bool = False, rich: bool = False,
                   recovery: bool = False) -> dict:
    """Build a small valid full report with optional G8 boundary semantics."""
    report = copy.deepcopy(valid_report())
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report["source"].update(
        name=source.name,
        sha256=source_hash,
        size_bytes=source.stat().st_size,
        duration_ms=5000,
    )

    data = report["gameplay_tracking"]
    data["readings"] = []
    data["checkpoints"] = []
    data["intervals"] = []
    data["performance_accounting"] = {"checkpoints": [], "intervals": []}

    if rich:
        # Keep the fixture's required arrays while borrowing the compact
        # timestamped readings used by the turn-ledger tests.
        for key, value in ledger_report()["gameplay_tracking"].items():
            data[key] = copy.deepcopy(value)
        data["checkpoints"] = [
            checkpoint("before", 500, 10),
            checkpoint("after", 2200, 15),
        ]
        observed = {field: 5 for field in FIELDS}
        supported = {field: 0 for field in FIELDS}
        data["intervals"] = [{
            "start_ms": 600,
            "end_ms": 2200,
            "observed_change": observed,
            "supported_change": supported,
            "unexplained_change": observed,
            "status": "unresolved",
        }]
        data["events"] = [{
            "id": "training-1",
            "kind": "training",
            "first_seen_ms": 700,
            "last_seen_ms": 900,
            "evidence": "events/training.png",
            "deltas": {"speed": 5},
            "result_state_derived_fields": ["speed"],
            "field_evidence": {"speed": ["events/training.png"]},
            "effects": [{
                "kind": "stat_change",
                "field": "stamina",
                "amount": None,
            }],
            # These are names/reasons, not filesystem paths.  The worker must
            # leave them alone while checking the actual evidence path above.
            "raw_receipt_name_candidates": ["../../unreadable receipt"],
            "uncertainty_reasons": ["C:\\OCR\\confidence note"],
        }]
        data["turn_action_receipts"] = [{
            "kind": "training",
            "source_timestamp_ms": 700,
            "evidence": "events/training.png",
            "event_id": "training-1",
        }]
        report["turn_ledger"] = build_turn_ledger(report)
        report["causal_accounting"] = build_causal_accounting(report)

    if "turn_ledger" not in report:
        report["turn_ledger"] = build_turn_ledger(report)

    report["verification"] = {
        "source_sha256": source_hash,
        "source_duration_ms": 5000,
        "full_source_processed": not partial,
        "source_coverage_errors": (["missing_base_timestamp:4000"] if partial else []),
        "base_frames_expected": 20,
        "base_frames_processed": 19 if partial else 20,
        "fully_verified": False,
        "go_ready": False,
    }
    if recovery:
        report["training_gain_recovery"] = {
            "schema_version": "tracen-replay/training-gain-recovery-v1",
            "method": "bounded_training_gain_recovery",
            "source_sha256": source_hash,
            "requested_windows": [{"start_ms": 600, "end_ms": 900}],
            "processed_windows": [{"start_ms": 600, "end_ms": 900}],
            "pending_windows": [{
                "start_ms": 4000,
                "end_ms": 4500,
                "pending_reason": "ocr_disabled",
            }],
            "new_frames": 0,
            "promoted_source_timestamps": 0,
            "complete_event_history": False,
        }
    return report


def _write_report(output: Path, report: dict) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    evidence_root = output.resolve()
    for _, evidence in _evidence_paths(report, "report"):
        path = (evidence_root / evidence).resolve(strict=False)
        try:
            path.relative_to(evidence_root)
        except ValueError:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"evidence")
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def _run_worker(source: Path, output: Path, producer) -> tuple[int, dict, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
            patch("sys.stdout", stdout), patch("sys.stderr", stderr):
        result = main([str(source), "--output", str(output), "--reparse-only"])
    records = [line for line in stdout.getvalue().splitlines() if line]
    assert len(records) == 1, stdout.getvalue()
    return result, json.loads(records[0]), stdout.getvalue()


class FinalWorkerContractTests(unittest.TestCase):
    def test_partial_report_succeeds_and_retains_unknown_and_derived_fields(self):
        """Recognition incompleteness is a report result, not a job failure."""
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report = _worker_report(source, partial=True, rich=True, recovery=True)
            report["verification"]["sampled_coverage_by_minute"] = [{
                "start_ms": 0,
                "end_ms": 5000,
                "base_frames_expected": 20,
                "base_frames_processed": 19,
                "classified_observations": 0,
                "unknown_observations": 0,
                "reviewed": False,
            }]

            def producer():
                _write_report(output, report)
                print("cached observations reparsed")

            result, payload, raw_stdout = _run_worker(source, output, producer)

            self.assertEqual(result, 0)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["schema_version"], JOB_SCHEMA)
            self.assertEqual(payload["status"], "succeeded")
            self.assertFalse(payload["full_source_processed"])
            self.assertFalse(payload["fully_verified"])
            self.assertFalse(payload["go_ready"])
            self.assertNotIn("error", payload)

            saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["verification"]["source_coverage_errors"],
                             ["missing_base_timestamp:4000"])
            self.assertEqual(saved["training_gain_recovery"]["pending_windows"][0]["pending_reason"],
                             "ocr_disabled")
            contribution_bases = {
                contribution["basis"]
                for contribution in saved["causal_accounting"]["contributions"]
            }
            self.assertIn("state_derived", contribution_bases)
            unknown = next(
                contribution for contribution in saved["causal_accounting"]["contributions"]
                if contribution["amount"] is None
            )
            self.assertEqual(unknown["basis"], "observed_receipt")
            self.assertTrue(all(
                turn["complete_event_history"] is False
                for turn in saved["turn_ledger"]["turns"]
            ))

    def test_failure_after_writing_partial_report_has_only_failure_fields(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report = _worker_report(source, partial=True, rich=True)

            def producer():
                _write_report(output, report)
                raise PipelineError("cached observations are incomplete")

            result, payload, raw_stdout = _run_worker(source, output, producer)

            self.assertEqual(result, 1)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "producer_failed")
            for field in ("report_path", "report_sha256", "source_sha256", "evidence_root",
                          "full_source_processed", "fully_verified", "go_ready"):
                self.assertNotIn(field, payload)

    def test_contradictory_full_source_status_is_not_published(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report = _worker_report(source, partial=True, rich=True)
                report["verification"]["full_source_processed"] = True
                _write_report(output, report)

            result, payload, _ = _run_worker(source, output, producer)

            self.assertEqual(result, 1)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "invalid_full_source_processed")
            self.assertNotIn("report_sha256", payload)

    def test_complete_source_status_requires_all_base_frames_processed(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report = _worker_report(source, partial=False)
                report["verification"]["base_frames_processed"] -= 1
                _write_report(output, report)

            result, payload, _ = _run_worker(source, output, producer)

            self.assertEqual(result, 1)
            self.assertEqual(payload["error"]["code"], "invalid_full_source_processed")

    def test_complete_source_status_rejects_declared_missing_base_timestamps(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report = _worker_report(source, partial=False)
                report["verification"]["missing_base_timestamps_ms"] = [4000]
                _write_report(output, report)

            result, payload, _ = _run_worker(source, output, producer)

            self.assertEqual(result, 1)
            self.assertEqual(payload["error"]["code"], "invalid_full_source_processed")

    def test_complete_source_status_rejects_incomplete_coverage_bin(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report = _worker_report(source, partial=False)
                report["verification"]["sampled_coverage_by_minute"] = [{
                    "start_ms": 0,
                    "end_ms": 5000,
                    "base_frames_expected": 20,
                    "base_frames_processed": 19,
                    "classified_observations": 0,
                    "unknown_observations": 0,
                    "reviewed": False,
                }]
                _write_report(output, report)

            result, payload, _ = _run_worker(source, output, producer)

            self.assertEqual(result, 1)
            self.assertEqual(payload["error"]["code"], "invalid_full_source_processed")

    def test_coverage_bins_must_match_partial_report_totals(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report = _worker_report(source, partial=True)
                report["verification"]["sampled_coverage_by_minute"] = [{
                    "start_ms": 0,
                    "end_ms": 5000,
                    "base_frames_expected": 20,
                    "base_frames_processed": 18,
                    "classified_observations": 0,
                    "unknown_observations": 0,
                    "reviewed": False,
                }]
                _write_report(output, report)

            result, payload, _ = _run_worker(source, output, producer)

            self.assertEqual(result, 1)
            self.assertEqual(payload["error"]["code"], "invalid_report_status")
            self.assertIn("processed counts", payload["error"]["message"])

    def test_verification_status_implications_are_required(self):
        for field, expected_code in (("fully_verified", "invalid_fully_verified"),
                                     ("go_ready", "invalid_go_ready")):
            with self.subTest(field=field), workspace_temp() as root:
                source = root / "run.mp4"
                source.write_bytes(b"source")
                output = root / "output"

                def producer():
                    report = _worker_report(source, partial=True)
                    report["verification"][field] = True
                    _write_report(output, report)

                result, payload, _ = _run_worker(source, output, producer)

                self.assertEqual(result, 1)
                self.assertEqual(payload["error"]["code"], expected_code)

    def test_missing_timestamps_must_be_sorted_unique_and_in_source(self):
        for missing, message in (([4, 1, 1], "sorted and unique"),
                                 ([1, 5001], "within the source")):
            with self.subTest(missing=missing), workspace_temp() as root:
                source = root / "run.mp4"
                source.write_bytes(b"source")
                output = root / "output"

                def producer():
                    report = _worker_report(source, partial=True)
                    report["verification"]["missing_base_timestamps_ms"] = missing
                    _write_report(output, report)

                result, payload, _ = _run_worker(source, output, producer)

                self.assertEqual(result, 1)
                self.assertEqual(payload["error"]["code"], "invalid_report_status")
                self.assertIn(message, payload["error"]["message"])

    def test_existing_partial_report_noop_is_stale(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            _write_report(output, _worker_report(source, partial=True, rich=True))

            result, payload, raw_stdout = _run_worker(source, output, lambda: None)

            self.assertEqual(result, 1)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "report_stale")
            self.assertNotIn("report_sha256", payload)

    def test_source_change_after_report_write_is_source_mismatch(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                _write_report(output, _worker_report(source, partial=True))
                source.write_bytes(b"source changed")

            result, payload, raw_stdout = _run_worker(source, output, producer)

            self.assertEqual(result, 1)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["error"]["code"], "source_mismatch")
            self.assertNotIn("report_sha256", payload)

    def test_out_of_range_frame_timestamp_is_rejected_before_success(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report = _worker_report(source, partial=True, rich=True)
            report["frames"][1]["source_timestamp_ms"] = report["source"]["duration_ms"] + 1

            result, payload, raw_stdout = _run_worker(
                source, output, lambda: _write_report(output, report)
            )

            self.assertEqual(result, 1)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["error"]["code"], "invalid_report")
            self.assertNotIn("report_sha256", payload)

    def test_source_pts_provenance_mismatch_is_rejected_before_success(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report = _worker_report(source, partial=True, rich=True)
            report["frames"][1]["source_pts"] += 1

            result, payload, raw_stdout = _run_worker(
                source, output, lambda: _write_report(output, report)
            )

            self.assertEqual(result, 1)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["error"]["code"], "invalid_report")
            self.assertNotIn("report_sha256", payload)

    def test_nested_evidence_paths_are_checked_without_treating_metadata_as_paths(self):
        from tracen_replay.analysis_job import JobInputError

        with workspace_temp() as root:
            evidence = root / "gameplay/0001.png"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_bytes(b"evidence")
            payload = {
                "field_evidence": {
                    "speed": {"observed": ["gameplay/0001.png"]},
                },
                "raw_receipt_name_candidates": ["../../name is not a path"],
                "uncertainty_reasons": ["C:\\OCR\\reason"],
            }
            self.assertEqual(
                {path for _, path in _evidence_paths(payload, "report")},
                {"gameplay/0001.png"},
            )
            _validate_evidence_paths(payload, root)
            with self.assertRaisesRegex(JobInputError, "inside the evidence root"):
                _validate_evidence_paths(
                    {"field_evidence": {"speed": ["../../outside.png"]}}, root
                )

    def test_in_root_symlink_resolves_to_a_real_in_root_file(self):
        with workspace_temp() as root:
            target = root / "gameplay/0001.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"evidence")
            link = root / "gameplay/current.png"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            _validate_evidence_paths({"evidence": "gameplay/current.png"}, root)

    def test_same_root_evidence_metadata_is_identity_checked_and_accepted(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report = _worker_report(source, partial=True, rich=True)

            def producer():
                report["evaluation_context"] = {
                    "evidence_root": str(output.resolve()),
                    "development_recording": True,
                }
                _write_report(output, report)

            result, payload, _ = _run_worker(source, output, producer)
            self.assertEqual(result, 0)
            self.assertEqual(payload["status"], "succeeded")

    def test_conflicting_evidence_root_metadata_is_a_structured_failure(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report = _worker_report(source, partial=True, rich=True)
            report["evaluation_context"] = {
                "evidence_root": str((root / "other-cache").resolve()),
            }

            result, payload, raw_stdout = _run_worker(
                source, output, lambda: _write_report(output, report)
            )
            self.assertEqual(result, 1)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["error"]["code"], "evidence_root_mismatch")
            self.assertNotIn("report_sha256", payload)

    def test_hash_and_verified_metadata_never_becomes_a_path(self):
        with workspace_temp() as root:
            evidence = root / "gameplay/0001.png"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_bytes(b"evidence")
            payload = {
                "field_evidence": {
                    "speed": {
                        "observed": ["gameplay/0001.png"],
                        "evidence_sha256": ["C:\\outside\\sha256"],
                        "verified": "C:\\outside\\verification-note",
                        "evidence_scope": "C:\\outside\\scope-label",
                    },
                },
                "hashes": {"evidence": "C:\\outside\\hash-map"},
            }
            self.assertEqual(
                list(_evidence_paths(payload, "report")),
                [("report.field_evidence.speed.observed[0]", "gameplay/0001.png")],
            )
            _validate_evidence_paths(payload, root)

    def test_missing_in_root_evidence_is_not_published(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report = _worker_report(source, partial=True, rich=True)

            def producer():
                _write_report(output, report)
                (output / "events/training.png").unlink()

            result, payload, raw_stdout = _run_worker(source, output, producer)
            self.assertEqual(result, 1)
            self.assertEqual(raw_stdout.count("\n"), 1)
            self.assertEqual(payload["error"]["code"], "evidence_missing")
            self.assertNotIn("report_sha256", payload)


if __name__ == "__main__":
    unittest.main()
