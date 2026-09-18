"""Tests of ``tests.test_worker_preparation_proofs`` that need locally preserved evidence; they run only where it is."""
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
from tests.test_worker_preparation_proofs import CONCERT_MANIFEST, CONCERT_SIDECAR, V1_CACHE


class WorkerPreparationProofTests(unittest.TestCase):






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
