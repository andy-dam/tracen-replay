"""Focused checks for the source-bound cached replay entry point."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import _apply_manifest_supplements


def _load_script():
    path = Path(__file__).parents[1] / "tools" / "replay_cached_recording.py"
    spec = importlib.util.spec_from_file_location("replay_cached_recording_under_test", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load replay script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReplayCachedRecordingTests(unittest.TestCase):
    def test_external_status_sidecar_is_applied_only_in_its_declared_namespace(self):
        with workspace_temp() as root:
            base_root = root / "initial-baseline"
            (base_root / "neural").mkdir(parents=True)
            (base_root / "gameplay").mkdir()
            raw_path = base_root / "neural/frame.json"
            raw_path.write_text(json.dumps({
                "source_timestamp_ms": 0,
                "evidence": "gameplay/frame.png",
                "lines": [],
            }), encoding="utf-8")
            evidence = base_root / "gameplay/frame.png"
            evidence.write_bytes(b"gameplay")
            sidecar_path = root / "status-badge-source/review/status-badge-refinement/frame.json"
            sidecar_path.parent.mkdir(parents=True)
            sidecar_path.write_text("{}", encoding="utf-8")
            capture = {"frames": [{"id": "frame", "source_timestamp_ms": 0,
                                   "evidence": "gameplay/frame.png"}]}
            row = {"source_timestamp_ms": 0, "evidence": "gameplay/frame.png",
                   "screen": "unknown", "stats": {"values": {}}, "facts": {},
                   "ocr": {"neural": []}}
            parsed = dict(row, facts={"status_badges": [{"kind": "mood_status"}]},
                          ocr={"neural": [{"text": "Good"}]},
                          status_badge_refinement={"source_frame_id": "frame"})
            normalized = {
                "source_sha256": "a" * 64,
                "base": {"folder": "initial-baseline"},
                "supplements": [{
                    "kind": "raw_sidecars", "name": "status_badge_refinement",
                    "folder": "status-badge-source/review/status-badge-refinement",
                    "entries": [{
                        "path": "status-badge-source/review/status-badge-refinement/frame.json",
                        "raw_path": "initial-baseline/neural/frame.json",
                        "evidence_path": "initial-baseline/gameplay/frame.png",
                        "source_timestamp_ms": 0,
                        "sidecar_sha256": "b" * 64,
                    }],
                }],
            }

            with patch("tracen_replay.status_badge_refinement.apply", return_value={
                "source_timestamp_ms": 0, "evidence": "gameplay/frame.png",
                "lines": [], "status_badge_refinement": {"source_frame_id": "frame"},
            }), patch("tracen_replay.full_recording.parse_receipt_pixels", return_value=parsed):
                rows, applied = _apply_manifest_supplements(
                    normalized, root, base_root, capture, [row]
                )

            self.assertEqual(rows[0]["facts"]["status_badges"][0]["kind"], "mood_status")
            self.assertEqual(rows[0]["status_badge_refinement"]["source_frame_id"], "frame")
            self.assertEqual(applied[0]["name"], "status_badge_refinement")

    def test_manifest_replay_uses_base_namespace_and_preassembly_sources(self):
        script = _load_script()
        with workspace_temp() as root:
            source = root / "source.mp4"
            source.write_bytes(b"source")
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            input_root = root / "input"
            input_root.mkdir()
            manifest = input_root / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            base_root = input_root / "initial-baseline"
            output = root / "replay"
            report = {
                "source": {"sha256": source_sha256},
                "frames": [{"id": "frame", "source_timestamp_ms": 0}],
            }
            rows = [{"source_timestamp_ms": 0, "evidence": "initial-baseline/gameplay/frame.png"}]
            bundle = {
                "report": report,
                "readings": rows,
                "base_root": base_root,
                "inspections": [],
                "recoveries": [],
                "supplements": [],
                "manifest": {"reference": {"accepted_report_sha256": "b" * 64}},
            }
            automatic = {
                "schema_version": "tracen-replay/automatic-refinement-v1",
                "status": "passed",
                "unresolved": [],
            }
            choice_source = {
                "schema_version": "tracen-replay/event-choice-source-adapter-v1",
                "observations": [],
                "committed_choices": [{"kind": "dialogue_choice", "selection_observed_ms": 0}],
            }
            candidate = {"candidate": True}

            with patch.object(script, "_implementation_hashes", return_value={"code.py": "c" * 64}), \
                    patch("tracen_replay.full_recording.load_replay_input_bundle", return_value=bundle), \
                    patch("tracen_replay.inspect_choices.load", return_value=(None, ["legacy-choice"])), \
                    patch("tracen_replay.race_reward_inspection.load", return_value=(None, [])), \
                    patch("tracen_replay.hint_card_cache.load", return_value=[]), \
                    patch("tracen_replay.automatic_refinement.run", return_value=automatic) as run_auto, \
                    patch("tracen_replay.full_recording.build_event_choice_observations", return_value=choice_source) as build_choices, \
                    patch("tracen_replay.full_recording.assemble", return_value=candidate) as assemble, \
                    patch("tracen_replay.report_contract.validate") as validate:
                script._replay_manifest(manifest, input_root, source, output)

            run_auto.assert_called_once_with(bundle["report"], base_root, allow_ocr=False)
            build_choices.assert_called_once_with(rows, input_root.resolve(), ["legacy-choice"])
            assemble.assert_called_once_with(
                report,
                rows,
                ["legacy-choice"],
                [],
                [],
                source_root=input_root.resolve(),
                committed_choices=choice_source["committed_choices"],
                event_choice_observations=choice_source,
            )
            validate.assert_called_once_with(
                candidate, require_gameplay=True, source_root=input_root.resolve()
            )
            self.assertEqual(json.loads((output / "candidate-report.json").read_text(encoding="utf-8")), candidate)
            audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
            self.assertFalse(audit["accepted_report_values_loaded"])
            self.assertFalse(audit["ocr_executed"])
            self.assertEqual(audit["automatic_refinement"], automatic)
            self.assertEqual(audit["choice_source"], choice_source)


if __name__ == "__main__":
    unittest.main()
