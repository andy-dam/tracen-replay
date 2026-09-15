"""Focused tests for the source-bound common replay loader helpers."""

from __future__ import annotations

import json
import hashlib
from copy import deepcopy
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import localdata
from tests.test_gameplay import workspace_temp
from tests.test_training_gain_candidate_recovery import _row
from tracen_replay.full_recording import (
    PipelineError,
    _manifest_group_payload,
    load_replay_input_bundle,
    _replay_recovery_rows,
)
from tracen_replay.transactions import training_events
from tracen_replay.inspect_training import reparse_inspection
from tracen_replay.vision import parse


def _phase_row(timestamp, gains, shape, *, option='wit'):
    """Build a source-shaped result row for replay phase integration tests."""

    first_field = next(iter(gains))
    row = _row(
        timestamp,
        f'{timestamp}.png',
        field=first_field,
        amount=gains[first_field],
        option=option,
    )
    provenance = {}
    for field, amount in gains.items():
        donor = _row(
            timestamp,
            f'{timestamp}-{field}.png',
            field=field,
            amount=amount,
            option=option,
        )
        provenance[field] = deepcopy(
            donor['facts']['training_gain_crop_provenance'][field]
        )
    row['facts']['training_gains'] = dict(gains)
    row['facts']['observed_training_gain_fields'] = list(shape)
    row['facts']['training_gain_crop_provenance'] = provenance
    return row


