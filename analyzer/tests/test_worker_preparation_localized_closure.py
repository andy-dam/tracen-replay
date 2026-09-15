"""Closure tests for explicitly registered training badge sidecars."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from tools import prepare_final_worker_inputs as preparation
from tests.test_gameplay import workspace_temp


class WorkerPreparationLocalizedClosureTests(unittest.TestCase):
    def _fixture(self, root: Path, *, timestamp: int = 100) -> dict:
        window = root / "training-inspection" / "100"
        frames = window / "frames"
        frames.mkdir(parents=True)
        evidence = window / "frame-000001.png"
        raw = window / "frame-000001.v2.json"
        source_frame = frames / "000001.jpg"
        frame_manifest = window / "frames.json"
        sidecar = root / "training-badge-localization" / "badge.json"
        sidecar.parent.mkdir(parents=True)

        evidence.write_bytes(b"gameplay-pane")
        raw.write_text("{}\n", encoding="utf-8")
        source_frame.write_bytes(b"decoded-source-frame")
        frame_manifest.write_text(
            json.dumps(
                [
                    {
                        "id": "frame-000001",
                        "source_timestamp_ms": timestamp,
                        "evidence": "frames/000001.jpg",
                    }
                ]
            ),
            encoding="utf-8",
        )
        sidecar.write_bytes(b"localized-proof")
        entry = {
            "path": "training-badge-localization/badge.json",
            "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
            "source_timestamp_ms": timestamp,
            "evidence": "training-inspection/100/frame-000001.png",
            "raw_path": "training-inspection/100/frame-000001.v2.json",
            "source_frame": "training-inspection/100/frames/000001.jpg",
            "source_frame_id": "frame-000001",
        }
        (root / "training-inspection.json").write_text(
            json.dumps(
                {
                    "training_badge_localization_sidecars": [entry],
                }
            ),
            encoding="utf-8",
        )
        return {
            "base": {"capture": "capture.json", "frames": []},
            "inspections": [
                {
                    "manifest": "training-inspection.json",
                    "folder": "",
                    "rows": [
                        {
                            "source_timestamp_ms": timestamp,
                            "evidence": "training-inspection/100/frame-000001.png",
                            "raw": "training-inspection/100/frame-000001.v2.json",
                            "raw_evidence": "training-inspection/100/frame-000001.png",
                            "source_frame": "training-inspection/100/frames/000001.jpg",
                        }
                    ],
                }
            ],
            "recovery": [],
            "supplements": [],
        }

    def _source_fixture(self, root: Path) -> dict:
        normalized = self._fixture(root)
        manifest = root / "training-inspection.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        localized = payload.pop("training_badge_localization_sidecars")
        source_path = root / "training-gain-source-refinement" / "expanded.json"
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(b"expanded-source-proof")
        localized_entry = localized[0]
        payload["training_gain_source_refinement_sidecars"] = [
            {
                "path": "training-gain-source-refinement/expanded.json",
                "sidecar_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "source_timestamp_ms": localized_entry["source_timestamp_ms"],
                "evidence": localized_entry["evidence"],
                "raw_path": localized_entry["raw_path"],
                "source_frame": localized_entry["source_frame"],
                "source_frame_id": localized_entry["source_frame_id"],
            }
        ]
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return normalized

    def test_registered_sidecar_keeps_all_explicit_proof_paths(self):
        with workspace_temp() as root:
            normalized = self._fixture(root)

            selected, immutable, conventional = preparation._collect_closure(
                normalized, root
            )

            expected = {
                "training-badge-localization/badge.json",
                "training-inspection/100/frame-000001.png",
                "training-inspection/100/frame-000001.v2.json",
                "training-inspection/100/frames/000001.jpg",
                "training-inspection/100/frames.json",
            }
            self.assertTrue(expected.issubset(selected))
            self.assertIn("training-inspection/100/frames/000001.jpg", immutable)
            self.assertIn("training-inspection/100/frame-000001.png", immutable)
            self.assertEqual(
                conventional["inspection_localized_sidecars"],
                ["training-badge-localization/badge.json"],
            )

    def test_registration_must_bind_one_selected_row(self):
        with workspace_temp() as root:
            normalized = self._fixture(root, timestamp=101)
            manifest = root / "training-inspection.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["training_badge_localization_sidecars"][0]["source_timestamp_ms"] = 102
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(preparation.PreparationError) as caught:
                preparation._collect_closure(normalized, root)
            self.assertEqual(caught.exception.code, "inspection_localized_sidecar_unselected")

    def test_registration_hash_is_checked_before_closure(self):
        with workspace_temp() as root:
            normalized = self._fixture(root)
            manifest = root / "training-inspection.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["training_badge_localization_sidecars"][0]["sidecar_sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(preparation.PreparationError) as caught:
                preparation._collect_closure(normalized, root)
            self.assertEqual(
                caught.exception.code,
                "inspection_localized_sidecar_hash_mismatch",
            )

    def test_expanded_registration_keeps_all_explicit_proof_paths(self):
        with workspace_temp() as root:
            normalized = self._source_fixture(root)

            selected, immutable, conventional = preparation._collect_closure(
                normalized, root
            )

            expected = {
                "training-gain-source-refinement/expanded.json",
                "training-inspection/100/frame-000001.png",
                "training-inspection/100/frame-000001.v2.json",
                "training-inspection/100/frames/000001.jpg",
                "training-inspection/100/frames.json",
            }
            self.assertTrue(expected.issubset(selected))
            self.assertIn("training-inspection/100/frames/000001.jpg", immutable)
            self.assertIn("training-inspection/100/frame-000001.png", immutable)
            self.assertEqual(
                conventional["inspection_source_refinement_sidecars"],
                ["training-gain-source-refinement/expanded.json"],
            )

    def test_expanded_registration_rejects_proof_path_mismatch(self):
        with workspace_temp() as root:
            normalized = self._source_fixture(root)
            manifest = root / "training-inspection.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["training_gain_source_refinement_sidecars"][0]["raw_path"] = (
                "training-inspection/100/other.v2.json"
            )
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(preparation.PreparationError) as caught:
                preparation._collect_closure(normalized, root)
            self.assertEqual(
                caught.exception.code,
                "inspection_source_refinement_path_mismatch",
            )

    def test_expanded_registration_rejects_duplicate_selected_identity(self):
        with workspace_temp() as root:
            normalized = self._source_fixture(root)
            normalized["inspections"][0]["rows"].append(
                dict(normalized["inspections"][0]["rows"][0])
            )
            with self.assertRaises(preparation.PreparationError) as caught:
                preparation._collect_closure(normalized, root)
            self.assertEqual(
                caught.exception.code,
                "inspection_source_refinement_unselected",
            )


if __name__ == "__main__":
    unittest.main()
