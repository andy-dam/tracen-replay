"""Tests of ``tests.test_worker_sidecar_integration`` that need locally preserved evidence; they run only where it is."""
from __future__ import annotations
import shutil
import unittest
from pathlib import Path
from tests import localdata
from tests.test_gameplay import workspace_temp
from tracen_replay.automatic_refinement import discover, run
from tracen_replay.full_recording import cached_readings
from tests.test_worker_sidecar_integration import FRAME_ID, INDEPENDENT_ROOT, NUMERIC_SOURCE, TIMESTAMP, WEAK_SOURCE


@unittest.skipUnless(INDEPENDENT_ROOT.is_dir() and WEAK_SOURCE.exists() and NUMERIC_SOURCE.exists(),
                     "preserved independent-01 caches and sidecar sources are not present")
class WorkerSidecarIntegrationTests(unittest.TestCase):
    def _frame_root(self) -> tuple[Path, dict]:
        context = workspace_temp()
        root = context.__enter__()
        for relative in (
            f"neural/{FRAME_ID}.json",
            "gameplay/part-011-frame-000093.png",
            "part-011/frames/000093.jpg",
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(INDEPENDENT_ROOT / relative, target)
        for folder, source in (
            ("weak-state-recovery", WEAK_SOURCE),
            ("numeric-cap-refinement", NUMERIC_SOURCE),
        ):
            target = root / folder / f"{FRAME_ID}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        frame = {
            "id": FRAME_ID,
            "source_timestamp_ms": TIMESTAMP,
            "evidence": "part-011/frames/000093.jpg",
        }
        return root, {"context": context, "frame": frame}

    def test_independent_frame_93_composes_weak_state_and_numeric_caps(self):
        root, state = self._frame_root()
        try:
            raw_path = root / "neural" / f"{FRAME_ID}.json"
            original_bytes = raw_path.read_bytes()
            report = {
                "source": {"sha256": "24e000837fa4bba40d4e9e7c1c7d6121300a51d166424ea7bf5cfea24f521c18"},
                "frames": [state["frame"]],
            }
            rows = cached_readings(report, root)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["stats"]["values"]["stamina"], 623)
            self.assertEqual(row["facts"]["performance_points"]["vocal"], 53)
            self.assertEqual(row["facts"]["performance_points"]["visual"], 49)
            self.assertEqual(row["facts"]["performance_caps"], {
                "composure": 400,
                "dance": 400,
                "passion": 400,
                "visual": 400,
                "vocal": 400,
            })
            self.assertIn("weak_state_recovery", row)
            self.assertIn("numeric_cap_refinement", row)
            self.assertEqual(raw_path.read_bytes(), original_bytes)
        finally:
            state["context"].__exit__(None, None, None)

    def test_automatic_reparse_marks_existing_auxiliary_sidecars_without_ocr(self):
        root, state = self._frame_root()
        try:
            report = {
                "source": {"sha256": "24e000837fa4bba40d4e9e7c1c7d6121300a51d166424ea7bf5cfea24f521c18"},
                "frames": [state["frame"]],
            }
            found = discover(report, root)
            self.assertEqual(found["weak"][0]["status"], "existing")
            self.assertEqual(found["numeric"][0]["status"], "existing")
            audit = run(report, root, allow_ocr=False)
            self.assertEqual(audit["existing_sidecars"]["weak_state_recovery"], 1)
            self.assertEqual(audit["existing_sidecars"]["numeric_cap_refinement"], 1)
            self.assertEqual(audit["generated"]["weak_state_recovery"]["written"], 0)
            self.assertEqual(audit["generated"]["numeric_cap_refinement"]["written"], 0)
            self.assertFalse(audit["unresolved"])
        finally:
            state["context"].__exit__(None, None, None)
