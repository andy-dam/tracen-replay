"""Tests for source-driven bounded refinement orchestration."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_gameplay import workspace_temp
from tracen_replay.automatic_refinement import discover, run


class AutomaticRefinementTests(unittest.TestCase):
    def _report(self, *ids):
        return {
            "source": {"sha256": "a" * 64},
            "frames": [
                {"id": frame_id, "source_timestamp_ms": index * 250, "evidence": f"{frame_id}.png"}
                for index, frame_id in enumerate(ids)
            ],
        }

    def _raws(self, root: Path, ids):
        neural = root / "neural"
        neural.mkdir()
        gameplay = root / "gameplay"
        gameplay.mkdir()
        for index, frame_id in enumerate(ids):
            (neural / f"{frame_id}.json").write_text(
                json.dumps({
                    "marker": frame_id,
                    "lines": [{"text": frame_id}],
                    "source_timestamp_ms": index * 250,
                    "evidence": f"gameplay/{frame_id}.png",
                }), encoding="utf-8"
            )
            (gameplay / f"{frame_id}.png").write_bytes(b"gameplay")

    def test_discovery_uses_weak_source_candidates_and_marks_existing_sidecars(self):
        with workspace_temp() as root:
            report = self._report("panel", "resolved", "badge")
            self._raws(root, ("panel", "resolved", "badge"))
            for frame_id in ("panel", "resolved"):
                path = root / "neural" / f"{frame_id}.json"
                raw = json.loads(path.read_text(encoding="utf-8"))
                raw["current_grid"] = True
                path.write_text(json.dumps(raw), encoding="utf-8")
            (root / "status-badge-refinement").mkdir()
            (root / "status-badge-refinement/badge.json").write_text("{}", encoding="utf-8")

            def field(raw, reread, name):
                return {"field": name} if raw["marker"] == "panel" and name == "dance" else None

            with patch("tracen_replay.vision._performance_panel_identity", return_value={}), \
                    patch("tracen_replay.vision._performance_panel_field",
                          side_effect=lambda lines, field, label_y, regions=None:
                          {"status": "resolved_merged_panel_value"}
                          if lines and lines[0].get("text") == "resolved"
                          else {"status": "unresolved_low_confidence_merged_panel_value"}), \
                    patch("tracen_replay.performance_panel_refinement._field_from_reader", side_effect=field), \
                    patch("tracen_replay.status_badge_refinement._candidate_records",
                          side_effect=lambda raw: [{"kind": "mood_status"}] if raw["marker"] == "badge" else []):
                result = discover(report, root)

            self.assertEqual([item["frame_id"] for item in result["panel"]], ["panel"])
            self.assertEqual([item["frame_id"] for item in result["status"]], ["badge"])
            self.assertEqual(result["status"][0]["status"], "existing")
            self.assertFalse(result["missing"])
            self.assertFalse(result["malformed"])

    def test_reparse_mode_reports_candidates_without_constructing_reader_or_running_ocr(self):
        with workspace_temp() as root:
            report = self._report("panel", "badge")
            discovered = {
                "panel": [{"frame_id": "panel", "source_timestamp_ms": 0,
                           "fields": ["dance"], "status": "candidate", "sidecar": "x"}],
                "status": [{"frame_id": "badge", "source_timestamp_ms": 250,
                            "kinds": ["mood_status"], "status": "candidate", "sidecar": "y"}],
                "missing": [], "malformed": [],
            }
            with patch("tracen_replay.automatic_refinement.discover", return_value=discovered), \
                    patch("tracen_replay.vision.NeuralReader", side_effect=AssertionError("OCR")), \
                    patch("tracen_replay.performance_panel_refinement.generate", side_effect=AssertionError("OCR")), \
                    patch("tracen_replay.status_badge_refinement.generate", side_effect=AssertionError("OCR")):
                result = run(report, root, allow_ocr=False)

            self.assertEqual(result["status"], "passed_with_unresolved")
            self.assertEqual(result["generated"]["performance_panel"]["written"], 0)
            self.assertEqual(result["generated"]["status_badge"]["written"], 0)
            self.assertEqual(
                {item["reason"] for item in result["unresolved"]},
                {"ocr_disabled_existing_sidecar_required"},
            )

    def test_fresh_run_groups_panel_fields_and_reuses_one_reader(self):
        with workspace_temp() as root:
            report = self._report("one", "two", "badge")
            discovered = {
                "panel": [
                    {"frame_id": "one", "source_timestamp_ms": 0,
                     "fields": ["dance"], "status": "candidate", "sidecar": "one"},
                    {"frame_id": "two", "source_timestamp_ms": 250,
                     "fields": ["passion"], "status": "candidate", "sidecar": "two"},
                ],
                "status": [{"frame_id": "badge", "source_timestamp_ms": 500,
                            "kinds": ["mood_status"], "status": "candidate", "sidecar": "badge"}],
                "missing": [], "malformed": [],
            }
            seen = []

            def panel_generate(*args, **kwargs):
                seen.append(("panel", kwargs["reader"], kwargs["frame_ids"], kwargs["fields"]))
                return {"written": 1, "unresolved_fields": []}

            def status_generate(*args, **kwargs):
                seen.append(("status", kwargs["reader"], kwargs["frame_ids"]))
                return {"written": 1, "unresolved": 0, "unresolved_frames": []}

            reader = object()
            with patch("tracen_replay.automatic_refinement.discover", return_value=discovered), \
                    patch("tracen_replay.performance_panel_refinement.generate", side_effect=panel_generate), \
                    patch("tracen_replay.status_badge_refinement.generate", side_effect=status_generate):
                result = run(report, root, allow_ocr=True, reader=reader)

            self.assertEqual([entry[0] for entry in seen], ["panel", "panel", "status"])
            self.assertTrue(all(entry[1] is reader for entry in seen))
            self.assertEqual(result["generated"]["performance_panel"]["written"], 2)
            self.assertEqual(result["generated"]["status_badge"]["written"], 1)
            self.assertFalse(result["unresolved"])

    def test_budget_exclusion_is_structured_and_deterministic(self):
        with workspace_temp() as root:
            report = self._report("first", "second")
            discovered = {
                "panel": [
                    {"frame_id": "first", "source_timestamp_ms": 0,
                     "fields": ["dance"], "status": "candidate", "sidecar": "first"},
                    {"frame_id": "second", "source_timestamp_ms": 250,
                     "fields": ["dance"], "status": "candidate", "sidecar": "second"},
                ],
                "status": [], "missing": [], "malformed": [],
            }
            with patch("tracen_replay.automatic_refinement.discover", return_value=discovered), \
                    patch("tracen_replay.performance_panel_refinement.generate", return_value={"written": 0, "unresolved_fields": []}):
                result = run(report, root, allow_ocr=False, max_panel_frames=1)

            self.assertEqual(result["budget"]["panel_budget_excluded"], 1)
            self.assertEqual(result["selected"]["panel_frames"], ["first"])
            excluded = [item for item in result["unresolved"] if "budget_exhausted" in item["reason"]]
            self.assertEqual([item["frame_id"] for item in excluded], ["second"])

    def test_generators_find_source_frame_evidence_in_fresh_capture_envelope(self):
        from tracen_replay.performance_panel_refinement import _selected_frames as panel_frames
        from tracen_replay.status_badge_refinement import _selected_frames as status_frames

        with workspace_temp() as root:
            self._raws(root, ("frame",))
            (root / "neural/frame.json").write_text(
                json.dumps({"source_timestamp_ms": 100}), encoding="utf-8"
            )
            (root / "capture.json").write_text(json.dumps({
                "frames": [{"id": "frame", "source_timestamp_ms": 100,
                            "evidence": "gameplay/frame.png"}]
            }), encoding="utf-8")
            performance = panel_frames(root, 0, 200, ["frame"])[0]
            status = status_frames(root, 0, 200, ["frame"])[0]
            self.assertEqual(performance[3], "gameplay/frame.png")
            self.assertEqual(status["source_frame_evidence"], "gameplay/frame.png")

    def test_discovery_rejects_traversal_frame_ids_before_reading_paths(self):
        with workspace_temp() as root:
            report = {
                "frames": [{"id": "../outside", "source_timestamp_ms": 0,
                            "evidence": "outside.png"}]
            }
            result = discover(report, root)
            self.assertEqual(result["panel"], [])
            self.assertEqual(result["status"], [])
            self.assertEqual(result["malformed"][0]["reason"], "invalid_frame_identity")

    def test_stale_timestamp_and_outside_raw_evidence_are_unresolved(self):
        with workspace_temp() as root:
            report = self._report("stale", "escape")
            self._raws(root, ("stale", "escape"))
            stale_path = root / "neural" / "stale.json"
            stale = json.loads(stale_path.read_text(encoding="utf-8"))
            stale["source_timestamp_ms"] = 999
            stale_path.write_text(json.dumps(stale), encoding="utf-8")
            escape_path = root / "neural" / "escape.json"
            escape = json.loads(escape_path.read_text(encoding="utf-8"))
            escape["evidence"] = "../outside.png"
            escape_path.write_text(json.dumps(escape), encoding="utf-8")

            result = discover(report, root)

            self.assertEqual(
                {item["reason"] for item in result["malformed"]},
                {"raw_timestamp_mismatch", "raw_evidence_missing_or_outside_root"},
            )
            self.assertFalse(result["panel"])
            self.assertFalse(result["status"])


if __name__ == "__main__":
    unittest.main()
