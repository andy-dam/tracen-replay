import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import capture
from tracen_replay.pipeline import PipelineError
from tracen_replay.report_contract import (
    FULL_RECORDING_SCHEMA,
    ReportContractError,
    validate,
)


def valid_report():
    return {
        "schema_version": FULL_RECORDING_SCHEMA,
        "source": {
            "name": "run.mp4",
            "sha256": "a" * 64,
            "size_bytes": 123,
            "duration_ms": 2000,
            "timeline_origin_seconds": 0.0,
            "width": 1920,
            "height": 1080,
            "codec": "h264",
        },
        "clip": {"source_start_ms": 0, "duration_ms": 2000},
        "sampling": {
            "requested_fps": 4,
            "frame_count": 2,
            "method": "minimum_interval_on_decoded_pts",
            "guarantees_all_events": False,
        },
        "frames": [
            {
                "id": "frame-000001",
                "source_timestamp_ms": 0,
                "clip_timestamp_ms": 0,
                "source_pts": 0,
                "time_base": "1/60",
                "evidence": "frames/000001.jpg",
                "screen_label": None,
                "confidence": None,
            },
            {
                "id": "frame-000002",
                "source_timestamp_ms": 250,
                "clip_timestamp_ms": 250,
                "source_pts": 15,
                "time_base": "1/60",
                "evidence": "frames/000002.jpg",
                "screen_label": None,
                "confidence": None,
            },
        ],
        "observations": [],
        "limitations": ["Sampling does not establish complete event recall."],
        "recognition": {"enabled": True, "model": None},
        "gameplay_tracking": {
            "method": "neural_gameplay_v1",
            "auxiliary_log_used": False,
            "input_region": [148, 0, 958, 1080],
            "readings": [{"screen": "unknown", "facts": {"value": None}}],
            "screens": [],
            "checkpoints": [],
            "events": [],
            "intervals": [],
            "training_previews": [],
            "dialogue_choices": [],
            "turn_action_receipts": [],
            "lesson_purchases": [],
            "skill_purchases": [],
            "skill_receipts": [],
            "concerts": [],
            "races": [],
            "song_acquisitions": [],
            "unparsed_receipt_candidates": [],
            "lesson_debit_observations": [],
            "performance_accounting": {},
            "fan_accounting": {},
            "owned_skill_inventory": {
                "complete": False,
                "observed_owned_cards": [],
                "summary_frames": [],
                "scope": "Visible cards only",
                "unresolved": ["Off-screen cards remain unknown."],
            },
        },
    }


