import copy
import hashlib
import json
import unittest
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
    def accounting_report(self):
        from tests.test_turn_ledger import report as ledger_fixture, checkpoint
        from tracen_replay.turn_ledger import build as ledger
        from tracen_replay.causal_accounting import build as accounting
        report = valid_report()
        source = ledger_fixture()
        report['source']['duration_ms'] = report['clip']['duration_ms'] = source['source']['duration_ms']
        data = report['gameplay_tracking']
        data.update(source['gameplay_tracking'])
        data['checkpoints'] = [checkpoint('before',100,100),checkpoint('after',2200,105)]
        data['events'] = [dict(id='award',kind='outcome',first_seen_ms=500,last_seen_ms=500,
            evidence='500.png',effects=[dict(kind='stat_change',field='speed',amount=5)],
            field_evidence={'stat_change|speed|':['500.png']},deltas={'speed':5})]
        report['turn_ledger'] = ledger(report)
        report['causal_accounting'] = accounting(report)
        return report

    def test_accounting_projection_preserves_partial_and_historical_reports(self):
        report = self.accounting_report()
        original = copy.deepcopy(report)
        self.assertIs(validate(report,require_gameplay=True),report)
        self.assertEqual(report,original)
        for transition in report['causal_accounting']['turn_transitions']:
            for key in ('transition_kind','endpoint_availability','terminal_observation'):
                transition.pop(key)
        report['causal_accounting'].pop('terminal_observations')
        self.assertIs(validate(report,require_gameplay=True),report)

    def test_terminal_observation_values_and_completion_claims_are_source_checked(self):
        from tracen_replay.turn_ledger import build as ledger
        from tracen_replay.causal_accounting import build as accounting
        report = self.accounting_report()
        row = copy.deepcopy(report['gameplay_tracking']['readings'][-1])
        row.update(source_timestamp_ms=2500, evidence='terminal.png', screen='career_summary',
                   facts={'final_attributes': {'speed': 105}})
        report['gameplay_tracking']['readings'].append(row)
        report['turn_ledger'] = ledger(report)
        report['causal_accounting'] = accounting(report)
        self.assertIs(validate(report, require_gameplay=True), report)
        self.assertEqual(len(report['causal_accounting']['terminal_observations']), 1)
        for mutation in ('value', 'completion', 'timestamp', 'pointer'):
            with self.subTest(mutation=mutation):
                modified = copy.deepcopy(report)
                observation = modified['causal_accounting']['terminal_observations'][0]
                if mutation == 'value': observation['values']['speed'] = 999
                elif mutation == 'completion': observation['run_completion_verified'] = True
                elif mutation == 'timestamp': observation['observed_at_ms'] = 2600
                else: observation['value_refs']['speed'] = ['/gameplay_tracking/readings/999/facts/speed']
                with self.assertRaisesRegex(ReportContractError, 'causal_accounting.*does not match'):
                    validate(modified, require_gameplay=True)

    def test_accounting_cannot_relabel_or_duplicate_a_source_contribution(self):
        for mutation in ('basis','amount','duplicate','source','status','terminal'):
            with self.subTest(mutation=mutation):
                report = self.accounting_report()
                accounting = report['causal_accounting']
                contribution = accounting['contributions'][0]
                if mutation=='basis': contribution['basis'] = 'state_derived'
                elif mutation=='amount': contribution['amount'] = 50
                elif mutation=='duplicate': accounting['contributions'].append(copy.deepcopy(contribution))
                elif mutation=='source': contribution['source_ref'] = '/gameplay_tracking/events/999'
                elif mutation=='status': accounting['turn_transitions'][0]['fields'][0]['status'] = 'fully_verified'
                else: accounting['turn_transitions'][0]['endpoint_availability'][0]['next_turn'] = 'no_next_turn'
                with self.assertRaisesRegex(ReportContractError,'causal_accounting.*does not match'):
                    validate(report,require_gameplay=True)

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
            ), patch("tracen_replay.full_recording.decode_frames", return_value=[frame]), \
                    patch("tracen_replay.full_recording.game_area", return_value=None):
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

    def test_frame_clip_and_pts_timestamps_are_source_consistent(self):
        cases = {
            "clip_after_duration": lambda report: report["frames"][1].update(
                clip_timestamp_ms=report["clip"]["duration_ms"] + 1,
            ),
            "clip_source_mismatch": lambda report: report["frames"][1].update(
                clip_timestamp_ms=0,
            ),
            "source_pts_mismatch": lambda report: report["frames"][1].update(
                source_pts=16,
            ),
            "source_outside_clip": lambda report: (
                report["clip"].update(source_start_ms=1000, duration_ms=1000),
                report["frames"][0].update(source_timestamp_ms=0),
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                report = valid_report()
                mutate(report)
                with self.assertRaises(ReportContractError):
                    validate(report)

    def test_frame_clip_timestamps_must_be_strictly_increasing(self):
        report = valid_report()
        report["frames"][0].update(
            source_timestamp_ms=1, clip_timestamp_ms=1, source_pts=1, time_base="1/1000"
        )
        report["frames"][1].update(
            source_timestamp_ms=2, clip_timestamp_ms=0, source_pts=2, time_base="1/1000"
        )
        with self.assertRaisesRegex(ReportContractError, "clip_timestamp_ms.*strictly increasing"):
            validate(report)

    def test_frame_clip_timestamp_allows_decoder_rounding_at_nonzero_clip_start(self):
        report = valid_report()
        report["clip"].update(source_start_ms=500, duration_ms=1500)
        report["frames"][0].update(source_timestamp_ms=500, clip_timestamp_ms=0, source_pts=30)
        report["frames"][1].update(
            source_timestamp_ms=833,
            clip_timestamp_ms=333,
            source_pts=5,
            time_base="1/6",
        )
        self.assertIs(validate(report), report)

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

    def test_a_layout_is_optional_and_checked_when_present(self):
        report = valid_report()
        report["layout"] = dict(frame=[608, 1316], pane=[0, 0, 608, 1316], top=53, bottom=0, fitted=True)
        self.assertIs(validate(report), report)
        # A game cut from a wider video records where it was cut from.
        framed = copy.deepcopy(report)
        framed["layout"]["crop"] = [0, 0, 10, 10]
        self.assertIs(validate(framed), framed)
        for field, value, message in (
                ("pane", [0, 0, 700, 1316], "must lie inside the frame"),
                ("frame", [608], "must contain a width and a height"),
                ("top", -1, "must be at least 0"),
                ("fitted", "yes", "must be a boolean"),
                ("crop", [0, 0, 10], "must contain a left, a top, a width and a height"),
                ("crop", [0, 0, 0, 10], "must be at least 1"),
                ("crop", [0, 0, 99999, 10], "must lie inside the recording")):
            broken = copy.deepcopy(report)
            broken["layout"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ReportContractError, message):
                validate(broken)


if __name__ == "__main__":
    unittest.main()
