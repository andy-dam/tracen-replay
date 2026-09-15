"""Tests for bounded, source-bound weak-state crop recovery."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.weak_state_recovery import (
    SCHEMA,
    apply,
    candidate_requests,
    discover,
    load,
    recover,
    validate,
)


class _FakeReader:
    models = {"fake-rec.onnx": "f" * 64}
    fingerprint = "fake-engine"

    def recognize_crops(self, _pane, requests):
        result = []
        for request in requests:
            kind = request["kind"]
            if kind == "current_stat":
                value = "221"
            elif kind == "panel_component":
                value = "89" if request["pattern"] == "panel_current" else "+14"
            elif kind == "panel_anchor_cap":
                value = "/250"
            elif kind == "panel_anchor_points":
                value = "Points"
            elif kind == "training_result_banner":
                value = "SUCCESS!"
            else:
                value = "53"
            result.append([
                {"variant": "view-a", "text": value, "confidence": 99.0},
                {"variant": "view-b", "text": value, "confidence": 98.0},
            ])
        return result


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _raw(root, *, lines=None):
    gameplay = root / "gameplay.png"
    source = root / "source.jpg"
    Image.new("RGB", (810, 1080), (30, 40, 50)).save(gameplay)
    Image.new("RGB", (1920, 1080), (30, 40, 50)).save(source)
    return {
        "source_timestamp_ms": 42,
        "evidence": "gameplay.png",
        "source_frame_sha256": _sha(source),
        "gameplay_sha256": hashlib.sha256(Image.open(gameplay).convert("RGB").tobytes()).hexdigest(),
        "model_sha256": {"source.onnx": "a" * 64},
        "engine_fingerprint": "source-engine",
        "lines": [] if lines is None else lines,
        "regions": {
            "current.speed": {
                "text": "?", "confidence": 0, "box": [309, 721, 366, 747],
            },
        },
        "header": "Training",
        "current_grid": True,
        "result_grid": False,
    }


class WeakStateRecoveryTests(unittest.TestCase):
    def test_discovery_is_bounded_and_uses_source_geometry(self):
        with workspace_temp() as root:
            raw = _raw(root, lines=[
                {"text": "89+14", "confidence": 80, "box": [208, 294, 317, 333]},
                {"text": "Poin", "confidence": 84, "box": [178, 276, 225, 297]},
                {"text": "/250", "confidence": 85, "box": [197, 431, 246, 457]},
            ])
            requests = candidate_requests(raw)
            self.assertGreaterEqual(len(requests), 3)
            self.assertEqual(len(discover(raw, max_requests=1)["requests"]), 1)
            self.assertTrue(discover(raw, max_requests=1)["deferred"])
            for request in requests:
                left, top, right, bottom = request["box"]
                self.assertGreaterEqual(left, 148)
                self.assertLessEqual(right, 958)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(bottom, 1080)
                self.assertNotIn("expected", request)
                self.assertNotIn("amount", request)

    def test_fresh_recovery_and_cached_replay_preserve_raw_lines(self):
        with workspace_temp() as root:
            raw = _raw(root)
            original = copy.deepcopy(raw)
            evidence = root / raw["evidence"]
            source = root / "source.jpg"
            sidecar = recover(
                raw,
                evidence,
                reader=_FakeReader(),
                source_frame_path=source,
                source_frame_evidence="source.jpg",
                source_frame_id="frame-000042",
            )
            self.assertEqual(sidecar["schema_version"], SCHEMA)
            self.assertEqual(sidecar["source_frame_verification"]["status"], "verified")
            self.assertEqual(sidecar["regions"]["current.speed"]["parsed_value"], 221)
            validate(
                raw,
                sidecar,
                evidence_path=evidence,
                source_frame_path=source,
                source_frame_id="frame-000042",
                source_frame_evidence="source.jpg",
            )
            refined = apply(
                raw,
                sidecar,
                evidence_path=evidence,
                source_frame_path=source,
                source_frame_id="frame-000042",
                source_frame_evidence="source.jpg",
            )
            self.assertEqual(raw, original)
            self.assertEqual(refined["lines"], original["lines"])
            self.assertEqual(refined["regions"]["current.speed"]["text"], "221")
            path = root / "weak-state-recovery" / "frame.json"
            path.parent.mkdir()
            path.write_text(json.dumps(sidecar), encoding="utf-8")
            replayed = load(
                raw,
                path,
                evidence_path=evidence,
                source_frame_path=source,
                source_frame_id="frame-000042",
                source_frame_evidence="source.jpg",
            )
            self.assertEqual(replayed["regions"]["current.speed"]["text"], "221")

    def test_conflicting_weak_state_region_is_rejected(self):
        # A region attached by an earlier weak-state replay that disagrees
        # with this sidecar is an inconsistent input, not evidence.
        with workspace_temp() as root:
            raw = _raw(root)
            evidence = root / raw["evidence"]
            source = root / "source.jpg"
            sidecar = recover(raw, evidence, reader=_FakeReader(), source_frame_path=source)
            conflicting = copy.deepcopy(raw)
            conflicting["regions"]["current.speed"].update(text="222", source_request_id="stat-current:speed")
            with self.assertRaisesRegex(ValueError, "disagrees"):
                apply(
                    conflicting,
                    sidecar,
                    original=raw,
                    evidence_path=evidence,
                    source_frame_path=source,
                )

    def test_conflicting_foreign_region_keeps_the_stronger_read_and_records_it(self):
        # Another refinement stage read the same crop from one view.  OCR
        # passes are not bit-identical, so the two reads can differ; the job
        # keeps the read with the stronger evidence and records the conflict
        # instead of aborting.
        with workspace_temp() as root:
            raw = _raw(root)
            evidence = root / raw["evidence"]
            source = root / "source.jpg"
            sidecar = recover(raw, evidence, reader=_FakeReader(), source_frame_path=source)
            selected = next(o["selected"] for o in sidecar["observations"] if o["region"] == "current.speed")
            weaker = copy.deepcopy(raw)
            weaker["regions"]["current.speed"].update(text="222", confidence=40.0, role="panel_current_component")
            applied = apply(weaker, sidecar, original=raw, evidence_path=evidence, source_frame_path=source)
            self.assertEqual(applied["regions"]["current.speed"]["text"], selected["text"])
            conflicts = applied["weak_state_recovery"]["conflicting_source_regions"]
            self.assertEqual([(c["region"], c["existing_text"], c["kept"]) for c in conflicts], [("current.speed", "222", "sidecar")])
            stronger = copy.deepcopy(raw)
            stronger["regions"]["current.speed"].update(text="222", confidence=99.99, role="panel_current_component")
            applied = apply(stronger, sidecar, original=raw, evidence_path=evidence, source_frame_path=source)
            self.assertEqual(applied["regions"]["current.speed"]["text"], "222")
            self.assertEqual(applied["weak_state_recovery"]["conflicting_source_regions"][0]["kept"], "existing")
            self.assertIn("current.speed", applied["weak_state_recovery"]["retained_source_regions"])

    def test_cached_replay_rejects_ocr_mode(self):
        with workspace_temp() as root:
            raw = _raw(root)
            with self.assertRaisesRegex(ValueError, "does not run OCR"):
                load(raw, root / "missing.json", allow_ocr=True)

    def test_training_result_banner_uses_result_geometry_and_keeps_outcome_observational(self):
        with workspace_temp() as root:
            raw = _raw(root, lines=[
                {"text": "SUOCESS!", "confidence": 93.811,
                 "box": [370, 682, 723, 776]},
            ])
            raw["current_grid"] = False
            raw["result_grid"] = True
            requests = [item for item in candidate_requests(raw)
                        if item["kind"] == "training_result_banner"]
            self.assertEqual(len(requests), 1)
            request = requests[0]
            self.assertEqual(request["box"], [370, 682, 723, 776])
            self.assertEqual(request["channel"], "training_result")
            self.assertEqual(request["field"], "outcome")
            self.assertEqual(request["semantic_promotion"], "none")
            self.assertNotIn("recording_id", request)
            self.assertNotIn("timestamp", request)

            sidecar = recover(
                raw, root / raw["evidence"], reader=_FakeReader(),
                source_frame_path=root / "source.jpg",
            )
            self.assertEqual(
                sidecar["regions"]["weak_state_recovery.training_result_banner"]["parsed_value"],
                "SUCCESS",
            )
            refined = apply(
                raw, sidecar, evidence_path=root / raw["evidence"],
                source_frame_path=root / "source.jpg",
            )
            self.assertEqual(refined["lines"], raw["lines"])
            self.assertNotIn("training_outcome", refined)
            self.assertEqual(
                refined["weak_state_recovery"]["applied_regions"],
                ["weak_state_recovery.training_result_banner"],
            )

            preview = copy.deepcopy(raw)
            preview["current_grid"] = True
            preview["result_grid"] = False
            preview["lines"] = [{
                "text": "Failure", "confidence": 99.908,
                "box": [736, 769, 798, 794],
            }]
            self.assertFalse(any(
                item["kind"] == "training_result_banner"
                for item in candidate_requests(preview)
            ))

    def test_actual_frozen_weak_cases_resolve_from_their_own_source(self):
        base = Path(".local/full-recording")
        cases = {
            "v1-dance-merged": (
                base / "v1/neural/part-002-frame-000050.json",
                base / "v1/gameplay/part-002-frame-000050.png",
                base / "v1/part-002/frames/000050.jpg",
                {"performance_panel_current.dance": 89,
                 "performance_panel_projected.dance": 14},
            ),
            "v1-performance-opening": (
                base / "v1/neural/part-004-frame-000039.json",
                base / "v1/gameplay/part-004-frame-000039.png",
                base / "v1/part-004/frames/000039.jpg",
                {"weak_state_recovery.performance_panel_anchor.points": "Points",
                 "weak_state_recovery.performance_panel_anchor.cap.vocal": 250},
            ),
            "v1-performance-weak-geometry": (
                base / "v1/neural/part-011-frame-000328.json",
                base / "v1/gameplay/part-011-frame-000328.png",
                base / "v1/part-011/frames/000328.jpg",
                {"performance_panel_localized_current.passion": 111,
                 "weak_state_recovery.performance_panel_anchor.cap.visual": 400},
            ),
            "independent-before-performance": (
                base / "independent-01/neural/part-011-frame-000093.json",
                base / "independent-01/gameplay/part-011-frame-000093.png",
                base / "independent-01/part-011/frames/000093.jpg",
                {"performance_panel_localized_current.vocal": 53,
                 "performance_panel_localized_current.visual": 49,
                 "current.stamina": 623},
            ),
            "independent-speed-weak": (
                base / "independent-02/initial-baseline/neural/part-003-frame-000164.json",
                base / "independent-02/initial-baseline/gameplay/part-003-frame-000164.png",
                base / "independent-02/initial-baseline/part-003/frames/000164.jpg",
                 {"current.speed": 221},
            ),
            "independent-training-success": (
                base / "independent-01/neural/part-011-frame-000114.json",
                base / "independent-01/gameplay/part-011-frame-000114.png",
                base / "independent-01/part-011/frames/000114.jpg",
                {"weak_state_recovery.training_result_banner": "SUCCESS"},
            ),
        }
        if not all(path.is_file() for item in cases.values() for path in item[:3]):
            self.skipTest("frozen source cases are not present in this checkout")
        from tracen_replay.vision import NeuralReader

        reader = NeuralReader(".local/models/rapidocr")
        for name, (raw_path, evidence, source, expected) in cases.items():
            with self.subTest(case=name):
                raw = json.loads(raw_path.read_text(encoding="utf-8"))
                sidecar = recover(
                    raw,
                    evidence,
                    reader=reader,
                    source_frame_path=source,
                    source_frame_evidence=source.as_posix(),
                    source_frame_id=raw_path.stem,
                )
                self.assertEqual(sidecar["source_frame_verification"]["status"], "verified")
                self.assertEqual(
                    {key: sidecar["regions"][key]["parsed_value"] for key in expected},
                    expected,
                )

        preview_path = base / "independent-02/wit-training-inspection-v1/neural/part-005-frame-000239.json"
        if preview_path.is_file():
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(preview.get("header"), "Training")
            self.assertTrue(preview.get("current_grid"))
            self.assertFalse(preview.get("result_grid"))
            self.assertFalse(any(
                item["kind"] == "training_result_banner"
                for item in candidate_requests(preview)
            ))


if __name__ == "__main__":
    unittest.main()
