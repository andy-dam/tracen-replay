"""Tests of ``tests.test_preview_recovery_promotion`` that need locally preserved evidence; they run only where it is."""
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
from tests.test_preview_recovery_promotion import REPORT, SOURCE_ROOT


class PreviewRecoveryPromotionTests(unittest.TestCase):
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
            for timestamp in (401000, 402250, 1214500, 1215750, 1216250, 1297750)
        }

    def test_actual_v11_recovery_effects_reach_preview_builder(self):
        expected = {
            401000: {("stamina", 5), ("power", 13), ("skill_points", 5)},
            402250: {("speed", 3), ("power", 3), ("guts", 10), ("skill_points", 5)},
            1214500: {("speed", 11), ("power", 4), ("skill_points", 4)},
            1215750: {("speed", 6), ("power", 5), ("guts", 15), ("skill_points", 6)},
            1216250: {("stamina", 5), ("power", 11)},
            1297750: {("stamina", 11), ("guts", 9), ("skill_points", 5)},
        }
        for timestamp, row in self.rows.items():
            with self.subTest(timestamp=timestamp):
                parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
                parsed_values = {
                    (item["field"], item["amount"])
                    for item in parsed["preview_overlay_effects"]
                    if item.get("kind") == "stat_change"
                }
                self.assertTrue(expected[timestamp] <= parsed_values)

                built = build_preview_observations([row], source_root=SOURCE_ROOT)
                observed = {
                    (item["payload"]["field"], item["payload"]["amount"])
                    for item in built["observations"]
                    if item["payload"].get("kind") == "stat_change"
                }
                self.assertTrue(expected[timestamp] <= observed)
                self.assertTrue(all(item["phase"] == "preview" for item in built["observations"]))
                self.assertTrue(built["preview_is_not_applied"])
                self.assertFalse(built["committed_actions_inferred"])

    @unittest.skipUnless(
        SOURCE_ROOT.is_dir(), "immutable preserved third-recording source root is unavailable"
    )
    def test_source_root_validates_all_actual_recovery_rows(self):
        expected = {
            401000: {("stamina", 5), ("power", 13), ("skill_points", 5)},
            402250: {("speed", 3), ("power", 3), ("guts", 10), ("skill_points", 5)},
            1214500: {("speed", 11), ("power", 4), ("skill_points", 4)},
            1215750: {("speed", 6), ("power", 5), ("guts", 15), ("skill_points", 6)},
            1216250: {("stamina", 5), ("power", 11)},
            1297750: {("stamina", 11), ("guts", 9), ("skill_points", 5)},
        }
        for timestamp, row in self.rows.items():
            with self.subTest(timestamp=timestamp):
                parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
                observed = {
                    (item["field"], item["amount"])
                    for item in parsed["preview_overlay_effects"]
                }
                self.assertTrue(expected[timestamp] <= observed)

    @unittest.skipUnless(
        SOURCE_ROOT.is_dir(), "immutable preserved third-recording source root is unavailable"
    )
    def test_merged_neighbor_tokens_cannot_change_field_identity(self):
        """The ``+3 +10`` source line binds each token to its own column."""

        row = copy.deepcopy(self.rows[402250])
        for owner in (row, row["facts"]):
            recovery = owner["preview_recovery"]
            region = recovery["regions"]["preview.main.power"]
            region["text"] = "+10"
            region["parsed_value"] = 10
            for effect in recovery["effects"]:
                if effect.get("source_region") == "preview.main.power":
                    effect["amount"] = 10
            for observation in recovery["observations"]:
                if observation.get("region") != "preview.main.power":
                    continue
                selected = observation.get("selected")
                if isinstance(selected, dict):
                    selected["text"] = "+10"
                    selected["parsed_value"] = 10
        parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
        self.assertFalse(any(
            effect.get("field") == "power" and effect.get("amount") == 10
            for effect in parsed["preview_overlay_effects"]
        ))

    @unittest.skipUnless(
        SOURCE_ROOT.is_dir(), "immutable preserved third-recording source root is unavailable"
    )
    def test_source_root_ignores_coordinated_report_ocr_mutation(self):
        """A copied report OCR line cannot replace the immutable neural witness."""

        row = copy.deepcopy(self.rows[402250])
        for owner in (row, row["facts"]):
            recovery = owner["preview_recovery"]
            region = recovery["regions"]["preview.main.power"]
            region["text"] = "+99"
            region["parsed_value"] = 99
            for effect in recovery["effects"]:
                if effect.get("source_region") == "preview.main.power":
                    effect["amount"] = 99
            for observation in recovery["observations"]:
                if observation.get("region") != "preview.main.power":
                    continue
                selected = observation.get("selected")
                if isinstance(selected, dict):
                    selected["text"] = "+99"
                    selected["parsed_value"] = 99
        for line in row["ocr"]["neural"]:
            if line.get("text") == "+3 +10":
                line["text"] = "+99 +10"
        parsed = parse_preview_overlay(row, source_root=SOURCE_ROOT)
        self.assertFalse(any(
            effect.get("field") == "power" and effect.get("amount") == 99
            for effect in parsed["preview_overlay_effects"]
        ))

    def test_source_frame_identity_mutation_blocks_promotion(self):
        row = copy.deepcopy(self.rows[401000])
        row["source_frame_sha256"] = "0" * 64
        parsed = parse_preview_overlay(row)
        self.assertEqual(parsed["preview_overlay_effects"], [])
        built = build_preview_observations([row])
        self.assertFalse(
            any(
                item["payload"].get("field") == "power"
                and item["payload"].get("amount") == 13
                for item in built["observations"]
            )
        )

    def test_recovery_and_facts_disagreement_blocks_promotion(self):
        row = copy.deepcopy(self.rows[402250])
        row["preview_recovery"]["option"] = "speed"
        self.assertNotEqual(
            row["preview_recovery"], row["facts"]["preview_recovery"]
        )
        parsed = parse_preview_overlay(row)
        self.assertEqual(parsed["preview_overlay_effects"], [])
        built = build_preview_observations([row])
        self.assertEqual(built["observations"], [])

    def test_result_phase_mutation_never_becomes_preview(self):
        row = copy.deepcopy(self.rows[1215750])
        row["screen"] = "training_result"
        row["completed_action"] = "training"
        built = build_preview_observations([row])
        self.assertEqual(built["observations"], [])

    def test_result_proof_inside_recovery_blocks_promotion(self):
        row = copy.deepcopy(self.rows[1216250])
        for owner in (row, row["facts"]):
            owner["preview_recovery"]["phase"]["result_proven"] = True
        built = build_preview_observations([row])
        self.assertFalse(
            any(
                item["payload"].get("field") == "power"
                and item["payload"].get("amount") == 11
                for item in built["observations"]
            )
        )

    def test_region_value_mutation_blocks_amount_projection(self):
        row = copy.deepcopy(self.rows[1297750])
        for owner in (row, row["facts"]):
            owner["preview_recovery"]["regions"]["preview.main.stamina"]["parsed_value"] = 99
        parsed = parse_preview_overlay(row)
        self.assertEqual(parsed["preview_overlay_effects"], [])
        built = build_preview_observations([row])
        self.assertFalse(
            any(
                item["payload"].get("field") == "stamina"
                and item["payload"].get("amount") == 11
                for item in built["observations"]
            )
        )

    def test_mirrored_recovery_mutation_cannot_override_source_ocr(self):
        """A self-consistent copied envelope still needs source-row support."""

        row = copy.deepcopy(self.rows[1297750])
        for owner in (row, row["facts"]):
            recovery = owner["preview_recovery"]
            region = recovery["regions"]["preview.main.stamina"]
            region["text"] = "+99"
            region["parsed_value"] = 99
            for effect in recovery["effects"]:
                if effect.get("source_region") == "preview.main.stamina":
                    effect["amount"] = 99
            for observation in recovery["observations"]:
                if observation.get("region") != "preview.main.stamina":
                    continue
                selected = observation.get("selected")
                if isinstance(selected, dict):
                    selected["text"] = "+99"
                    selected["parsed_value"] = 99
        parsed = parse_preview_overlay(row)
        self.assertFalse(any(
            effect.get("field") == "stamina" and effect.get("amount") == 99
            for effect in parsed["preview_overlay_effects"]
        ))

    def test_mirrored_recovery_path_must_identify_source_frame(self):
        row = copy.deepcopy(self.rows[401000])
        changed_path = "initial-baseline/part-003/frames/000999.jpg"
        for owner in (row, row["facts"]):
            recovery = owner["preview_recovery"]
            recovery["source_frame_evidence"] = changed_path
            recovery["source_frame_verification"]["path"] = changed_path
        parsed = parse_preview_overlay(row)
        self.assertEqual(parsed["preview_overlay_effects"], [])