class ReplayLoaderTests(unittest.TestCase):
    def test_nested_base_capture_path_is_not_prefixed_twice(self):
        with workspace_temp() as root:
            capture_path = root / "initial-baseline/capture.json"
            capture_path.parent.mkdir(parents=True, exist_ok=True)
            capture_path.write_text("{}", encoding="utf-8")
            source_sha256 = "a" * 64
            normalized = {
                "source_sha256": source_sha256,
                "base": {
                    "folder": "initial-baseline",
                    "capture": "initial-baseline/capture.json",
                },
                "inspections": [],
                "recovery": [],
                "supplements": [],
            }
            capture = {"source": {"sha256": source_sha256}, "frames": []}
            with patch("tracen_replay.replay_inputs.load_manifest", return_value=normalized), \
                    patch("tracen_replay.full_recording._load_cached_capture", return_value=capture) as load_capture, \
                    patch("tracen_replay.full_recording.cached_readings", return_value=[]), \
                    patch("tracen_replay.full_recording._apply_manifest_supplements", return_value=([], [])):
                load_replay_input_bundle(root / "manifest.json", root)

            load_capture.assert_called_once_with(capture_path.resolve(), source_sha256, 4.0)

    def test_nested_own_root_rows_are_rebased_before_reparse(self):
        with workspace_temp() as root:
            proof = root / "recovery/receipt-inspection/window/proof.png"
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_bytes(b"proof")
            manifest = root / "recovery/receipt-inspection.json"
            manifest.write_text(
                json.dumps({"readings": [{"evidence": "recovery/receipt-inspection/window/proof.png"}]}),
                encoding="utf-8",
            )
            group = {
                "folder": "",
                "own_root": "recovery",
                "manifest": "recovery/receipt-inspection.json",
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "row_evidence_root": "root",
                "row_indices": [0],
            }

            payload, own_root = _manifest_group_payload(root, group)

            self.assertEqual(own_root, root / "recovery")
            self.assertEqual(payload["readings"][0]["evidence"], "receipt-inspection/window/proof.png")

            manifest.write_text(json.dumps({"readings": [{"evidence": "other.png"}]}), encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "manifest_sha256"):
                _manifest_group_payload(root, group)
            for escaped in ("../receipt-inspection.json", str(manifest.resolve())):
                with self.subTest(path=escaped), self.assertRaises(PipelineError):
                    _manifest_group_payload(root, dict(group, manifest=escaped))

    def test_recovery_source_metadata_mismatch_is_rejected(self):
        with workspace_temp() as root:
            recovery = root / "numeric"
            recovery.mkdir()
            (recovery / "last-plan.json").write_text(
                json.dumps({"source_sha256": "b" * 64}),
                encoding="utf-8",
            )
            group = {
                "kind": "numeric_receipt",
                "folder": "numeric",
                "manifest_sha256": "c" * 64,
            }

            with self.assertRaisesRegex(PipelineError, "another source"):
                _replay_recovery_rows([], [], group, root, "a" * 64)

    def test_recovery_manifest_metadata_mismatch_is_rejected(self):
        with workspace_temp() as root:
            recovery = root / "numeric"
            recovery.mkdir()
            (recovery / "last-plan.json").write_text(
                json.dumps({"source_sha256": "a" * 64, "manifest_sha256": "d" * 64}),
                encoding="utf-8",
            )
            group = {
                "kind": "numeric_receipt",
                "folder": "numeric",
                "manifest_sha256": "c" * 64,
            }

            with self.assertRaisesRegex(PipelineError, "stale"):
                _replay_recovery_rows([], [], group, root, "a" * 64)

    def test_recovery_metadata_is_used_only_to_bound_promotions(self):
        with workspace_temp() as root:
            recovery = root / "numeric"
            recovery.mkdir()
            (recovery / "last-plan.json").write_text(
                json.dumps({
                    "source_sha256": "a" * 64,
                    "manifest_sha256": "c" * 64,
                    "requested_windows": [{"start_ms": 10, "end_ms": 20}],
                }),
                encoding="utf-8",
            )
            group = {
                "kind": "numeric_receipt",
                "folder": "numeric",
                "manifest_sha256": "c" * 64,
            }
            with patch("tracen_replay.receipt_recovery.scoped_observations", return_value=[{"fresh": 1}]) as scoped, \
                    patch("tracen_replay.inspect_receipts.merge", return_value=[{"merged": 1}]) as merge:
                rows, metadata = _replay_recovery_rows(
                    [{"old": 1}], [{"fresh": 1}], group, root, "a" * 64
                )

            self.assertEqual(rows, [{"merged": 1}])
            self.assertEqual(metadata["requested_windows"], [{"start_ms": 10, "end_ms": 20}])
            scoped.assert_called_once_with(
                [{"old": 1}], [{"fresh": 1}], [{"start_ms": 10, "end_ms": 20}]
            )
            merge.assert_called_once_with([{ "old": 1}], [{"fresh": 1}])

    def test_generic_occluded_receipt_replay_requires_bound_plan_and_scopes_rows(self):
        with workspace_temp() as root:
            recovery = root / "occluded-receipt-recovery"
            recovery.mkdir()
            (recovery / "receipt-inspection.json").write_text(json.dumps({
                "source_sha256": "a" * 64,
                "readings": [],
                "windows": [{"start_ms": 10, "end_ms": 20, "fps": 16,
                             "reason": "source_bound_occluded_receipt_review"}],
            }), encoding="utf-8")
            manifest_hash = hashlib.sha256((recovery / "receipt-inspection.json").read_bytes()).hexdigest()
            windows = [{
                "start_ms": 10,
                "end_ms": 20,
                "triggers": [{
                    "source_timestamp_ms": 15,
                    "evidence": "base.png",
                    "line_box": [314, 828, 709, 863],
                    "kind": "occluded_receipt_line",
                    "owner_ref": "outcome-1",
                    "owner_start_ms": 15,
                    "owner_end_ms": 15,
                    "window_start_ms": 10,
                    "window_end_ms": 20,
                    "physical_overlay_count": 1,
                    "source_line_text_sha256": hashlib.sha256(b"Friendship with Air Groove went up by 7.").hexdigest(),
                }],
            }]
            (recovery / "last-plan.json").write_text(json.dumps({
                "schema": "tracen-replay/occluded-receipt-recovery-v1",
                "source_sha256": "a" * 64,
                "manifest_sha256": manifest_hash,
                "requested_fps": 16,
                "requested_windows": windows,
                "processed_windows": windows,
                "pending_windows": [],
            }), encoding="utf-8")
            group = {
                "kind": "occluded_receipt",
                "folder": "occluded-receipt-recovery",
                "manifest": "occluded-receipt-recovery/receipt-inspection.json",
                "manifest_sha256": manifest_hash,
                "plan": "occluded-receipt-recovery/last-plan.json",
                "plan_sha256": hashlib.sha256((recovery / "last-plan.json").read_bytes()).hexdigest(),
            }
            with patch("tracen_replay.occluded_receipt_recovery.scoped_observations",
                       return_value=[{"promoted": 1}]) as scoped, \
                    patch("tracen_replay.occluded_receipt_recovery._validate_cache_provenance",
                          return_value={}), \
                    patch("tracen_replay.inspect_receipts.merge",
                          return_value=[{"merged": 1}]) as merge:
                result, metadata = _replay_recovery_rows(
                    [{"old": 1}], [{"fresh": 1}], group, root, "a" * 64
                )

            self.assertEqual(result, [{"merged": 1}])
            self.assertEqual(metadata["processed_windows"], windows)
            scoped.assert_called_once_with(
                [{"old": 1}], [{"fresh": 1}], windows
            )
            merge.assert_called_once_with([{ "old": 1}], [{"promoted": 1}])

            # A newly pinned plan can still falsely claim an uninspected
            # interval or sampling rate. Hash validation alone cannot catch
            # those semantic changes; promotion must not be called.
            original_plan = json.loads((recovery / "last-plan.json").read_text(encoding="utf-8"))
            for change in ({"end_ms": 21}, {"fps": 32}):
                with self.subTest(uninspected_window=change):
                    changed_plan = deepcopy(original_plan)
                    changed_plan["processed_windows"][0].update(change)
                    (recovery / "last-plan.json").write_text(json.dumps(changed_plan), encoding="utf-8")
                    changed_group = dict(group, plan_sha256=hashlib.sha256(
                        (recovery / "last-plan.json").read_bytes()).hexdigest())
                    with patch("tracen_replay.occluded_receipt_recovery._validate_cache_provenance", return_value={}), \
                            patch("tracen_replay.occluded_receipt_recovery.scoped_observations") as promote:
                        with self.assertRaisesRegex(PipelineError, "was not inspected"):
                            _replay_recovery_rows([{"old": 1}], [{"fresh": 1}], changed_group, root, "a" * 64)
                        promote.assert_not_called()

            (recovery / "last-plan.json").write_text(json.dumps({
                "source_sha256": "a" * 64, "manifest_sha256": manifest_hash,
                "requested_windows": [], "processed_windows": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "plan_sha256"):
                _replay_recovery_rows([], [], group, root, "a" * 64)

    def test_generic_occluded_receipt_replay_rejects_unbound_plan(self):
        with workspace_temp() as root:
            recovery = root / "occluded-receipt-recovery"
            recovery.mkdir()
            (recovery / "last-plan.json").write_text(json.dumps({
                "source_sha256": "a" * 64,
                "requested_windows": [],
            }), encoding="utf-8")
            group = {
                "kind": "occluded_receipt",
                "folder": "occluded-receipt-recovery",
                "manifest_sha256": "c" * 64,
            }
            with self.assertRaisesRegex(PipelineError, "not bound"):
                _replay_recovery_rows([], [], group, root, "a" * 64)

    def test_training_recovery_manifest_reconstructs_bounded_fields_without_last_plan(self):
        with workspace_temp() as root:
            recovery = root / "training"
            recovery.mkdir()
            (recovery / "receipt-inspection.json").write_text(
                json.dumps({
                    "windows": [{"start_ms": 100, "end_ms": 200, "fps": 60}],
                    "readings": [],
                }),
                encoding="utf-8",
            )
            group = {
                "kind": "training_gain",
                "folder": "training",
                "manifest": "receipt-inspection.json",
                "manifest_sha256": "c" * 64,
            }
            rows = [{
                "source_timestamp_ms": 120,
                "screen": "training_result",
                "training_option": "wit",
                "facts": {"training_gains": {"speed": 4}},
            }]
            owner = {
                "id": "training-0001",
                "first_seen_ms": 100,
                "last_seen_ms": 200,
                "conflicting_readings": {"wit": [2, 21]},
            }
            with patch("tracen_replay.transactions.training_events", return_value=[owner]) as events, \
                    patch("tracen_replay.training_gain_recovery.promote", return_value=[{"merged": 1}]) as promote:
                result, metadata = _replay_recovery_rows(
                    rows, [{"fresh": 1}], group, root, "a" * 64
                )

            self.assertEqual(result, [{"merged": 1}])
            self.assertEqual(metadata["processed_windows"][0]["owner_id"], "training-0001")
            self.assertEqual(metadata["processed_windows"][0]["fields"], ["wit"])
            events.assert_called_once_with(rows)
            promote.assert_called_once()
            self.assertEqual(promote.call_args.args[2][0]["fields"], ["wit"])

    def test_training_last_plan_candidate_metadata_requires_source_binding(self):
        with workspace_temp() as root:
            recovery = root / "training"
            recovery.mkdir()
            (recovery / "last-plan.json").write_text(
                json.dumps({
                    "manifest_sha256": "c" * 64,
                    "requested_windows": [],
                }),
                encoding="utf-8",
            )
            group = {
                "kind": "training_gain",
                "folder": "training",
                "manifest_sha256": "c" * 64,
            }

            with self.assertRaisesRegex(PipelineError, "another source"):
                _replay_recovery_rows([], [], group, root, "a" * 64)

    def test_training_last_plan_candidate_metadata_rejects_malformed_window(self):
        with workspace_temp() as root:
            recovery = root / "training"
            recovery.mkdir()
            (recovery / "last-plan.json").write_text(
                json.dumps({
                    "source_sha256": "a" * 64,
                    "manifest_sha256": "c" * 64,
                    "requested_windows": "not-an-array",
                }),
                encoding="utf-8",
            )
            group = {
                "kind": "training_gain",
                "folder": "training",
                "manifest_sha256": "c" * 64,
            }

            with self.assertRaisesRegex(PipelineError, "requested_windows must be an array"):
                _replay_recovery_rows([], [], group, root, "a" * 64)

    def test_training_conflicting_source_candidates_are_not_promoted(self):
        rows = [_row(100, "training/frame-100.png", amount=3),
                _row(133, "training/frame-133.png", amount=4)]

        promoted, metadata = _replay_recovery_rows(
            rows, [], {"kind": "training_gain"}, Path("."), "a" * 64
        )

        self.assertFalse(any(
            row.get("facts", {}).get("training_gains")
            for row in promoted
            if isinstance(row, dict)
        ))
        self.assertEqual(metadata.get("candidate_only_recovery_count", 0), 0)
        self.assertEqual(metadata["candidate_only_recoveries"], [])

    def test_training_single_frame_nested_candidate_is_replayed(self):
        row = _row(
            100, "training/frame-100.png", field="wit", amount=13,
            confidence=97.0, wide="+13:", wide_confidence=84.0,
            wide_box=[448, 930, 660, 1027],
        )

        promoted, metadata = _replay_recovery_rows(
            [row], [], {"kind": "training_gain"}, Path("."), "a" * 64
        )

        self.assertEqual(promoted[0]["facts"]["training_gains"]["wit"], 13)
        self.assertEqual(metadata["candidate_only_recovery_count"], 1)
        self.assertEqual(
            metadata["candidate_only_recoveries"][0]["basis"],
            "same_frame_nested_source_gain_crop_agreement",
        )

    def test_optionless_candidate_is_not_promoted_for_committed_owner(self):
        owner = _row(100, "training/owner.png", field="wit", amount=21, option="wit")
        owner["facts"]["training_gains"] = {"wit": 21}
        owner["facts"]["training_gain_crop_provenance"] = {}
        optionless = [
            _row(133, "training/one.png", amount=3, option=None),
            _row(166, "training/two.png", amount=3, option=None),
        ]

        promoted, metadata = _replay_recovery_rows(
            [owner], optionless,
            {"kind": "training_gain"}, Path("."), "a" * 64,
        )

        self.assertEqual([row["source_timestamp_ms"] for row in promoted], [100])
        self.assertEqual(metadata.get("candidate_only_recovery_count", 0), 0)

        only_optionless, _metadata = _replay_recovery_rows(
            [], optionless,
            {"kind": "training_gain"}, Path("."), "a" * 64,
        )
        self.assertEqual(only_optionless, [])

    def test_replay_preserves_dense_phase_rows_for_state_bound_resolution(self):
        full_shape = ("skill_points", "speed", "wit")
        component_shape = ("skill_points", "wit")
        base = [_phase_row(
            1000,
            {"speed": 8, "wit": 21, "skill_points": 10},
            full_shape,
        )]
        fresh = [
            _phase_row(
                1033,
                {"speed": 8, "wit": 21, "skill_points": 10},
                full_shape,
            ),
            _phase_row(
                1066,
                {"speed": 8, "wit": 21, "skill_points": 10},
                full_shape,
            ),
            _phase_row(
                1099,
                {"wit": 2, "skill_points": 1},
                component_shape,
            ),
            _phase_row(
                1132,
                {"wit": 2, "skill_points": 1},
                component_shape,
            ),
            # This candidate-only row creates the overlapping recomputed
            # scope that used to suppress all accepted rows in the reviewed
            # window.  It is deliberately a source candidate, not a selected
            # amount; the repeated canonical rows above carry the phase.
            _row(1115, "1115-candidate.png", amount=8, option="wit"),
            # This source row is outside the reviewed phase and must not be
            # invented or imported by the bounded replay.
            _phase_row(2000, {"speed": 99}, ("speed",)),
        ]
        group = {
            "kind": "training_gain",
            "windows": [{"start_ms": 1000, "end_ms": 1150, "fps": 60}],
        }

        promoted, _metadata = _replay_recovery_rows(
            base, fresh, group, Path("."), "a" * 64,
        )

        timestamps = {row["source_timestamp_ms"] for row in promoted}
        self.assertTrue({1000, 1033, 1066, 1099, 1132}.issubset(timestamps))
        self.assertNotIn(2000, timestamps)
        result_rows = [
            row for row in promoted
            if 1000 <= row["source_timestamp_ms"] <= 1132
        ]
        without_checkpoints = training_events(result_rows)[0]
        self.assertEqual(
            without_checkpoints["gain_phase_candidates"]["skill_points"]["value"],
            10,
        )
        checkpoint = {
            "id": "checkpoint-before",
            "first_seen_ms": 900,
            "last_seen_ms": 900,
            "values": {
                "speed": 100,
                "stamina": 100,
                "power": 100,
                "guts": 100,
                "wit": 100,
                "skill_points": 100,
            },
            "evidence": "checkpoint.png",
        }
        with_checkpoints = training_events(result_rows, [checkpoint])[0]
        self.assertEqual(with_checkpoints["deltas"]["skill_points"], 10)
        self.assertNotIn("skill_points", with_checkpoints["conflicting_readings"])

    def test_actual_dense_skill_points_phase_is_replayed_without_state_choice(self):
        root = localdata.root("prepared_snapshot_initial", "independent-01")
        report_path = localdata.root("full_worker_candidate_batch_reports", "independent-01/report.json")
        manifest_path = root / "training-gain-recovery/receipt-inspection.json"
        if not all(path.is_file() for path in (report_path, manifest_path)):
            self.skipTest("actual SP10/SP17 replay fixtures are unavailable")

        report = json.loads(report_path.read_text(encoding="utf-8"))
        base = report["gameplay_tracking"]["readings"]
        recovery_root = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fresh = reparse_inspection(manifest, recovery_root)
        group = {
            "kind": "training_gain",
            "folder": "training-gain-recovery",
            "manifest": "receipt-inspection.json",
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
        source_sha256 = report["source"]["sha256"]
        promoted, _metadata = _replay_recovery_rows(
            base, fresh, group, root, source_sha256,
        )
        checkpoints = report["gameplay_tracking"]["checkpoints"]

        for start_ms, end_ms, expected in (
            (534933, 535200, 10),
            (792750, 793017, 17),
        ):
            rows = [
                row for row in promoted
                if row.get("screen") == "training_result"
                and start_ms <= row.get("source_timestamp_ms", -1) <= end_ms
            ]
            self.assertGreaterEqual(len(rows), 5)
            without_checkpoints = training_events(rows)[0]
            self.assertEqual(
                without_checkpoints["gain_phase_candidates"]["skill_points"]["value"],
                expected,
            )
            state = next(
                checkpoint for checkpoint in reversed(checkpoints)
                if checkpoint["last_seen_ms"] < start_ms
            )
            with_checkpoints = training_events(rows, [state])[0]
            self.assertEqual(with_checkpoints["deltas"]["skill_points"], expected)
            self.assertNotIn("skill_points", with_checkpoints["conflicting_readings"])

    def test_actual_v1_candidate_speed_is_replayed_from_gameplay_source(self):
        root = localdata.root("prepared_snapshot_initial", "v1")
        rels = [
            "training-inspection/262000/frame-000017.png",
            "training-inspection/262000/frame-000018.png",
            "training-inspection/262000/frame-000019.png",
        ]
        paths = [root / rel for rel in rels]
        if not all(path.is_file() and path.with_name(path.stem + ".v2.json").is_file() for path in paths):
            self.skipTest("prepared v1 source frames are unavailable")
        rows = []
        for rel, path in zip(rels, paths):
            raw = json.loads(path.with_name(path.stem + ".v2.json").read_text(encoding="utf-8"))
            row = parse(raw)
            row.update(source_timestamp_ms=raw["source_timestamp_ms"], evidence=rel,
                       training_option="wit")
            rows.append(row)

        promoted, metadata = _replay_recovery_rows(
            rows, [], {"kind": "training_gain"}, root,
            "a75008eb095a2d37f9d0b49d09f1a54be8285bd9ef69e243037440a580193174",
        )

        recovered = [row for row in promoted
                     if row.get("facts", {}).get("training_gains", {}).get("speed") == 3]
        self.assertEqual(len(recovered), 1)
        proof = recovered[0]["facts"]["training_gain_candidate_recovery"]["speed"]
        self.assertEqual(proof["accepted_amount"], 3)
        self.assertEqual(metadata["candidate_only_recovery_count"], 1)
        self.assertFalse(proof["policy"]["uses_balance_arithmetic"])

    def test_actual_independent_wit_candidate_is_replayed_from_gameplay_source(self):
        root = localdata.root("prepared_snapshot_early", "independent-02")
        rels = [
            "training-recovery-v1/training-inspection/830250/frame-000014.png",
            "training-recovery-v1/training-inspection/830250/frame-000015.png",
            "training-recovery-v1/training-inspection/830250/frame-000016.png",
        ]
        paths = [root / rel for rel in rels]
        if not all(path.is_file() and path.with_name(path.stem + ".v2.json").is_file() for path in paths):
            self.skipTest("prepared independent-02 source frames are unavailable")
        rows = []
        for rel, path in zip(rels, paths):
            raw = json.loads(path.with_name(path.stem + ".v2.json").read_text(encoding="utf-8"))
            row = parse(raw)
            row.update(source_timestamp_ms=raw["source_timestamp_ms"], evidence=rel)
            rows.append(row)

        promoted, metadata = _replay_recovery_rows(
            rows, [], {"kind": "training_gain"}, root,
            "a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313",
        )

        recovered = [row for row in promoted
                     if row.get("facts", {}).get("training_gains", {}).get("wit") == 13]
        self.assertEqual(len(recovered), 1)
        proof = recovered[0]["facts"]["training_gain_candidate_recovery"]["wit"]
        self.assertEqual(proof["accepted_amount"], 13)
        self.assertEqual(metadata["candidate_only_recovery_count"], 1)
        self.assertFalse(proof["policy"]["uses_expected_amount"])
        transition = next(row for row in promoted if row["source_timestamp_ms"] == 830683)
        self.assertNotIn("wit", transition.get("facts", {}).get("training_gains", {}))


if __name__ == "__main__":
    unittest.main()
