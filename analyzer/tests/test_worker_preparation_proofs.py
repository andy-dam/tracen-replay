"""Preparation closure and source-bound Concert Info proof checks."""

from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests import localdata
from tools import prepare_final_worker_inputs as preparation
from tests.test_gameplay import workspace_temp


REPOSITORY_ROOT = Path(__file__).parents[2]
V1_CACHE = localdata.root("development_first_recording")
CONCERT_SIDECAR = V1_CACHE / "concert-panel-refinement/part-005-frame-000248.json"
CONCERT_MANIFEST = V1_CACHE / "part-005/frames.json"


class WorkerPreparationProofTests(unittest.TestCase):
    def test_gameplay_receipt_overlay_sidecars_follow_selected_images(self):
        with workspace_temp() as root:
            (root / "gameplay").mkdir()
            (root / "gameplay" / "base.overlay.json").write_text(
                '{"source_frame_sha256":"source"}\n', encoding="utf-8"
            )
            normalized = {
                "base": {
                    "capture": "capture.json",
                    "frames": [
                        {
                            "evidence": "gameplay/base.png",
                            "raw": "neural/base.json",
                            "raw_evidence": "gameplay/base.png",
                        }
                    ],
                },
                "inspections": [],
                "recovery": [],
                "supplements": [],
            }

            selected, immutable, conventional = preparation._collect_closure(
                normalized, root
            )

            self.assertIn("gameplay/base.overlay.json", selected)
            self.assertNotIn("gameplay/base.overlay.json", immutable)
            self.assertEqual(
                conventional["receipt_overlay_sidecars"],
                ["gameplay/base.overlay.json"],
            )
            self.assertIsNone(preparation._receipt_overlay_path("profile/base.png"))

    def test_inspection_receipt_overlay_sidecars_follow_selected_evidence(self):
        with workspace_temp() as root:
            evidence = root / "receipt-inspection" / "100" / "frame-000001.png"
            evidence.parent.mkdir(parents=True)
            evidence.write_bytes(b"selected-gameplay-crop")
            selected_overlay = evidence.with_suffix(".overlay.json")
            selected_overlay.write_text('{"lines": []}\n', encoding="utf-8")
            unselected_overlay = evidence.with_name("frame-000002.overlay.json")
            unselected_overlay.write_text('{"lines": []}\n', encoding="utf-8")
            normalized = {
                "base": {"capture": "capture.json", "frames": []},
                "inspections": [
                    {
                        "manifest": "receipt-inspection.json",
                        "folder": "",
                        "rows": [
                            {
                                "evidence": "receipt-inspection/100/frame-000001.png",
                                "raw": "receipt-inspection/100/frame-000001.v2.json",
                                "raw_evidence": "receipt-inspection/100/frame-000001.png",
                                "source_frame": "receipt-inspection/100/frames/000001.jpg",
                            }
                        ],
                    }
                ],
                "recovery": [],
                "supplements": [],
            }

            selected, immutable, conventional = preparation._collect_closure(
                normalized, root
            )

            self.assertIn(selected_overlay.relative_to(root).as_posix(), selected)
            self.assertNotIn(selected_overlay.relative_to(root).as_posix(), immutable)
            self.assertEqual(
                conventional["inspection_receipt_overlay_sidecars"],
                [selected_overlay.relative_to(root).as_posix()],
            )
            self.assertNotIn(unselected_overlay.relative_to(root).as_posix(), selected)

    def test_inspection_refinement_companions_follow_selected_raw_paths(self):
        with workspace_temp() as root:
            raw = root / "training-inspection" / "100" / "frame-000001.v2.json"
            raw.parent.mkdir(parents=True)
            for suffix in preparation.INSPECTION_REFINEMENT_SUFFIXES:
                raw.with_suffix(f".{suffix}.json").write_text(
                    json.dumps({"raw_sha256": "raw", "evidence_sha256": "image"}),
                    encoding="utf-8",
                )
            # An unselected raw record's companion must not enter the closure.
            raw.with_name("frame-000002.v2.performance.json").write_text(
                '{"raw_sha256":"other"}', encoding="utf-8"
            )
            normalized = {
                "base": {"capture": "capture.json", "frames": []},
                "inspections": [
                    {
                        "manifest": "training-inspection.json",
                        "folder": "",
                        "rows": [
                            {
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

            selected, immutable, conventional = preparation._collect_closure(
                normalized, root
            )

            expected = {
                f"training-inspection/100/frame-000001.v2.{suffix}.json"
                for suffix in preparation.INSPECTION_REFINEMENT_SUFFIXES
            }
            self.assertTrue(expected.issubset(selected))
            self.assertFalse(expected & immutable)
            self.assertEqual(conventional["inspection_refinement_companions"], sorted(expected))
            self.assertNotIn(
                "training-inspection/100/frame-000002.v2.performance.json", selected
            )

    def test_inspection_refinement_repair_copies_only_companions_and_preserves_manifests(self):
        with workspace_temp() as root:
            source = root / "source"
            source.mkdir()
            worker_parent = root / "prepared"
            worker = worker_parent / "v1"
            worker.mkdir(parents=True)
            canonical = source / "canonical.json"
            canonical.write_text('{"canonical":true}\n', encoding="utf-8")
            worker_manifest = worker / "replay-input-manifest.json"
            worker_manifest.write_text('{"worker":true}\n', encoding="utf-8")
            previous_manifest = worker / "replay-input-manifest.previous-v1.json"
            previous_manifest.write_text('{"previous":true}\n', encoding="utf-8")
            raw = {
                "source_sha256": "source",
                "source_timestamp_ms": 100,
                "evidence": "training-inspection/100/frame-000001.png",
            }
            raw_path = source / "training-inspection" / "100" / "frame-000001.v2.json"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            evidence = source / raw["evidence"]
            evidence.write_bytes(b"gameplay-proof")
            companion = raw_path.with_suffix(".performance.json")
            companion.write_text(
                json.dumps(
                    {
                        "raw_sha256": preparation._canonical_json_fingerprint(raw),
                        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                        "model_sha256": {},
                        "regions": {},
                    }
                ),
                encoding="utf-8",
            )
            worker_raw = worker / "training-inspection" / "100" / raw_path.name
            worker_raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_path, worker_raw)
            worker_evidence = worker / raw["evidence"]
            worker_evidence.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(evidence, worker_evidence)
            row = {
                "evidence": raw["evidence"],
                "raw": "training-inspection/100/frame-000001.v2.json",
                "raw_evidence": raw["evidence"],
                "source_frame": "training-inspection/100/frames/000001.jpg",
                "source_timestamp_ms": 100,
            }
            normalized = {
                "base": {"folder": ""},
                "inspections": [{"rows": [row]}],
                "recovery": [],
            }
            before_manifests = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (worker_manifest, previous_manifest)
            }
            details = {
                "cache_root": source,
                "manifest": canonical,
                "source_sha256": "source",
                "base_folder": "",
            }
            with patch.dict(preparation.RECORDINGS, {"v1": details}, clear=True), patch.object(
                preparation,
                "verified_source_freeze",
                return_value={"g2_freeze_sha256": "freeze"},
            ), patch.object(
                preparation, "load_manifest", return_value=normalized
            ), patch.object(
                preparation,
                "_validate_inspection_refinement_with_loader",
                return_value={"status": "passed"},
            ):
                report = preparation.repair_inspection_refinement_companions(
                    worker_parent, ["v1"]
                )

            target = worker / "training-inspection" / "100" / "frame-000001.v2.performance.json"
            self.assertEqual(target.read_bytes(), companion.read_bytes())
            self.assertEqual(
                {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (worker_manifest, previous_manifest)
                },
                before_manifests,
            )
            self.assertEqual(report["recordings"][0]["staged_paths"], [
                "training-inspection/100/frame-000001.v2.performance.json"
            ])
            self.assertFalse(report["manifests_modified"])
            self.assertFalse(report["accepted_report_values_loaded"])

    def test_inspection_receipt_overlay_repair_uses_annotate_path_and_preserves_manifests(self):
        with workspace_temp() as root:
            source = root / "source"
            source.mkdir()
            worker_parent = root / "prepared"
            worker = worker_parent / "v1"
            worker.mkdir(parents=True)
            canonical = source / "canonical.json"
            canonical.write_text('{"canonical":true}\n', encoding="utf-8")
            worker_manifest = worker / "replay-input-manifest.json"
            worker_manifest.write_text('{"worker":true}\n', encoding="utf-8")
            previous_manifest = worker / "replay-input-manifest.previous-v1.json"
            previous_manifest.write_text('{"previous":true}\n', encoding="utf-8")
            raw = {
                "source_sha256": "source",
                "source_timestamp_ms": 100,
                "evidence": "receipt-inspection/100/frame-000001.png",
                "lines": [
                    {
                        "text": "Energy went down by 8.",
                        "confidence": 99.0,
                        "box": [314, 806, 560, 839],
                    }
                ],
            }
            raw_path = source / "receipt-inspection" / "100" / "frame-000001.v2.json"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            evidence = source / raw["evidence"]
            evidence.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (810, 1080), "white").save(evidence)
            with Image.open(evidence) as pane:
                raw["gameplay_sha256"] = hashlib.sha256(
                    pane.convert("RGB").tobytes()
                ).hexdigest()
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            overlay = evidence.with_suffix(".overlay.json")
            overlay.write_text(
                json.dumps(
                    {
                        "raw_sha256": preparation._canonical_json_fingerprint(raw),
                        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                        "model_sha256": {},
                        "lines": [],
                    }
                ),
                encoding="utf-8",
            )
            worker_raw = worker / raw_path.relative_to(source)
            worker_raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_path, worker_raw)
            worker_evidence = worker / raw["evidence"]
            worker_evidence.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(evidence, worker_evidence)
            row = {
                "evidence": raw["evidence"],
                "raw": "receipt-inspection/100/frame-000001.v2.json",
                "raw_evidence": raw["evidence"],
                "source_frame": "receipt-inspection/100/frames/000001.jpg",
                "source_timestamp_ms": 100,
            }
            normalized = {
                "base": {"folder": ""},
                "inspections": [{"rows": [row]}],
                "recovery": [],
            }
            before_manifests = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (worker_manifest, previous_manifest)
            }
            details = {
                "cache_root": source,
                "manifest": canonical,
                "source_sha256": "source",
                "base_folder": "",
            }
            with patch.dict(preparation.RECORDINGS, {"v1": details}, clear=True), patch.object(
                preparation,
                "verified_source_freeze",
                return_value={"g2_freeze_sha256": "freeze"},
            ), patch.object(preparation, "load_manifest", return_value=normalized):
                report = preparation.repair_inspection_receipt_overlays(
                    worker_parent, ["v1"]
                )

            target = worker / raw["evidence"]
            target = target.with_suffix(".overlay.json")
            self.assertEqual(target.read_bytes(), overlay.read_bytes())
            self.assertEqual(
                {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (worker_manifest, previous_manifest)
                },
                before_manifests,
            )
            item = report["recordings"][0]
            self.assertEqual(item["overlay_count"], 1)
            self.assertEqual(item["source_annotate_path_invoked_count"], 1)
            self.assertEqual(item["worker_annotate_path_invoked_count"], 1)
            self.assertEqual(item["source_annotate_path_provenance_bound_count"], 1)
            self.assertEqual(item["worker_annotate_path_provenance_bound_count"], 1)
            self.assertEqual(
                item["staged_paths"],
                ["receipt-inspection/100/frame-000001.overlay.json"],
            )
            self.assertFalse(report["manifests_modified"])
            self.assertFalse(report["accepted_report_values_loaded"])

    def test_concert_proof_paths_are_in_closure_with_only_source_frames_immutable(self):
        with workspace_temp() as root:
            sidecar_directory = root / "concert-panel-refinement"
            sidecar_directory.mkdir()
            sidecar = {
                "observations": [
                    {
                        "evidence": "gameplay/proof.png",
                        "source_frame_evidence": "part-005/frames/000001.jpg",
                        "source_manifest_evidence": "part-005/frames.json",
                        "panel_raw_evidence": "neural/panel.json",
                    }
                ]
            }
            (sidecar_directory / "panel.json").write_text(
                json.dumps(sidecar), encoding="utf-8"
            )
            normalized = {
                "base": {
                    "capture": "capture.json",
                    "frames": [
                        {
                            "evidence": "gameplay/base.png",
                            "raw": "neural/base.json",
                            "raw_evidence": "gameplay/base.png",
                        }
                    ],
                },
                "inspections": [],
                "recovery": [],
                "supplements": [],
            }

            selected, immutable, conventional = preparation._collect_closure(
                normalized, root
            )

            expected = {
                "concert-panel-refinement/panel.json",
                "gameplay/proof.png",
                "part-005/frames/000001.jpg",
                "part-005/frames.json",
                "neural/panel.json",
            }
            self.assertTrue(expected.issubset(selected))
            self.assertIn("part-005/frames/000001.jpg", immutable)
            self.assertNotIn("gameplay/proof.png", immutable)
            self.assertEqual(
                conventional["concert_panel_proof_paths"],
                sorted(
                    {
                        "gameplay/proof.png",
                        "neural/panel.json",
                        "part-005/frames.json",
                        "part-005/frames/000001.jpg",
                    }
                ),
            )

    def _materialize_concert_worker(self, root: Path, include_manifest: bool) -> None:
        sidecar = json.loads(CONCERT_SIDECAR.read_text(encoding="utf-8"))
        sidecar_target = root / "concert-panel-refinement" / CONCERT_SIDECAR.name
        sidecar_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CONCERT_SIDECAR, sidecar_target)
        (root / "neural").mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            V1_CACHE / "neural" / CONCERT_SIDECAR.name,
            root / "neural" / CONCERT_SIDECAR.name,
        )
        raw = json.loads(
            (V1_CACHE / "neural" / CONCERT_SIDECAR.name).read_text(encoding="utf-8")
        )
        raw_evidence = root / raw["evidence"]
        raw_evidence.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(V1_CACHE / raw["evidence"], raw_evidence)
        for observation in sidecar["observations"]:
            for field in ("evidence", "source_frame_evidence", "panel_raw_evidence"):
                source = V1_CACHE / observation[field]
                target = root / observation[field]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        if include_manifest:
            target = root / "part-005" / "frames.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CONCERT_MANIFEST, target)

    @unittest.skipUnless(CONCERT_SIDECAR.exists() and CONCERT_MANIFEST.exists(), "preserved v1 concert sidecar is not present")
    def test_concert_preflight_rejects_missing_nested_pts_manifest_then_passes(self):
        with workspace_temp() as root:
            self._materialize_concert_worker(root, include_manifest=False)
            with self.assertRaises(preparation.PreparationError) as caught:
                preparation._validate_concert_panel_sidecars(root)
            self.assertEqual(caught.exception.code, "concert_panel_validation_failed")
            self.assertIn("missing or escapes", str(caught.exception))

            self._materialize_concert_worker(root, include_manifest=True)
            result = preparation._validate_concert_panel_sidecars(root)
            self.assertEqual(result["checked"], 1)
            self.assertEqual(
                result["sidecars"],
                ["concert-panel-refinement/part-005-frame-000248.json"],
            )

    def test_repair_stages_proofs_without_editing_worker_manifest(self):
        with workspace_temp() as root:
            source = root / "source"
            source.mkdir()
            (source / "proof.json").write_text('{"proof": true}\n', encoding="utf-8")
            canonical = source / "canonical.json"
            canonical.write_text('{"canonical": true}\n', encoding="utf-8")
            worker = root / "prepared" / "v1"
            worker.mkdir(parents=True)
            worker_manifest = worker / "replay-input-manifest.json"
            worker_manifest.write_text('{"worker": true}\n', encoding="utf-8")
            before = hashlib.sha256(worker_manifest.read_bytes()).hexdigest()
            details = {
                "cache_root": source,
                "manifest": canonical,
                "source_sha256": "source-hash",
                "base_folder": "",
            }
            normalized = {"base": {"folder": ""}}
            with patch.dict(preparation.RECORDINGS, {"v1": details}, clear=True), patch.object(
                preparation,
                "verified_source_freeze",
                return_value={"g2_freeze_sha256": "freeze"},
            ), patch.object(
                preparation,
                "load_manifest",
                return_value=normalized,
            ), patch.object(
                preparation,
                "_concert_panel_proof_paths",
                return_value=[("proof.json", False)],
            ), patch.object(
                preparation,
                "_validate_concert_panel_sidecars",
                return_value={"checked": 0, "sidecars": []},
            ):
                report = preparation.repair_concert_panel_proof_closure(
                    root / "prepared", ["v1"]
                )

            self.assertEqual(report["recordings"][0]["staged_paths"], ["proof.json"])
            self.assertTrue((worker / "proof.json").is_file())
            self.assertEqual(
                hashlib.sha256(worker_manifest.read_bytes()).hexdigest(), before
            )
            self.assertFalse(report["manifests_modified"])
            self.assertTrue((root / "prepared" / "concert-panel-proof-repair-v1.json").is_file())


if __name__ == "__main__":
    unittest.main()
