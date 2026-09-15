"""Source-root binding tests for persisted preview recovery promotion."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.preview_observations import (
    build_preview_observations,
    parse_preview_overlay,
)


REPORT = localdata.root("third_recording_receipt_logs", "report.json")
SOURCE_ROOT = localdata.root("prepared_snapshot_final", "independent-02")


class PreviewSourceRootHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not REPORT.is_file():
            raise unittest.SkipTest("immutable preserved third-recording report is unavailable")
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        cls.rows = {
            timestamp: next(
                row
                for row in report["gameplay_tracking"]["readings"]
                if row.get("source_timestamp_ms") == timestamp
            )
            for timestamp in (1214500, 1216250)
        }

    @unittest.skipUnless(
        SOURCE_ROOT.is_dir(), "immutable preserved third-recording source root is unavailable"
    )
    def test_source_root_replaces_mutated_existing_skill_points(self):
        row = copy.deepcopy(self.rows[1216250])
        for effect in row["facts"]["preview_overlay_effects"]:
            if effect.get("field") == "skill_points":
                effect["amount"] = 99

        parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
        self.assertIn(
            ("stat_change", "power", 11),
            {
                (effect.get("kind"), effect.get("field"), effect.get("amount"))
                for effect in parsed["preview_overlay_effects"]
            },
        )
        self.assertNotIn(
            99,
            {effect.get("amount") for effect in parsed["preview_overlay_effects"]},
        )
        built = build_preview_observations([row], source_root=SOURCE_ROOT)
        self.assertNotIn(
            99,
            {
                item["payload"].get("amount")
                for item in built["observations"]
            },
        )

    @unittest.skipUnless(
        SOURCE_ROOT.is_dir(), "immutable preserved third-recording source root is unavailable"
    )
    def test_source_root_replaces_mutated_existing_song_modifier(self):
        row = copy.deepcopy(self.rows[1214500])
        for effect in row["facts"]["preview_modifier_effects"]:
            if effect.get("field") == "skill_points":
                effect["amount"] = 99

        parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
        self.assertIn(
            ("song_modifier_change", "skill_points", 5),
            {
                (effect.get("kind"), effect.get("field"), effect.get("amount"))
                for effect in parsed["preview_modifier_effects"]
            },
        )
        self.assertNotIn(
            99,
            {effect.get("amount") for effect in parsed["preview_modifier_effects"]},
        )
        built = build_preview_observations([row], source_root=SOURCE_ROOT)
        self.assertNotIn(
            99,
            {
                item["payload"].get("amount")
                for item in built["observations"]
            },
        )

    @unittest.skipUnless(
        SOURCE_ROOT.is_dir(), "immutable preserved third-recording source root is unavailable"
    )
    def test_source_root_invalid_recovery_does_not_fall_back_to_mutable_facts(self):
        row = copy.deepcopy(self.rows[1216250])
        for effect in row["facts"]["preview_overlay_effects"]:
            effect["amount"] = 99
        recovery = row["facts"]["preview_recovery"]
        forged_path = "initial-baseline/part-010/frames/000066.jpeg"
        recovery["source_frame_evidence"] = forged_path
        recovery["source_frame_verification"]["path"] = forged_path

        parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
        self.assertEqual(parsed["preview_overlay_effects"], [])
        # The unbound envelope is not promoted and the mutated report lists
        # are never used; what remains observable is the row's own immutable
        # neural witness reparsed under the root.
        built = build_preview_observations([row], source_root=SOURCE_ROOT)
        self.assertNotIn(99, [o["payload"].get("amount") for o in built["observations"]])
        self.assertEqual([o["payload"] for o in built["observations"]], self._witness_payloads(row))

    def _witness_payloads(self, row):
        from tracen_replay.preview_observations import _native_witness_overlay
        native = _native_witness_overlay(row, SOURCE_ROOT)
        self.assertIsNotNone(native)
        pristine = copy.deepcopy(row)
        pristine["facts"].update({k: v for k, v in native.items() if k.startswith("preview_")})
        pristine["facts"].pop("preview_recovery", None)
        pristine.pop("preview_recovery", None)
        return [o["payload"] for o in build_preview_observations([pristine])["observations"]]

    def test_source_root_rejects_coordinated_envelope_amount_mutation(self):
        row = copy.deepcopy(self.rows[1216250])
        for owner in (row, row["facts"]):
            recovery = owner["preview_recovery"]
            for key in ("effects", "modifier_effects"):
                for effect in recovery.get(key, []):
                    effect["amount"] = 99
            for region in recovery.get("regions", {}).values():
                if isinstance(region, dict):
                    region["text"] = "+99"
                    region["parsed_value"] = 99
            for observation in recovery.get("observations", []):
                selected = observation.get("selected")
                if isinstance(selected, dict):
                    selected["text"] = "+99"
                    selected["parsed_value"] = 99

        parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
        self.assertEqual(parsed["preview_overlay_effects"], [])
        self.assertEqual(parsed["preview_modifier_effects"], [])
        built = build_preview_observations([row], source_root=SOURCE_ROOT)
        self.assertNotIn(99, [o["payload"].get("amount") for o in built["observations"]])
        self.assertEqual([o["payload"] for o in built["observations"]], self._witness_payloads(row))

    @unittest.skipUnless(
        SOURCE_ROOT.is_dir(), "immutable preserved third-recording source root is unavailable"
    )
    def test_nested_namespace_witness_evidence_is_cited_root_relative(self):
        # An earlier implementation's independent-02 worker was rejected by the consumer
        # contract: retained native channels from the reparsed witness cited
        # the frame as ``gameplay/...`` (namespace-relative) beside the
        # reading's ``initial-baseline/gameplay/...`` path.  Every evidence
        # path an observation cites must exist under the worker root.
        for timestamp in (1214500, 1216250):
            with self.subTest(timestamp=timestamp):
                row = copy.deepcopy(self.rows[timestamp])
                parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
                cited = set()
                for key in ("preview_overlay_effects", "preview_modifier_effects"):
                    for effect in parsed.get(key) or []:
                        cited.update(effect.get("source_evidence") or [])
                cited.update(
                    (parsed.get("preview_overlay_evidence") or {}).get("source_evidence") or []
                )
                self.assertTrue(cited)
                self.assertEqual(
                    sorted(path for path in cited if not (SOURCE_ROOT / path).is_file()),
                    [],
                )
                built = build_preview_observations([row], source_root=SOURCE_ROOT)
                missing = sorted({
                    path
                    for observation in built["observations"]
                    for path in observation.get("evidence", [])
                    if not (SOURCE_ROOT / path).is_file()
                })
                self.assertEqual(missing, [])

    def test_persisted_recovery_without_source_root_is_not_promoted(self):
        row = copy.deepcopy(self.rows[1216250])
        row["facts"]["preview_overlay_effects"] = []
        row["facts"]["preview_modifier_effects"] = []
        recovery = row["facts"]["preview_recovery"]
        forged_path = "initial-baseline/part-010/frames/000066.jpeg"
        recovery["source_frame_evidence"] = forged_path
        recovery["source_frame_verification"]["path"] = forged_path

        parsed = parse_preview_overlay(row)
        self.assertEqual(parsed["preview_overlay_effects"], [])
        built = build_preview_observations([row])
        self.assertEqual(built["observations"], [])

    def test_rootless_persisted_row_keeps_the_parser_owned_typed_facts(self):
        # Without a root the envelope is not promoted, but the row's own
        # parser-owned preview lists stay observable: they are trusted exactly
        # as on rows without an envelope, and clearing them dropped every
        # native preview on recovered frames in full-recording reports.
        row = copy.deepcopy(self.rows[1216250])
        row["facts"]["preview_overlay_effects"] = [
            {"kind": "stat_change", "field": "stamina", "amount": 99,
             "phase": "preview", "preview": True, "awarded": False},
        ]
        row["facts"]["preview_overlay_proven"] = True
        built = build_preview_observations([row])
        payloads = [o["payload"] for o in built["observations"]]
        self.assertIn({"kind": "stat_change", "field": "stamina", "amount": 99}, payloads)


if __name__ == "__main__":
    unittest.main()
