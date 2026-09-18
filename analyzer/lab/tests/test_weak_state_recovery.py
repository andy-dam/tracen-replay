"""Tests of ``tests.test_weak_state_recovery`` that need locally preserved evidence; they run only where it is."""
from __future__ import annotations
import copy
import hashlib
import json
import unittest
from pathlib import Path
from PIL import Image
from tests import localdata
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


class WeakStateRecoveryTests(unittest.TestCase):






    def test_actual_frozen_weak_cases_resolve_from_their_own_source(self):
        base = localdata.root("full_recording_archive")
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

        reader = NeuralReader(localdata.MODEL_DIR)
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
