from __future__ import annotations

import copy
import hashlib
import json
import shutil
import unittest
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from tracen_replay.full_recording import _manifest_group_rows
from tracen_replay.inspect_training import reparse_inspection
from tracen_replay.pipeline import PipelineError
from tracen_replay.training_badge_localization import (
    GAIN_BOXES,
    WIDE_GAIN_BOXES,
    build_sidecar,
    file_fingerprint,
    fingerprint,
    gameplay_fingerprint,
    localize_training_badges,
)
from tracen_replay.transactions import training_events


class DenseTrainingInspectionFixture:
    source_sha256 = "a" * 64

    def __init__(self):
        self.directory = Path.cwd() / (".tmp-training-inspection-sidecar-" + uuid.uuid4().hex)
        self.directory.mkdir()
        self.root = self.directory / "inspection"
        self.root.mkdir()
        self.evidence_rel = "training-inspection/100/frame-000001.png"
        self.raw_rel = "training-inspection/100/frame-000001.v2.json"
        self.frame_rel = "training-inspection/100/frames/000001.jpg"
        evidence = self.root / self.evidence_rel
        evidence.parent.mkdir(parents=True)
        frame = evidence.parent / "frames" / "000001.jpg"
        frame.parent.mkdir()

        pixels = np.zeros((1080, 810, 3), dtype=np.uint8)
        left, top, right, bottom = WIDE_GAIN_BOXES["speed"]
        x0 = int((left + right) / 2 - 26)
        x1 = x0 + 52
        y0 = top + 31
        y1 = y0 + 29
        pixels[y0:y1, x0 - 148 : x1 - 148] = (240, 100, 30)
        pane = Image.fromarray(pixels, mode="RGB")
        pane.save(evidence)
        frame.write_bytes(b"immutable-source-frame")
        frame_manifest = [
            {
                "id": "frame-000001",
                "source_timestamp_ms": 100,
                "clip_timestamp_ms": 100,
                "evidence": "frames/000001.jpg",
            }
        ]
        (evidence.parent / "frames.json").write_text(
            json.dumps(frame_manifest), encoding="utf-8"
        )
        self.raw = {
            "lines": [
                {"text": "Training", "confidence": 99.9, "box": [155, 0, 250, 29]},
                {"text": "Speed Lvl 1", "confidence": 99.9, "box": [220, 162, 400, 198]},
            ],
            "regions": {
                "header": {"text": "Training", "confidence": 99.9, "box": [155, 0, 250, 29]},
                "option": {"text": "Speed Lvl 1", "confidence": 99.9, "box": [220, 162, 400, 198]},
                "gain.speed": {"text": "+7", "confidence": 99.9, "box": list(GAIN_BOXES["speed"])},
            },
            "header": "Training",
            "result_grid": True,
            "current_grid": False,
            "engine_fingerprint": "engine-v1",
            "model_sha256": {"model.onnx": "b" * 64},
            "gameplay_sha256": gameplay_fingerprint(pane),
            "inspection": "training_result_only",
            "source_timestamp_ms": 100,
            "source_frame_sha256": file_fingerprint(frame),
            "source_frame_evidence": self.frame_rel,
            "source_sha256": self.source_sha256,
            "evidence": self.evidence_rel,
        }
        raw_path = self.root / self.raw_rel
        raw_path.write_text(json.dumps(self.raw), encoding="utf-8")
        # Dense inspections commonly carry a legacy totals refinement beside
        # the raw row.  Its hash binds the pre-localization raw object; the
        # loader must apply it before attaching the supplemental badge regions.
        (raw_path.with_suffix(".totals.json")).write_text(
            json.dumps(
                {
                    "raw_sha256": fingerprint(self.raw),
                    "evidence_sha256": file_fingerprint(evidence),
                    "regions": {},
                }
            ),
            encoding="utf-8",
        )
        localization = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            screen="training_result",
            source_metadata={
                "source_timestamp_ms": 100,
                "evidence": self.evidence_rel,
                "source_sha256": self.source_sha256,
                "source_frame_sha256": self.raw["source_frame_sha256"],
                "source_frame_evidence": self.frame_rel,
                "source_frame_id": "frame-000001",
            },
        )
        self.localization = localization
        self.sidecar = build_sidecar(
            self.raw,
            localization,
            evidence_path=evidence,
            source_frame_path=frame,
            source_frame_id="frame-000001",
            source_frame_evidence=self.frame_rel,
            source_sha256=self.source_sha256,
        )
        self.inspection = {
            "source_sha256": self.source_sha256,
            "readings": [
                {
                    "screen": "training_result",
                    "training_option": "speed",
                    "source_timestamp_ms": 100,
                    "evidence": self.evidence_rel,
                }
            ],
            "windows": [],
        }
        (self.root / "training-inspection.json").write_text(
            json.dumps(self.inspection), encoding="utf-8"
        )

    def write_registered_sidecar(self, *, name="one.json"):
        sidecar_path = self.root / "training-badge-localization" / name
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(
            json.dumps(self.sidecar, sort_keys=True), encoding="utf-8"
        )
        self.inspection["training_badge_localization_sidecars"] = [
            {
                "path": sidecar_path.relative_to(self.root).as_posix(),
                "sidecar_sha256": file_fingerprint(sidecar_path),
                "source_timestamp_ms": 100,
                "evidence": self.evidence_rel,
                "raw_path": self.raw_rel,
                "source_frame": self.frame_rel,
                "source_frame_id": "frame-000001",
            }
        ]
        return sidecar_path

    def close(self):
        shutil.rmtree(self.directory, ignore_errors=True)


class TrainingInspectionLocalizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = DenseTrainingInspectionFixture()

    def tearDown(self):
        self.fixture.close()

    def test_reparse_consumes_sidecar_by_exact_dense_reading_identity(self):
        self.fixture.write_registered_sidecar()
        rows = reparse_inspection(self.fixture.inspection, self.fixture.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["facts"]["training_gains"]["speed"], 7)
        self.assertEqual(
            rows[0]["facts"]["training_gain_crop_provenance"]["speed"]["canonical_amount"],
            7,
        )

    def test_reparse_rejects_duplicate_dense_identity(self):
        self.fixture.write_registered_sidecar()
        self.fixture.inspection["readings"].append(
            copy.deepcopy(self.fixture.inspection["readings"][0])
        )
        with self.assertRaises(PipelineError):
            reparse_inspection(self.fixture.inspection, self.fixture.root)

    def test_reparse_rejects_sidecar_source_frame_mismatch(self):
        self.fixture.write_registered_sidecar()
        path = self.fixture.root / "training-badge-localization/one.json"
        sidecar = json.loads(path.read_text(encoding="utf-8"))
        sidecar["metadata"]["source_frame_sha256"] = hashlib.sha256(b"different").hexdigest()
        path.write_text(json.dumps(sidecar, sort_keys=True), encoding="utf-8")
        self.fixture.inspection["training_badge_localization_sidecars"][0][
            "sidecar_sha256"
        ] = file_fingerprint(path)
        with self.assertRaises(PipelineError):
            reparse_inspection(self.fixture.inspection, self.fixture.root)

    def test_manifest_group_rows_reaches_training_events(self):
        self.fixture.write_registered_sidecar()
        manifest = self.fixture.root / "training-inspection.json"
        manifest.write_text(json.dumps(self.fixture.inspection, sort_keys=True), encoding="utf-8")
        rows, own_root = _manifest_group_rows(
            self.fixture.root,
            {
                "manifest": "training-inspection.json",
                "manifest_sha256": file_fingerprint(manifest),
                "folder": "",
                "own_root": "",
                "row_evidence_root": "root",
                "row_indices": [0],
            },
        )
        events = training_events(rows)
        self.assertEqual(own_root, self.fixture.root.resolve())
        self.assertEqual(events[0]["deltas"]["speed"], 7)


if __name__ == "__main__":
    unittest.main()