class ReportContractTests(unittest.TestCase):
    def test_full_recording_capture_persists_its_schema(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            frame = {
                "id": "frame-000001",
                "source_timestamp_ms": 0,
                "clip_timestamp_ms": 0,
                "source_pts": 0,
                "time_base": "1/60",
                "evidence": "frames/000001.jpg",
                "screen_label": None,
                "confidence": None,
            }
            with patch(
                "tracen_replay.full_recording.probe",
                return_value=(
                    {},
                    {"width": 1920, "height": 1080, "codec_name": "h264"},
                    1.0,
                    0.0,
                ),
            ), patch("tracen_replay.full_recording.decode_frames", return_value=[frame]):
                report = capture(source, root / "output", 1)
            self.assertEqual(report["schema_version"], FULL_RECORDING_SCHEMA)
            saved = json.loads((root / "output" / "capture.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], FULL_RECORDING_SCHEMA)

    def test_capture_envelope_is_valid_without_analysis(self):
        report = valid_report()
        report.pop("gameplay_tracking")
        self.assertIs(validate(report), report)

    def test_analyzed_report_requires_gameplay_sections(self):
        report = valid_report()
        self.assertIs(validate(report, require_gameplay=True), report)
        report["gameplay_tracking"].pop("concerts")
        with self.assertRaisesRegex(ReportContractError, r"gameplay_tracking\.concerts: is required"):
            validate(report, require_gameplay=True)

    def test_missing_schema_version_is_rejected(self):
        report = valid_report()
        report.pop("schema_version")
        with self.assertRaisesRegex(ReportContractError, r"report\.schema_version: is required"):
            validate(report)

    def test_older_clip_schema_is_rejected(self):
        report = valid_report()
        report["schema_version"] = "tracen-replay/local-v0.1"
        with self.assertRaisesRegex(ReportContractError, "unsupported value"):
            validate(report)

    def test_historical_unversioned_capture_is_reused_without_rewriting(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            report = valid_report()
            report.pop("schema_version")
            report["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            output = root / "output"
            (output / "frames").mkdir(parents=True)
            (output / "frames" / "000001.jpg").write_bytes(b"frame")
            report["frames"] = [report["frames"][0]]
            report["sampling"]["frame_count"] = 1
            capture_path = output / "capture.json"
            capture_path.write_text(json.dumps(report), encoding="utf-8")
            original = capture_path.read_bytes()
            with patch(
                "tracen_replay.full_recording.probe",
                return_value=(
                    {},
                    {"width": 1920, "height": 1080, "codec_name": "h264"},
                    2.0,
                    0.0,
                ),
            ):
                converted = capture(source, output, 4)
            self.assertEqual(converted["schema_version"], FULL_RECORDING_SCHEMA)
            self.assertEqual(capture_path.read_bytes(), original)

    def test_malformed_legacy_capture_is_refused_after_conversion_validation(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            output.mkdir()
            (output / "capture.json").write_text(json.dumps({"source": {}}), encoding="utf-8")
            with patch(
                "tracen_replay.full_recording.probe",
                return_value=(
                    {},
                    {"width": 1920, "height": 1080, "codec_name": "h264"},
                    2.0,
                    0.0,
                ),
            ), self.assertRaisesRegex(PipelineError, r"report\.source\.name: is required"):
                capture(source, output, 4)

    def test_explicitly_wrong_cached_schema_is_never_relabelled(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            report = valid_report()
            report["schema_version"] = "tracen-replay/local-v0.1"
            report["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            output = root / "output"
            (output / "frames").mkdir(parents=True)
            (output / "frames" / "000001.jpg").write_bytes(b"frame")
            report["frames"] = [report["frames"][0]]
            report["sampling"]["frame_count"] = 1
            capture_path = output / "capture.json"
            capture_path.write_text(json.dumps(report), encoding="utf-8")
            original = capture_path.read_bytes()
            with patch(
                "tracen_replay.full_recording.probe",
                return_value=(
                    {},
                    {"width": 1920, "height": 1080, "codec_name": "h264"},
                    2.0,
                    0.0,
                ),
            ), self.assertRaisesRegex(PipelineError, "unsupported value"):
                capture(source, output, 4)
            self.assertEqual(capture_path.read_bytes(), original)

    def test_source_and_frame_provenance_fields_are_required(self):
        report = valid_report()
        report["source"].pop("sha256")
        with self.assertRaisesRegex(ReportContractError, r"report\.source\.sha256: is required"):
            validate(report)

        report = valid_report()
        report["frames"][1]["source_timestamp_ms"] = 0
        with self.assertRaisesRegex(ReportContractError, "strictly increasing"):
            validate(report)

    def test_unknown_semantic_values_are_preserved(self):
        report = valid_report()
        report["source"]["codec"] = None
        report["recognition"]["model"] = None
        report["gameplay_tracking"]["readings"][0]["facts"]["value"] = None
        self.assertIs(validate(report, require_gameplay=True), report)

    def test_numbers_are_json_numbers_and_fps_may_be_positive_fractional(self):
        report = valid_report()
        report["sampling"]["requested_fps"] = 0.5
        self.assertIs(validate(report), report)
        report["sampling"]["requested_fps"] = "0.5"
        with self.assertRaisesRegex(ReportContractError, "requested_fps: must be a finite number"):
            validate(report)
        report = valid_report()
        report["source"]["timeline_origin_seconds"] = True
        with self.assertRaisesRegex(ReportContractError, "timeline_origin_seconds: must be a finite number"):
            validate(report)

    def test_full_recording_capture_cli_keeps_one_to_eight_fps_bound(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            with patch(
                "tracen_replay.full_recording.probe",
                return_value=(
                    {},
                    {"width": 1920, "height": 1080, "codec_name": "h264"},
                    2.0,
                    0.0,
                ),
            ), self.assertRaisesRegex(PipelineError, "1 to 8 FPS"):
                capture(source, root / "output", 0.5)

    def test_auxiliary_log_reports_are_not_accepted_as_gameplay_only(self):
        report = copy.deepcopy(valid_report())
        report["gameplay_tracking"]["auxiliary_log_used"] = True
        with self.assertRaisesRegex(ReportContractError, "must be false"):
            validate(report, require_gameplay=True)

    def test_clip_cannot_extend_past_source(self):
        report = valid_report()
        report["clip"]["duration_ms"] = 2001
        with self.assertRaisesRegex(ReportContractError, "exceeds report.source.duration_ms"):
            validate(report)


if __name__ == "__main__":
    unittest.main()
