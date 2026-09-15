import hashlib
import json
from pathlib import Path
import shutil
import unittest
import uuid
from base64 import b64decode
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:  # Pillow is an optional analysis dependency.
    Image = None

import tools.audit_final_reference_proofs as proof_audit


class FinalReferenceProofAuditTests(unittest.TestCase):
    source_sha256 = "a" * 64

    def setUp(self):
        self.root = (Path(__file__).resolve().parents[2] / ".local" / "test-runs"
                     / uuid.uuid4().hex).resolve()
        self.root.mkdir(parents=True)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.reference_path = self.root / "reference.json"
        self.report_path = self.root / "before-report.json"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def image(self, relative="dense/frame-000007.png"):
        path = self.evidence / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if Image is not None:
            Image.new("RGB", (8, 8), "white").save(path)
        else:
            # 1x1 opaque PNG for environments without optional Pillow.  The
            # audit still exercises path, timestamp and hash handling; the
            # dedicated invalid-image case is skipped there.
            path.write_bytes(b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "+w8AAgEBAScY42YAAAAASUVORK5CYII="))
        return path

    def write_json(self, path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def reference(self, evidence, *, start=100, end=150, image_hash=None):
        image_hash = image_hash or hashlib.sha256((self.evidence / evidence).read_bytes()).hexdigest()
        payload = {
            "schema_version": "final-reliability-source-reference-v1",
            "run": "fixture",
            "source_sha256": self.source_sha256,
            "cases": [{
                "case_id": "fixture-case",
                "turn_id": "turn-1",
                "scope_ms": [0, 1000],
                "reference_complete": True,
                "review_coverage": {"categories": ["effect"], "fields": ["speed"],
                                    "intervals_ms": [[0, 1000]]},
                "observations": [{
                    "id": "speed-effect",
                    "category": "effect",
                    "phase": "applied",
                    "start_ms": start,
                    "end_ms": end,
                    "evidence": [evidence],
                    "payload": {"kind": "stat_change", "field": "speed", "amount": 5},
                    "expected_turn_id": "turn-1",
                    "status": "observed",
                }],
                "unobservable_intervals": [],
                "notes": [],
            }],
            "image_sha256": {evidence: image_hash},
        }
        self.write_json(self.reference_path, payload)

    def report(self, evidence, timestamp, *, capture_timestamp=None):
        self.write_json(self.report_path, {
            "source": {"sha256": self.source_sha256},
            "gameplay_tracking": {"readings": [{
                "evidence": evidence,
                "source_timestamp_ms": timestamp,
            }]},
            "frames": [],
        })
        if capture_timestamp is not None:
            self.write_json(self.evidence / "capture.json", {
                "source": {"sha256": self.source_sha256},
                "frames": [{"evidence": evidence, "source_timestamp_ms": capture_timestamp}],
            })

    def audit(self):
        if Image is None:
            with patch.object(proof_audit, "_verify_image",
                              return_value={"ok": True, "format": "PNG", "size": [1, 1]}):
                return proof_audit.audit_reference_proofs(
                    self.reference_path, self.report_path, self.evidence)
        return proof_audit.audit_reference_proofs(
            self.reference_path, self.report_path, self.evidence)

    def test_explicit_report_mapping_and_pillow_verification_match(self):
        image = self.image()
        evidence = "dense/frame-000007.png"
        self.reference(evidence)
        self.report(evidence, 123)

        result = self.audit()

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["unmapped"], [])
        self.assertEqual(result["image_verification"]["files_valid"], 1)
        self.assertEqual(result["source_manifest_sha256"][str(image.resolve())],
                         hashlib.sha256(image.read_bytes()).hexdigest())
        self.assertIn(str(self.report_path), result["source_manifest_sha256"])
        self.assertEqual(result["reference_sha256"],
                         hashlib.sha256(self.reference_path.read_bytes()).hexdigest())
        self.assertEqual(result["report_sha256"],
                         hashlib.sha256(self.report_path.read_bytes()).hexdigest())

    def test_timestamp_outside_review_interval_is_a_mismatch(self):
        evidence = "dense/frame-000007.png"
        self.image(evidence)
        self.reference(evidence, start=100, end=150)
        self.report(evidence, 200)

        result = self.audit()

        self.assertEqual(result["matched"], [])
        reasons = {issue["reason"] for issue in result["mismatches"][0]["issues"]}
        self.assertIn("timestamp_outside_observation_interval", reasons)

    def test_mapped_timestamp_with_missing_image_is_not_a_match(self):
        evidence = "dense/missing.png"
        self.write_json(self.reference_path, {
            "schema_version": "final-reliability-source-reference-v1",
            "run": "fixture",
            "source_sha256": self.source_sha256,
            "cases": [{"case_id": "fixture-case", "turn_id": "turn-1",
                        "scope_ms": [0, 1000], "reference_complete": True,
                        "review_coverage": {"categories": ["effect"], "fields": ["speed"],
                                            "intervals_ms": [[0, 1000]]},
                        "observations": [{"id": "speed-effect", "category": "effect",
                                          "phase": "applied", "start_ms": 100, "end_ms": 150,
                                          "evidence": [evidence], "payload": {},
                                          "expected_turn_id": "turn-1", "status": "observed"}],
                        "unobservable_intervals": [], "notes": []}],
            "image_sha256": {evidence: "b" * 64},
        })
        self.report(evidence, 123)

        result = self.audit()

        self.assertEqual(result["matched"], [])
        self.assertIn("image_missing", {
            issue["reason"] for issue in result["mismatches"][0]["issues"]})

    def test_capture_metadata_maps_dense_frame_without_frame_index_guess(self):
        evidence = "dense/frame-000007.png"
        self.image(evidence)
        self.reference(evidence, start=100, end=150)
        # The report has no reading.  The only valid mapping is the explicit
        # capture.json evidence/timestamp pair.
        self.write_json(self.report_path, {
            "source": {"sha256": self.source_sha256},
            "gameplay_tracking": {"readings": []},
            "frames": [],
        })
        self.write_json(self.evidence / "capture.json", {
            "source": {"sha256": self.source_sha256},
            "frames": [{"evidence": evidence, "source_timestamp_ms": 123}],
        })

        result = self.audit()

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mapped_from"], ["capture.json:capture.json"])

    def test_similar_frame_name_without_explicit_metadata_stays_unmapped(self):
        evidence = "dense/frame-000007.png"
        self.image(evidence)
        self.reference(evidence)
        self.write_json(self.report_path, {
            "source": {"sha256": self.source_sha256},
            "gameplay_tracking": {"readings": [{
                "evidence": "part-000/frames/000007.jpg",
                "source_timestamp_ms": 123,
            }]},
            "frames": [],
        })

        result = self.audit()

        self.assertEqual(result["matched"], [])
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(len(result["unmapped"]), 1)
        self.assertEqual(result["unmapped"][0]["reason"],
                         "no_explicit_report_or_capture_timestamp")

    def test_report_and_capture_timestamp_disagreement_is_visible(self):
        evidence = "dense/frame-000007.png"
        self.image(evidence)
        self.reference(evidence)
        self.report(evidence, 123, capture_timestamp=124)

        result = self.audit()

        self.assertEqual(result["matched"], [])
        reasons = {issue["reason"] for issue in result["mismatches"][0]["issues"]}
        self.assertIn("report_capture_timestamp_conflict", reasons)

    def test_result_records_input_rewrite_during_audit(self):
        evidence = "dense/frame-000007.png"
        self.image(evidence)
        self.reference(evidence)
        self.report(evidence, 123)

        def mutate_report(_path):
            self.report_path.write_text('{"source":{"sha256":"changed"}}', encoding="utf-8")
            return {"ok": True, "format": "PNG", "size": [1, 1]}

        with patch.object(proof_audit, "_verify_image", side_effect=mutate_report):
            result = proof_audit.audit_reference_proofs(
                self.reference_path, self.report_path, self.evidence)

        self.assertTrue(any(item.get("reason") == "report_changed_during_audit"
                            for item in result["mismatches"]))
        self.assertNotEqual(result["input_hashes"]["report_before"],
                            result["input_hashes"]["report_after"])

    @unittest.skipUnless(Image is not None, "Pillow is optional; invalid-image check needs it")
    def test_invalid_image_and_declared_hash_are_reported(self):
        evidence = "dense/frame-000007.png"
        path = self.evidence / evidence
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a PNG")
        self.reference(evidence, image_hash="b" * 64)
        self.report(evidence, 123)

        result = self.audit()

        self.assertEqual(result["matched"], [])
        reasons = {issue["reason"] for issue in result["mismatches"][0]["issues"]}
        self.assertIn("PIL_INVALID_IMAGE", reasons)
        self.assertIn("image_hash_mismatch", reasons)


if __name__ == "__main__":
    unittest.main()
