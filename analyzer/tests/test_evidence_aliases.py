from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from contextlib import contextmanager
import shutil
import unittest
import uuid


from tests import localdata
from tracen_replay.evidence_aliases import resolve_evidence_aliases
from tracen_replay.observation_evaluate import evaluate


SOURCE_SHA = "a" * 64


@contextmanager
def workspace_temp():
    root = localdata.scratch(uuid.uuid4().hex)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _effect(*, evidence: list[str], amount: int = 21, field: str = "wit") -> dict:
    return {
        "id": "predicted-wit",
        "category": "effect",
        "phase": "applied",
        "start_ms": 550,
        "end_ms": 600,
        "evidence": evidence,
        "amount_evidence": list(evidence),
        "payload": {"kind": "stat_change", "field": field, "amount": amount},
    }


def _document(reference_path: str, digest: str, *, amount: int = 21, field: str = "wit") -> dict:
    return {
        "source_sha256": SOURCE_SHA,
        "image_sha256": {reference_path: digest},
        "cases": [
            {
                "observations": [
                    {
                        "id": "reference-wit",
                        "category": "effect",
                        "phase": "applied",
                        "start_ms": 400,
                        "end_ms": 500,
                        "evidence": [reference_path],
                        "payload": {
                            "kind": "stat_change",
                            "field": field,
                            "amount": amount,
                        },
                        "status": "observed",
                    }
                ]
            }
        ],
    }


def _report(reference_path: str, candidate_path: str, *, candidate_time: int = 500) -> dict:
    return {
        "source": {"sha256": SOURCE_SHA},
        "gameplay_tracking": {
            "readings": [
                {"source_timestamp_ms": 500, "evidence": reference_path},
                {"source_timestamp_ms": candidate_time, "evidence": candidate_path},
            ]
        },
    }


class EvidenceAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = workspace_temp()
        self.root = self.tempdir.__enter__().resolve()
        (self.root / "training-inspection").mkdir()
        (self.root / "gameplay").mkdir()
        self.reference = "training-inspection/frame-000016.png"
        self.candidate = "gameplay/part-004-frame-000207.png"
        content = b"verified source frame bytes"
        (self.root / self.reference).write_bytes(content)
        (self.root / self.candidate).write_bytes(content)
        self.digest = sha256(content).hexdigest()
        self.document = _document(self.reference, self.digest)
        self.report = _report(self.reference, self.candidate)

    def tearDown(self) -> None:
        self.tempdir.__exit__(None, None, None)

    def _prediction(self, effect: dict | None = None) -> dict:
        return {"source_sha256": SOURCE_SHA, "observations": [effect or _effect(evidence=[self.candidate])]}

    def _grade(self, prediction):
        reference = {
            "source_sha256": SOURCE_SHA, "scope_ms": [0, 1000],
            "reference_complete": False,
            "observations": self.document["cases"][0]["observations"],
        }
        prediction["auxiliary_log_used"] = False
        return evaluate(reference, prediction)["results"][0]

    def test_adds_only_proven_alias_to_a_copy(self) -> None:
        prediction = self._prediction()
        result, diagnostics = resolve_evidence_aliases(
            self.document, self.report, prediction, self.root
        )

        self.assertIsNot(result, prediction)
        self.assertEqual(prediction["observations"][0]["evidence"], [self.candidate])
        self.assertEqual(
            result["observations"][0]["evidence"], [self.candidate, self.reference]
        )
        self.assertEqual(
            result["observations"][0]["amount_evidence"], [self.candidate, self.reference]
        )
        self.assertEqual(diagnostics["alias_count"], 1)
        self.assertEqual(diagnostics["aliases"][0]["source_timestamp_ms"], 500)
        self.assertEqual(diagnostics["aliases"][0]["sha256"], self.digest)

    def test_requires_matching_field(self) -> None:
        effect = _effect(evidence=[self.candidate], field="speed")
        result, diagnostics = resolve_evidence_aliases(
            self.document, self.report, self._prediction(effect), self.root
        )
        self.assertEqual(result["observations"][0]["evidence"], [self.candidate])
        self.assertEqual(diagnostics["alias_count"], 0)

    def test_alias_does_not_hide_wrong_predicted_amount(self) -> None:
        effect = _effect(evidence=[self.candidate], amount=99)
        result, diagnostics = resolve_evidence_aliases(
            self.document, self.report, self._prediction(effect), self.root
        )
        self.assertEqual(
            result["observations"][0]["evidence"], [self.candidate, self.reference]
        )
        self.assertEqual(result["observations"][0]["payload"]["amount"], 99)
        self.assertEqual(diagnostics["aliases"][0]["reference_amount"], 21)
        self.assertEqual(diagnostics["aliases"][0]["candidate_amount"], 99)
        grade = self._grade(result)
        self.assertEqual(grade["matching_basis"], "source_identity")
        self.assertEqual(grade["status"], "incorrect")
        self.assertEqual(next(field["status"] for field in grade["fields"]
                              if field["field"] == "/amount"), "incorrect")

    def test_alias_preserves_unknown_predicted_amount(self) -> None:
        effect = _effect(evidence=[self.candidate])
        effect["payload"]["amount"] = None
        result, diagnostics = resolve_evidence_aliases(
            self.document, self.report, self._prediction(effect), self.root
        )
        self.assertEqual(
            result["observations"][0]["evidence"], [self.candidate, self.reference]
        )
        self.assertIsNone(diagnostics["aliases"][0]["candidate_amount"])
        grade = self._grade(result)
        self.assertEqual(grade["matching_basis"], "source_identity")
        self.assertEqual(grade["status"], "partial")
        self.assertEqual(next(field["status"] for field in grade["fields"]
                              if field["field"] == "/amount"), "missed")

    def test_amount_alias_requires_amount_specific_evidence_when_declared(self) -> None:
        effect = _effect(evidence=[self.candidate])
        effect["amount_evidence"] = ["gameplay/phase-only.png"]
        (self.root / "gameplay/phase-only.png").write_bytes(b"different phase bytes")
        result, diagnostics = resolve_evidence_aliases(
            self.document, self.report, self._prediction(effect), self.root
        )
        self.assertEqual(result["observations"][0]["evidence"], [self.candidate])
        self.assertEqual(diagnostics["alias_count"], 0)

    def test_ignores_unbound_event_provenance_timestamps(self) -> None:
        report = {"source": {"sha256": SOURCE_SHA}, "events": [{
            "source_timestamp_ms": 500,
            "evidence": [self.reference, self.candidate],
        }]}
        result, diagnostics = resolve_evidence_aliases(
            self.document, report, self._prediction(), self.root
        )
        self.assertEqual(result["observations"][0]["evidence"], [self.candidate])
        self.assertEqual(diagnostics["alias_count"], 0)

    def test_inspection_children_cannot_escape_foreign_source_parent(self) -> None:
        report = {"source": {"sha256": SOURCE_SHA}, "gameplay_tracking": {
            "readings": [{
                "source_sha256": "b" * 64,
                "source_timestamp_ms": 500,
                "evidence": "foreign.png",
                "inspection_merge_rejections": [{
                    "source_timestamp_ms": 500,
                    "evidence": [self.reference, self.candidate],
                }],
            }],
        }}
        result, diagnostics = resolve_evidence_aliases(
            self.document, report, self._prediction(), self.root)
        self.assertEqual(diagnostics["alias_count"], 0)
        self.assertEqual(result["observations"][0]["evidence"], [self.candidate])

    def test_rejects_wrong_bytes(self) -> None:
        (self.root / self.candidate).write_bytes(b"different frame bytes")
        result, diagnostics = resolve_evidence_aliases(
            self.document, self.report, self._prediction(), self.root
        )
        self.assertEqual(result["observations"][0]["evidence"], [self.candidate])
        self.assertEqual(diagnostics["alias_count"], 0)
        self.assertGreater(diagnostics["rejections"].get("candidate_bytes_mismatch", 0), 0)

    def test_rejects_same_bytes_at_a_different_source_time(self) -> None:
        report = _report(self.reference, self.candidate, candidate_time=501)
        result, diagnostics = resolve_evidence_aliases(
            self.document, report, self._prediction(), self.root
        )
        self.assertEqual(result["observations"][0]["evidence"], [self.candidate])
        self.assertEqual(diagnostics["alias_count"], 0)
        self.assertGreater(diagnostics["rejections"].get("source_timestamp_mismatch", 0), 0)

    def test_rejects_ambiguous_source_time(self) -> None:
        report = _report(self.reference, self.candidate)
        report["gameplay_tracking"]["readings"].append(
            {"source_timestamp_ms": 501, "evidence": self.candidate}
        )
        result, diagnostics = resolve_evidence_aliases(
            self.document, report, self._prediction(), self.root
        )
        self.assertEqual(result["observations"][0]["evidence"], [self.candidate])
        self.assertEqual(diagnostics["alias_count"], 0)
        self.assertGreater(
            diagnostics["rejections"].get("candidate_timestamp_missing_or_ambiguous", 0), 0
        )

    def test_rejects_escape_path_without_reading_outside_root(self) -> None:
        outside = self.root.parent / "outside-evidence.png"
        outside.write_bytes(b"verified source frame bytes")
        try:
            prediction = self._prediction(_effect(evidence=["../outside-evidence.png"]))
            result, diagnostics = resolve_evidence_aliases(
                self.document, self.report, prediction, self.root
            )
            self.assertEqual(result["observations"][0]["evidence"], ["../outside-evidence.png"])
            self.assertEqual(diagnostics["alias_count"], 0)
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_source_mismatch(self) -> None:
        report = _report(self.reference, self.candidate)
        report["source"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            resolve_evidence_aliases(self.document, report, self._prediction(), self.root)

    def test_deduplicates_repeated_candidate_evidence(self) -> None:
        prediction = self._prediction(_effect(evidence=[self.candidate, self.candidate]))
        result, diagnostics = resolve_evidence_aliases(
            self.document, self.report, prediction, self.root
        )
        self.assertEqual(diagnostics["alias_count"], 1)
        self.assertEqual(
            result["observations"][0]["evidence"], [self.candidate, self.candidate, self.reference]
        )


if __name__ == "__main__":
    unittest.main()
