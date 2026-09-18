"""Tests of ``tests.test_preview_source_regressions`` that need locally preserved evidence; they run only where it is."""
from __future__ import annotations
import copy
import unittest
from pathlib import Path
from PIL import Image
from tests import localdata
from tracen_replay.preview_observations import (
    _explicit_phase_proof,
    build_preview_observations,
    parse_preview_overlay,
    preview_phase_proof,
    produce_preview_panel_from_lines,
)
from tracen_replay.vision import parse as parse_vision


class SourcePreviewRegressionTests(unittest.TestCase):




    @localdata.needs("development_first_recording", "training-inspection", "1405000", "frame-000013.png")
    def test_same_frame_readers_share_result_flag_and_reject_crossfade_preview(self):
        # Both reader entry points inspect the exact same source pixels.  Their
        # fixed probes must agree on the result flag, and the preview parser
        # must reject the fading menu/Failure remnants from both paths.
        from tracen_replay.vision import NeuralReader, parse as parse_vision

        path = localdata.root(
            "development_first_recording", "training-inspection", "1405000", "frame-000013.png"
        )
        reader = NeuralReader()
        with Image.open(path) as opened:
            pane = opened.convert("RGB")
        normal = reader.read(pane)
        result_reader = reader.read_training(pane)
        self.assertEqual(normal["result_grid"], result_reader["result_grid"])
        self.assertTrue(normal["result_grid"])
        for raw in (normal, result_reader):
            parsed = parse_vision(raw)
            self.assertFalse(parsed["facts"].get("preview_overlay_proven"))
            self.assertEqual(parsed["completed_action"],
                             "training" if raw is result_reader else None)

    @unittest.skipUnless(
        localdata.available("development_first_recording", "gameplay", "part-004-frame-000200.png")
        and localdata.available("development_first_recording", "gameplay", "part-011-frame-000331.png"),
        "clean menu source controls are unavailable",
    )
    def test_clean_menu_controls_use_source_consensus_for_trailing_zero_and_song_rows(self):
        """Exercise the normal reader on the two clean menu controls.

        The cached result crop for the first control historically exposed a
        clipped ``+1``.  The source-bound recovery path must use the same
        image's validated crop consensus to retain ``+10``.  The second
        control additionally proves that the pink song row remains a separate
        preview channel from the orange main training row.
        """

        from tracen_replay.vision import NeuralReader

        reader = NeuralReader()
        cases = (
            (
                localdata.root("development_first_recording", "gameplay", "part-004-frame-000200.png"),
                "wit",
                {("stat_change", "wit", 18), ("stat_change", "skill_points", 10)},
                set(),
            ),
            (
                localdata.root("development_first_recording", "gameplay", "part-011-frame-000331.png"),
                "stamina",
                {("stat_change", "stamina", 29), ("stat_change", "guts", 15),
                 ("stat_change", "skill_points", 10)},
                {("training_modifier_change", "stamina", 15),
                 ("training_modifier_change", "guts", 7),
                 ("training_modifier_change", "skill_points", 12)},
            ),
        )
        for path, option, expected_main, expected_modifiers in cases:
            with self.subTest(path=path.name):
                with Image.open(path) as opened:
                    raw = reader.read(opened.convert("RGB"))
                parsed = parse_vision(raw)
                self.assertTrue(raw["current_grid"])
                self.assertFalse(raw["result_grid"])
                self.assertEqual(parsed["screen"], "training_preview")
                self.assertIsNone(parsed["completed_action"])
                recovery = raw.get("preview_recovery")
                self.assertEqual(recovery.get("status"), "resolved")
                self.assertEqual(recovery.get("option"), option)
                facts = parsed["facts"]
                main = {
                    (effect["kind"], effect["field"], effect["amount"])
                    for effect in facts.get("preview_overlay_effects", [])
                }
                modifiers = {
                    ("training_modifier_change", effect["field"], effect["amount"])
                    for effect in facts.get("preview_modifier_effects", [])
                }
                self.assertTrue(expected_main <= main)
                self.assertTrue(expected_modifiers <= modifiers)
                self.assertNotIn(("stat_change", "skill_points", 1), main)
