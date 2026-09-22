"""Tests of ``tests.test_training_gain_source_inspection`` that need locally preserved evidence; they run only where it is."""
from __future__ import annotations
import hashlib
import json
import shutil
import unittest
from pathlib import Path
from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import _manifest_group_rows
from tracen_replay.inspect_training import reparse_inspection
from tracen_replay.pipeline import PipelineError
from tracen_replay.transactions import training_events
from tests.test_training_gain_source_inspection import BUNDLE_ROOT, SOURCE_REFINEMENT_KEY, SOURCE_ROOT, _available_source_cases


@unittest.skipUnless(
    _available_source_cases(),
    "source-bound expanded refinement fixtures are unavailable",
)
class TrainingGainSourceInspectionTests(unittest.TestCase):
    def _make_fixture(self, root: Path, window: str, sidecar_name: str) -> dict:
        source_window = SOURCE_ROOT / "training-inspection" / window
        evidence_rel = f"training-inspection/{window}/frame-000015.png"
        raw_rel = f"training-inspection/{window}/frame-000015.v2.json"
        frame_rel = f"training-inspection/{window}/frames/000015.jpg"
        frame_ids = range(14, 21)
        for frame_number in frame_ids:
            frame_name = f"frame-000{frame_number:03d}"
            for relative in (
                f"training-inspection/{window}/{frame_name}.png",
                f"training-inspection/{window}/{frame_name}.v2.json",
                f"training-inspection/{window}/frames/{frame_number:06d}.jpg",
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = SOURCE_ROOT / relative
                shutil.copy2(source, destination)
        frames_destination = root / f"training-inspection/{window}/frames.json"
        frames_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_ROOT / f"training-inspection/{window}/frames.json", frames_destination)

        sidecar_source = BUNDLE_ROOT / "training-gain-source-refinement" / sidecar_name
        sidecar_rel = f"training-gain-source-refinement/{sidecar_name}"
        sidecar_path = root / sidecar_rel
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sidecar_source, sidecar_path)

        raw = json.loads((root / raw_rel).read_text(encoding="utf-8"))
        entry = {
            "path": sidecar_rel,
            "sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
            "source_timestamp_ms": raw["source_timestamp_ms"],
            "evidence": evidence_rel,
            "raw_path": raw_rel,
            "source_frame": frame_rel,
            "source_frame_id": "frame-000015",
        }
        inspection = {
            "source_sha256": raw["source_sha256"],
            "readings": [],
            SOURCE_REFINEMENT_KEY: [entry],
            "windows": [],
        }
        for frame_number in frame_ids:
            frame_name = f"frame-000{frame_number:03d}"
            row_raw = json.loads(
                (
                    root
                    / f"training-inspection/{window}/{frame_name}.v2.json"
                ).read_text(encoding="utf-8")
            )
            inspection["readings"].append(
                {
                    "source_timestamp_ms": row_raw["source_timestamp_ms"],
                    "evidence": f"training-inspection/{window}/{frame_name}.png",
                    "screen": "training_result",
                    "completed_action": "training",
                    "training_option": "speed",
                }
            )
        return inspection

    def _write_manifest(self, root: Path, inspection: dict) -> Path:
        path = root / "training-inspection.json"
        path.write_text(json.dumps(inspection, sort_keys=True), encoding="utf-8")
        return path

    def _assert_report_path(self, root: Path, inspection: dict, expected: int):
        manifest = self._write_manifest(root, inspection)
        rows, own_root = _manifest_group_rows(
            root,
            {
                "manifest": "training-inspection.json",
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "folder": "",
                "own_root": "",
                "row_evidence_root": "root",
                "row_indices": list(range(len(inspection["readings"]))),
            },
        )
        self.assertEqual(Path(own_root).resolve(), root.resolve())
        self.assertEqual(len(rows), len(inspection["readings"]))
        candidate_count = 0
        for row in rows:
            for field in row.get("facts", {}).get("training_gain_crop_provenance", {}).values():
                for candidate in field.get("candidates", []):
                    if "source_frame_evidence" not in candidate:
                        continue
                    candidate_count += 1
                    path = root / candidate["source_frame_evidence"]
                    self.assertTrue(path.is_file(), str(path))
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                     candidate["source_frame_sha256"])
        self.assertGreater(candidate_count, 0)
        events = training_events(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "training")
        self.assertEqual(events[0]["deltas"].get("speed"), expected)
        proof = events[0]["direct_gain_provenance"]["speed"]
        self.assertEqual(proof["basis"], "source_clipped_training_gain")
        self.assertEqual(proof["value"], expected)
        self.assertEqual(proof["observation_count"], 1)
        resolution = proof["source_clipping_resolution"]
        self.assertEqual(
            resolution["basis"], "source_pixel_refined_training_gain_phase"
        )
        self.assertEqual(
            resolution["resolution_mode"], "source_refinement_inner_single_frame"
        )
        self.assertIn("speed", target_facts := next(
            row["facts"] for row in rows
            if row["source_timestamp_ms"] == inspection["readings"][1]["source_timestamp_ms"]
        )["training_gains"])

    def test_external_envelope_enters_normal_inspection_parse_for_speed_36(self):
        with workspace_temp() as root:
            inspection = self._make_fixture(root, "845250", "7dd06ed3b5d4cda64eee4e91.json")
            rows = reparse_inspection(inspection, root)
            target = next(row for row in rows if row["source_timestamp_ms"] == 845717)
            self.assertEqual(target["facts"]["training_gains"]["speed"], 36)
            self.assertEqual(
                target["facts"]["training_gain_crop_provenance"]["speed"][
                    "canonical_amount"
                ],
                36,
            )
            self._assert_report_path(root, inspection, 36)

    def test_external_envelope_enters_normal_inspection_parse_for_speed_32(self):
        with workspace_temp() as root:
            inspection = self._make_fixture(root, "1420750", "3f06f468c6fae013c00968e2.json")
            rows = reparse_inspection(inspection, root)
            target = next(row for row in rows if row["source_timestamp_ms"] == 1421217)
            self.assertEqual(target["facts"]["training_gains"]["speed"], 32)
            self.assertEqual(
                target["facts"]["training_gain_crop_provenance"]["speed"][
                    "canonical_amount"
                ],
                32,
            )
            self._assert_report_path(root, inspection, 32)

    def test_external_envelope_rejects_wrong_source_identity(self):
        with workspace_temp() as root:
            inspection = self._make_fixture(root, "845250", "7dd06ed3b5d4cda64eee4e91.json")
            inspection[SOURCE_REFINEMENT_KEY][0]["source_timestamp_ms"] += 1
            with self.assertRaises(PipelineError):
                reparse_inspection(inspection, root)

    def test_external_envelope_rejects_stale_gameplay_pixels(self):
        with workspace_temp() as root:
            inspection = self._make_fixture(root, "845250", "7dd06ed3b5d4cda64eee4e91.json")
            evidence = root / inspection["readings"][1]["evidence"]
            evidence.write_bytes(evidence.read_bytes() + b"stale")
            with self.assertRaises(PipelineError):
                reparse_inspection(inspection, root)

    def test_external_envelope_rejects_preview_even_with_rehashed_entry(self):
        with workspace_temp() as root:
            inspection = self._make_fixture(root, "845250", "7dd06ed3b5d4cda64eee4e91.json")
            sidecar_path = root / inspection[SOURCE_REFINEMENT_KEY][0]["path"]
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            sidecar["preview"] = True
            sidecar_path.write_text(json.dumps(sidecar, sort_keys=True), encoding="utf-8")
            inspection[SOURCE_REFINEMENT_KEY][0]["sidecar_sha256"] = hashlib.sha256(
                sidecar_path.read_bytes()
            ).hexdigest()
            with self.assertRaises(PipelineError):
                reparse_inspection(inspection, root)

    def test_external_envelope_rejects_raw_identity_replacement(self):
        with workspace_temp() as root:
            inspection = self._make_fixture(root, "845250", "7dd06ed3b5d4cda64eee4e91.json")
            raw_path = root / inspection["readings"][1]["evidence"]
            raw_path = raw_path.with_suffix(".v2.json")
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["header"] = "Training"
            raw["lines"] = list(raw["lines"]) + [
                {"text": "changed", "confidence": 1, "box": [0, 0, 1, 1]}
            ]
            raw_path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
            with self.assertRaises(PipelineError):
                reparse_inspection(inspection, root)
