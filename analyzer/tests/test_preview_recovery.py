"""Tests for source-bound fixed-row training-preview recovery."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.preview_recovery import (
    SCHEMA,
    apply,
    candidate_requests,
    load,
    recover,
    recover_in_memory,
    validated_recovery,
)
from tracen_replay.vision import parse


FIELDS = ("speed", "stamina", "power", "guts", "wit", "skill_points")
FIELD_BOXES = {
    "speed": [300, 695, 360, 721],
    "stamina": [392, 697, 461, 720],
    "power": [489, 697, 550, 720],
    "guts": [594, 698, 640, 719],
    "wit": [671, 696, 732, 720],
    "skill_points": [757, 695, 832, 720],
}


def _raw(*, header="Training", marker=False, failure=True, merged=True):
    lines = [
        {"text": field.replace("_points", " Pts").title(),
         "confidence": 100.0, "box": box}
        for field, box in FIELD_BOXES.items()
    ]
    lines[0]["text"] = "Speed"
    lines[1]["text"] = "Stamina"
    lines[2]["text"] = "Power"
    lines[3]["text"] = "Guts"
    lines[4]["text"] = "Wit"
    lines[5]["text"] = "Skill Pts"
    if header:
        lines.append({"text": header, "confidence": 99.0, "box": [151, 1, 228, 32]})
    lines.append({"text": "Speed Lvl 1", "confidence": 99.0,
                  "box": [229, 168, 331, 194]})
    if marker:
        lines.extend([
            {"text": "Concert", "confidence": 99.0, "box": [184, 576, 260, 603]},
            {"text": "Bonuses", "confidence": 99.0, "box": [182, 593, 260, 617]},
        ])
    if failure:
        lines.append({"text": "Failure", "confidence": 99.0,
                      "box": [307, 770, 369, 794]})
    if merged:
        lines.extend([
            {"text": "+5 +11", "confidence": 93.0, "box": [386, 668, 545, 705]},
            {"text": "+2 +4", "confidence": 93.0, "box": [290, 667, 346, 706]},
        ])
    return {
        "source_timestamp_ms": 42,
        "evidence": "gameplay.png",
        "source_frame_sha256": "a" * 64,
        "model_sha256": {"source.onnx": "b" * 64},
        "engine_fingerprint": "source-engine",
        "gameplay_sha256": None,
        "lines": lines,
        "regions": {},
        "header": header,
        "current_grid": True,
        "result_grid": False,
    }


class _FakeReader:
    models = {"recovery.onnx": "c" * 64}
    fingerprint = "recovery-engine"

    def __init__(self, values=None, modifiers=None, *, disagreement=False):
        self.values = values or {}
        self.modifiers = modifiers or {}
        self.disagreement = disagreement

    def recognize_crops(self, _pane, requests):
        result = []
        for request in requests:
            if request["kind"] == "preview_failure":
                value = "Failure" if request["id"] == "preview-failure:0" else ""
            elif request["role"] == "main":
                value = self.values.get(request["field"], "")
            else:
                value = self.modifiers.get(request["field"], "")
            if self.disagreement and request["id"] == "preview-main:speed":
                result.append([
                    {"variant": "raw", "text": "+4", "confidence": 99.0},
                    {"variant": "gray_autocontrast", "text": "+5", "confidence": 99.0},
                ])
            else:
                result.append([
                    {"variant": "raw", "text": value, "confidence": 99.0},
                    {"variant": "gray_autocontrast", "text": value, "confidence": 98.0},
                ])
        return result


class PreviewRecoveryTests(unittest.TestCase):
    def test_real_selected_frames_schedule_fixed_rows_and_keep_roles(self):
        root = Path(__file__).resolve().parents[2] / ".local/full-recording/independent-02/initial-baseline/neural"
        if not root.is_dir():
            self.skipTest("preserved independent-02 initial-baseline caches are not present")
        names = (
            "part-003-frame-000165", "part-003-frame-000170",
            "part-010-frame-000059", "part-010-frame-000064",
            "part-010-frame-000066", "part-010-frame-000392",
            "part-010-frame-000395",
        )
        for name in names:
            raw = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            requests = candidate_requests(raw)
            self.assertTrue(requests, name)
            self.assertTrue(any(item["role"] == "main" for item in requests))
            for request in requests:
                self.assertNotIn("expected", request)
                self.assertNotIn("amount", request)
                left, top, right, bottom = request["box"]
                self.assertGreaterEqual(left, 148)
                self.assertLessEqual(right, 958)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(bottom, 1080)
        marker_name = "part-010-frame-000064"
        marker_raw = json.loads((root / f"{marker_name}.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(item["role"] == "modifier" for item in candidate_requests(marker_raw)), 6)

    def test_transition_frame_requires_fixed_failure_and_preserves_main_effects(self):
        raw = _raw(header="", marker=False, failure=False)
        pane = Image.new("RGB", (810, 1080), (40, 50, 60))
        original = copy.deepcopy(raw)
        refined = recover_in_memory(
            raw, pane,
            reader=_FakeReader({"stamina": "+5", "power": "+13", "skill_points": "+5"}),
        )
        self.assertEqual(raw, original)
        recovery = refined["preview_recovery"]
        self.assertEqual(recovery["phase"]["status"], "resolved")
        self.assertEqual(
            {(item["field"], item["amount"]) for item in recovery["effects"]},
            {("stamina", 5), ("power", 13), ("skill_points", 5)},
        )
        self.assertFalse(recovery["modifier_effects"])
        self.assertEqual(refined["regions"]["preview.main.power"]["role"], "main")

    def test_cross_view_disagreement_rejects_amount(self):
        raw = _raw(header="Training", marker=False, failure=False)
        pane = Image.new("RGB", (810, 1080), (40, 50, 60))
        refined = recover_in_memory(
            raw, pane, reader=_FakeReader({"speed": "+4"}, disagreement=True),
        )
        recovery = refined["preview_recovery"]
        self.assertNotIn("preview.main.speed", recovery["regions"])
        self.assertNotIn(("speed", 4), {
            (item["field"], item["amount"]) for item in recovery["effects"]
        })
        speed = next(item for item in recovery["observations"]
                     if item["request_id"] == "preview-main:speed")
        self.assertEqual(speed["status"], "unresolved_conflicting_crop_reads")

    def test_main_and_concert_rows_are_separate_channels(self):
        raw = _raw(header="Training", marker=True, failure=False)
        pane = Image.new("RGB", (810, 1080), (40, 50, 60))
        refined = recover_in_memory(
            raw, pane,
            reader=_FakeReader({"speed": "+5"}, {"speed": "+2"}),
        )
        recovery = refined["preview_recovery"]
        self.assertEqual(recovery["effects"][0]["kind"], "stat_change")
        self.assertEqual(recovery["effects"][0]["amount"], 5)
        self.assertEqual(recovery["modifier_effects"][0]["kind"], "song_modifier_change")
        self.assertEqual(recovery["modifier_effects"][0]["amount"], 2)

    def test_normal_vision_parse_uses_attached_phase_and_keeps_recovery_facts(self):
        raw = _raw(header="", marker=False, failure=False)
        pane = Image.new("RGB", (810, 1080), (40, 50, 60))
        refined = recover_in_memory(
            raw, pane,
            reader=_FakeReader({"stamina": "+5"}),
        )
        parsed = parse(refined)
        self.assertEqual(parsed["screen"], "training_preview")
        self.assertEqual(parsed["facts"]["preview_recovery"]["effects"][0]["amount"], 5)

    def test_sidecar_round_trip_binds_raw_and_gameplay_evidence(self):
        with workspace_temp() as root:
            evidence = root / "gameplay.png"
            Image.new("RGB", (810, 1080), (40, 50, 60)).save(evidence)
            raw = _raw(header="", marker=False, failure=False)
            raw["gameplay_sha256"] = hashlib.sha256(
                Image.open(evidence).convert("RGB").tobytes()).hexdigest()
            sidecar = recover(
                raw, evidence, reader=_FakeReader({"speed": "+4"}),
                source_frame_id="frame-000042", source_frame_evidence="source.jpg",
            )
            self.assertEqual(sidecar["schema_version"], SCHEMA)
            self.assertEqual(sidecar["source_frame_verification"]["status"], "unavailable")
            refined = apply(raw, sidecar, evidence_path=evidence,
                            source_frame_id="frame-000042",
                            source_frame_evidence="source.jpg")
            self.assertEqual(refined["preview_recovery"]["schema_version"], SCHEMA)
            path = root / "preview-recovery.json"
            path.write_text(json.dumps(sidecar), encoding="utf-8")
            replayed = load(raw, path, evidence_path=evidence,
                            source_frame_id="frame-000042",
                            source_frame_evidence="source.jpg")
            self.assertEqual(replayed["preview_recovery"]["regions"],
                             refined["preview_recovery"]["regions"])
            with self.assertRaisesRegex(ValueError, "does not run OCR"):
                load(raw, path, allow_ocr=True)

    def test_validated_recovery_rebuilds_in_memory_attachment(self):
        raw = _raw(header="Training", marker=True, failure=False)
        pane = Image.new("RGB", (810, 1080), (40, 50, 60))
        refined = recover_in_memory(
            raw, pane,
            reader=_FakeReader({"speed": "+5"}, {"speed": "+2"}),
        )
        original = copy.deepcopy(refined)
        validated = validated_recovery(refined)
        self.assertIsNotNone(validated)
        self.assertEqual(validated, refined["preview_recovery"])
        self.assertEqual(refined, original)
        self.assertEqual(
            validated["phase"]["control_proof"][0]["text"], "Speed Lvl 1",
        )
        self.assertEqual(
            {item["normalized"] for item in validated["phase"]["modifier_marker_proof"]},
            {"concert", "bonuses"},
        )

    def test_validated_recovery_rejects_tampered_source_and_selected_values(self):
        raw = _raw(header="Training", marker=True, failure=False)
        pane = Image.new("RGB", (810, 1080), (40, 50, 60))
        refined = recover_in_memory(
            raw, pane,
            reader=_FakeReader({"speed": "+5"}, {"speed": "+2"}),
        )
        mutations = []
        altered = copy.deepcopy(refined)
        altered["preview_recovery"]["source_timestamp_ms"] += 1
        mutations.append(altered)
        altered = copy.deepcopy(refined)
        altered["preview_recovery"]["evidence"] = "other.png"
        mutations.append(altered)
        altered = copy.deepcopy(refined)
        altered["preview_recovery"]["raw_sha256"] = "0" * 64
        mutations.append(altered)
        altered = copy.deepcopy(refined)
        altered["preview_recovery"]["phase"]["basis"] = "forged"
        mutations.append(altered)
        altered = copy.deepcopy(refined)
        altered["regions"]["preview.main.speed"]["parsed_value"] = 99
        mutations.append(altered)
        for candidate in mutations:
            self.assertIsNone(validated_recovery(candidate))

        # Rehashing a changed source mapping cannot make the old request list
        # valid: removing the same-frame control changes the phase proof, and
        # removing the Concert marker removes the modifier request channel.
        altered = copy.deepcopy(refined)
        altered["lines"] = [line for line in altered["lines"] if line["text"] != "Speed Lvl 1"]
        altered["preview_recovery"]["raw_sha256"] = __import__(
            "tracen_replay.preview_recovery", fromlist=["fingerprint"],
        ).fingerprint({key: value for key, value in altered.items() if key != "preview_recovery"})
        self.assertIsNone(validated_recovery(altered))
        altered = copy.deepcopy(refined)
        altered["lines"] = [line for line in altered["lines"] if line["text"] != "Bonuses"]
        altered["preview_recovery"]["raw_sha256"] = __import__(
            "tracen_replay.preview_recovery", fromlist=["fingerprint"],
        ).fingerprint({key: value for key, value in altered.items() if key != "preview_recovery"})
        self.assertIsNone(validated_recovery(altered))

    def test_validated_recovery_accepts_verified_loaded_sidecar(self):
        with workspace_temp() as root:
            evidence = root / "gameplay.png"
            Image.new("RGB", (810, 1080), (40, 50, 60)).save(evidence)
            source = root / "source-frame.bin"
            source.write_bytes(b"immutable source frame bytes")
            raw = _raw(header="", marker=False, failure=False)
            raw["gameplay_sha256"] = hashlib.sha256(
                Image.open(evidence).convert("RGB").tobytes()).hexdigest()
            raw["source_frame_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            sidecar = recover(
                raw, evidence, reader=_FakeReader({"speed": "+4"}),
                source_frame_id="frame-000042", source_frame_path=source,
                source_frame_evidence="source-frame.bin",
            )
            self.assertEqual(sidecar["source_frame_verification"]["status"], "verified")
            path = root / "preview-recovery.json"
            path.write_text(json.dumps(sidecar), encoding="utf-8")
            replayed = load(
                raw, path, evidence_path=evidence, source_frame_path=source,
                source_frame_id="frame-000042", source_frame_evidence="source-frame.bin",
            )
            self.assertEqual(validated_recovery(replayed), replayed["preview_recovery"])

            forged = copy.deepcopy(replayed)
            forged["preview_recovery"]["source_frame_verification"]["sha256"] = "0" * 64
            self.assertIsNone(validated_recovery(forged))

    def test_validated_recovery_does_not_run_ocr(self):
        import tracen_replay.preview_recovery as module

        raw = _raw(header="", marker=False, failure=False)
        pane = Image.new("RGB", (810, 1080), (40, 50, 60))
        refined = recover_in_memory(
            raw, pane, reader=_FakeReader({"speed": "+4"}),
        )
        original = module._engine_read
        module._engine_read = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("validated recovery must not run OCR"),
        )
        try:
            self.assertIsNotNone(validated_recovery(refined))
        finally:
            module._engine_read = original


if __name__ == "__main__":
    unittest.main()
