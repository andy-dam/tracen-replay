import copy
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import _fixed_supplement_folder, cached_readings
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.status_badge_refinement import (
    MIN_CONFIDENCE,
    SCHEMA,
    STAGE,
    VERSION,
    apply,
    generate,
    load,
    prepare,
)
from tracen_replay.status_badges import read_badges


class _Reader:
    models = {"refinement.onnx": "b" * 64}
    fingerprint = "c" * 64
    np = np
    TextRecInput = SimpleNamespace

    class _Engine:
        def text_rec(self, request):
            return SimpleNamespace(
                txts=["GREAT"] * len(request.img),
                scores=[0.99] * len(request.img),
            )

    engine = _Engine()


class _HypeReader(_Reader):
    class _Engine:
        def text_rec(self, request):
            return SimpleNamespace(
                txts=["Mild"] * len(request.img),
                scores=[0.99] * len(request.img),
            )

    engine = _Engine()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class StatusBadgeRefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.root = self.temp.__enter__()
        self.gameplay = self.root / "gameplay.png"
        self.source = self.root / "source.jpg"
        Image.new("RGB", (810, 1080), (245, 225, 240)).save(self.gameplay)
        Image.new("RGB", (1920, 1080), (20, 30, 40)).save(self.source)
        gameplay_hash = hashlib.sha256(Image.open(self.gameplay).convert("RGB").tobytes()).hexdigest()
        self.raw = {
            "source_timestamp_ms": 619000,
            "evidence": "gameplay.png",
            "source_frame_sha256": _sha(self.source),
            "gameplay_sha256": gameplay_hash,
            "model_sha256": {"source.onnx": "a" * 64},
            "engine_fingerprint": "d" * 64,
            "regions": {},
            "current_grid": False,
            "result_grid": False,
            "header": "Career",
            "lines": [
                {"text": "Energy", "confidence": 99.695, "box": [381, 119, 441, 154]},
                {"text": "GREAT", "confidence": 93.415, "box": [730, 120, 803, 149]},
            ],
        }

    def tearDown(self):
        self.temp.__exit__(None, None, None)

    def _artifact(self, prepared=None):
        with Image.open(self.gameplay) as image:
            pane = image.convert("RGB")
        prepared = prepare(self.raw, pane, _Reader()) if prepared is None else prepared
        return {
            "schema_version": SCHEMA,
            "version": VERSION,
            "stage": STAGE,
            "source_frame_id": "frame-000077",
            "source_timestamp_ms": self.raw["source_timestamp_ms"],
            "evidence": self.raw["evidence"],
            "evidence_sha256": _sha(self.gameplay),
            "source_frame_evidence": "source.jpg",
            "gameplay_sha256": self.raw["gameplay_sha256"],
            "source_frame_sha256": self.raw["source_frame_sha256"],
            "raw_sha256": fingerprint(self.raw),
            "source_model_sha256": self.raw["model_sha256"],
            "source_engine_fingerprint": self.raw["engine_fingerprint"],
            "refinement_model_sha256": _Reader.models,
            "refinement_engine_fingerprint": _Reader.fingerprint,
            "independent_observations": False,
            "crop_policy": {
                "padding": 4,
                "views": ["rgb", "grayscale", "blue"],
                "minimum_strong_views": 2,
                "minimum_confidence": 97.0,
            },
            "observations": prepared["observations"],
        }

    def test_prepare_reads_only_gameplay_badge_crop_and_requires_strong_consensus(self):
        with Image.open(self.gameplay) as image:
            prepared = prepare(self.raw, image.convert("RGB"), _Reader())
        self.assertEqual(prepared["candidate_count"], 1)
        self.assertEqual(prepared["accepted_count"], 1)
        observation = prepared["observations"][0]
        self.assertEqual(observation["kind"], "mood_status")
        self.assertEqual(observation["line_index"], 1)
        self.assertEqual(observation["accepted_confidence"], 99.0)
        self.assertEqual({view["mode"] for view in observation["views"]}, {"rgb", "grayscale", "blue"})
        self.assertTrue(all(view["text"] == "GREAT" for view in observation["views"]))

    def test_source_bound_apply_promotes_literal_without_injecting_a_value(self):
        artifact = self._artifact()
        original = copy.deepcopy(self.raw)
        refined = apply(self.raw, artifact, evidence_path=self.gameplay, original=self.raw)
        self.assertEqual(self.raw, original)
        self.assertEqual(refined["lines"][1]["text"], "GREAT")
        self.assertGreaterEqual(refined["lines"][1]["confidence"], MIN_CONFIDENCE)
        self.assertTrue(refined["lines"][1]["status_badge_refined"])
        self.assertEqual([(row["kind"], row["value"]) for row in read_badges(refined["lines"])],
                         [("mood_status", "great")])

    def test_same_helper_supports_hype_badges_with_their_header_anchor(self):
        raw = copy.deepcopy(self.raw)
        raw["lines"] = [
            {"text": "Hype Level", "confidence": 99.0, "box": [172, 159, 262, 183]},
            {"text": "Mild", "confidence": 93.0, "box": [181, 180, 254, 219]},
        ]
        with Image.open(self.gameplay) as image:
            prepared = prepare(raw, image.convert("RGB"), _HypeReader())
        self.assertEqual(prepared["accepted_count"], 1)
        artifact = self._artifact(prepared)
        artifact["raw_sha256"] = fingerprint(raw)
        artifact["source_timestamp_ms"] = raw["source_timestamp_ms"]
        artifact["gameplay_sha256"] = raw["gameplay_sha256"]
        artifact["source_frame_sha256"] = raw["source_frame_sha256"]
        artifact["source_model_sha256"] = raw["model_sha256"]
        artifact["source_engine_fingerprint"] = raw["engine_fingerprint"]
        refined = apply(raw, artifact, evidence_path=self.gameplay, original=raw)
        self.assertEqual([(row["kind"], row["value"]) for row in read_badges(refined["lines"])],
                         [("hype_status", "mild")])

    def test_very_low_source_confidence_is_not_promoted_by_an_expected_badge(self):
        raw = copy.deepcopy(self.raw)
        raw["lines"][1]["confidence"] = 79.9
        with Image.open(self.gameplay) as image:
            prepared = prepare(raw, image.convert("RGB"), _Reader())
        self.assertEqual(prepared["candidate_count"], 0)
        self.assertEqual(prepared["accepted_count"], 0)

    def test_text_disagreement_or_insufficient_strong_views_abstains(self):
        artifact = self._artifact()
        artifact["observations"][0]["views"][0]["text"] = "GOOD"
        with self.assertRaisesRegex(ValueError, "text disagrees"):
            apply(self.raw, artifact, evidence_path=self.gameplay, original=self.raw)

        artifact = self._artifact()
        for view in artifact["observations"][0]["views"]:
            view["confidence"] = 96.9
        with self.assertRaisesRegex(ValueError, "strong unchanged-text"):
            apply(self.raw, artifact, evidence_path=self.gameplay, original=self.raw)

    def test_changed_gameplay_pixels_and_source_line_are_rejected(self):
        artifact = self._artifact()
        changed = self.gameplay.with_name("changed.png")
        Image.new("RGB", (810, 1080), (1, 2, 3)).save(changed)
        with self.assertRaisesRegex(ValueError, "gameplay evidence changed"):
            apply(self.raw, artifact, evidence_path=changed, original=self.raw)

        artifact = self._artifact()
        changed_raw = copy.deepcopy(self.raw)
        changed_raw["lines"][1]["confidence"] = 92.0
        with self.assertRaisesRegex(ValueError, "already refined"):
            apply(changed_raw, artifact, evidence_path=self.gameplay, original=self.raw)

    def test_generate_writes_cache_and_load_replays_it(self):
        neural = self.root / "neural"
        neural.mkdir()
        raw_path = neural / "frame-000077.json"
        raw_path.write_text(json.dumps(self.raw), encoding="utf-8")
        report = {
            "frames": [{
                "id": "frame-000077",
                "source_timestamp_ms": self.raw["source_timestamp_ms"],
                "evidence": "source.jpg",
            }]
        }
        (self.root / "report.json").write_text(json.dumps(report), encoding="utf-8")
        output = self.root / "status-badge-refinement"
        summary = generate(self.root, start_ms=619000, end_ms=619250,
                           frame_ids=["frame-000077"], reader=_Reader(), output_dir=output)
        self.assertEqual(summary["written"], 1)
        path = output / raw_path.name
        self.assertTrue(path.is_file())
        refined = load(self.raw, path, evidence_path=self.gameplay, original=self.raw)
        self.assertEqual(refined["lines"][1]["confidence"], 99.0)

    def test_cached_readings_applies_status_badge_sidecar_before_parse(self):
        neural = self.root / "neural"
        sidecars = self.root / "status-badge-refinement"
        neural.mkdir()
        sidecars.mkdir()
        (neural / "frame-000077.json").write_text(json.dumps(self.raw), encoding="utf-8")
        (sidecars / "frame-000077.json").write_text(json.dumps(self._artifact()), encoding="utf-8")
        capture = {
            "source": {"sha256": hashlib.sha256(b"synthetic recording").hexdigest()},
            "frames": [{
                "id": "frame-000077",
                "source_timestamp_ms": self.raw["source_timestamp_ms"],
                "evidence": "source.jpg",
            }],
        }

        row = cached_readings(capture, self.root)[0]

        self.assertEqual(row["source_sha256"], capture["source"]["sha256"])
        self.assertEqual(row["facts"]["status_badges"][0]["value"], "great")
        self.assertEqual(row["ocr"]["neural"][1]["confidence"], 99.0)
        self.assertEqual(
            row["status_badge_refinement"]["applied_observations"][0]["kind"],
            "mood_status",
        )

    def test_manifest_fixed_sidecar_accepts_nested_and_worker_root_paths(self):
        self.assertTrue(_fixed_supplement_folder(
            "raw_sidecars", "status_badge_refinement",
            "initial-baseline/status-badge-refinement", "initial-baseline"
        ))
        self.assertTrue(_fixed_supplement_folder(
            "raw_sidecars", "status_badge_refinement",
            "status-badge-refinement", "initial-baseline"
        ))


if __name__ == "__main__":
    unittest.main()
