"""Source-bound regressions for high-rate and cached training previews."""

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


def _line(text: str, box: tuple[int, int, int, int], confidence: float = 99.0):
    return {"text": text, "confidence": confidence, "box": list(box)}


def _training_source(
    *, preview_lines=None, lines=None, success=False,
    current_grid=False, result_grid=True,
):
    """Build the shape emitted by ``NeuralReader.read_training``.

    ``lines`` represents the small cached result-reader collection.  The
    optional ``preview_lines`` collection is a same-frame full detector
    reread.  The fixture deliberately contains a clipped ``+1`` in the
    former and a visibly complete ``+10`` in the latter so the test proves
    that the amount comes from the source-bound reread.
    """

    full = preview_lines or [
        _line("Training", (150, 1, 227, 32)),
        _line("Wit Lvl 3", (230, 165, 365, 194)),
        _line("+4", (291, 668, 347, 706)),
        _line("+18", (660, 667, 738, 705)),
        _line("+10", (757, 667, 831, 705)),
        _line("Speed", (301, 697, 361, 721)),
        _line("Wit", (671, 696, 732, 720)),
        _line("Skill Pts", (757, 695, 832, 720)),
        _line("Failure", (735, 767, 798, 794)),
    ]
    cached = lines or [
        _line("Training", (155, 0, 250, 29)),
        _line("Wit Lvl 3", (230, 165, 365, 194), 64.4),
        _line("+1", (757, 667, 831, 705), 99),
    ]
    return {
        "header": "Training",
        "evidence": "training-inspection/531000/frame-000012.png",
        "current_grid": current_grid,
        "result_grid": result_grid,
        "inspection": "training_result_only",
        "regions": {
            # This total is intentionally wrong for the preview assertion. It
            # is present only to demonstrate that totals are never consulted.
            "result.skill_points": _line("22/400", (708, 952, 810, 990)),
        },
        "lines": cached,
        "preview_lines": full,
        **({"success_visible": True} if success else {}),
    }


class SourcePreviewRegressionTests(unittest.TestCase):
    def test_same_frame_full_reread_is_accepted_when_legacy_view_has_no_conflict(self):
        # The dense view is useful once the sparse view contains no competing
        # signed row.  This models a real current-menu source; result-only
        # transition frames are covered by the negative regression below.
        source = _training_source(
            current_grid=True, result_grid=False,
            lines=[
                _line("Training", (155, 0, 250, 29)),
                _line("Wit Lvl 3", (230, 165, 365, 194), 64.4),
            ],
        )

        panel = produce_preview_panel_from_lines(source)
        self.assertIsNotNone(panel)
        self.assertEqual(
            {(item["field"], item["amount"]) for item in panel["effects"]},
            {("speed", 4), ("wit", 18), ("skill_points", 10)},
        )

        proof = preview_phase_proof(source)
        self.assertIsNotNone(proof)
        self.assertTrue(proof["menu_proven"])
        self.assertTrue(proof["stat_row_proven"])
        self.assertIsNotNone(_explicit_phase_proof({"preview_phase_proof": proof}))

        parsed = parse_preview_overlay(source)
        self.assertTrue(parsed["preview_overlay_proven"])
        self.assertEqual(
            {(item["field"], item["amount"])
             for item in parsed["preview_overlay_effects"]},
            {("speed", 4), ("wit", 18), ("skill_points", 10)},
        )
        self.assertNotIn(
            ("skill_points", 1),
            {(item["field"], item["amount"])
             for item in parsed["preview_overlay_effects"]},
        )

    def test_conflicting_same_frame_views_do_not_prefer_dense_trailing_zero(self):
        source = _training_source(
            current_grid=True,
            result_grid=False,
            lines=[
                _line("Training", (155, 0, 250, 29)),
                _line("Wit Lvl 3", (230, 165, 365, 194), 64.4),
                _line("+1", (757, 667, 831, 705), 99),
            ],
        )
        self.assertIsNone(produce_preview_panel_from_lines(source))
        parsed = parse_preview_overlay(source)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])
        self.assertEqual(
            parsed["rejected_counts"],
            {"conflicting_same_frame_preview_views": 1},
        )

    def test_parser_option_disagreement_abstains_before_typed_panel_promotion(self):
        source = _training_source(current_grid=True, result_grid=False)
        source["preview_panel"] = {"option": "Power Lvl 1"}
        parsed = parse_preview_overlay(source)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])
        self.assertEqual(parsed["preview_option"], None)
        self.assertEqual(
            parsed["rejected_counts"]["conflicting_parser_preview_options"], 1
        )

    def test_result_components_stay_separate_from_ordinary_row_amount(self):
        source = {
            "header": "Training",
            "current_grid": True,
            "result_grid": False,
            "lines": [
                _line("Stamina Lvl 2", (230, 169, 344, 193)),
                _line("+15", (375, 670, 451, 710)),
                _line("+29", (377, 707, 452, 743)),
                _line("Stamina", (393, 737, 460, 756)),
                _line("Failure", (415, 785, 476, 809)),
            ],
        }
        panel = produce_preview_panel_from_lines(source)
        self.assertIsNotNone(panel)
        self.assertEqual(
            [(item["field"], item["amount"])
             for item in panel["effects"]],
            [("stamina", 29)],
        )
        row = panel["geometry"]["stat_rows"]["stamina"]
        self.assertEqual(row["mode"], "training_row")
        self.assertEqual(
            panel["geometry"]["unassigned_components"]["stamina"]["row"]["amount"],
            15,
        )


    def test_cached_typed_panel_and_fresh_producer_have_same_effects(self):
        source = _training_source(
            current_grid=True,
            result_grid=False,
            lines=[
                _line("Training", (155, 0, 250, 29)),
                _line("Wit Lvl 3", (230, 165, 365, 194), 64.4),
            ],
        )
        fresh = parse_preview_overlay(source)
        panel = produce_preview_panel_from_lines(source)
        self.assertIsNotNone(panel)

        cached = copy.deepcopy(source)
        cached["preview_panel"] = panel
        cached_result = parse_preview_overlay(cached)
        self.assertEqual(
            [(item["field"], item["amount"])
             for item in fresh["preview_overlay_effects"]],
            [(item["field"], item["amount"])
             for item in cached_result["preview_overlay_effects"]],
        )
        self.assertTrue(cached_result["preview_overlay_proven"])

    def test_stale_result_label_with_source_panel_reaches_cached_consumer(self):
        # The ordinary vision classifier sees the fixed result probes and
        # labels this transition ``training_result``.  A typed panel from the
        # same frame cannot override that applied-action boundary, even when
        # its rows resemble a selectable menu.
        source = _training_source()
        parsed = parse_vision(source)
        self.assertEqual(parsed["screen"], "training_result")
        self.assertEqual(parsed["completed_action"], "training")
        result = build_preview_observations([
            dict(parsed, source_timestamp_ms=531000, evidence=source["evidence"])
        ])
        self.assertEqual(result["observations"], [])
        self.assertTrue(result["preview_is_not_applied"])
        self.assertFalse(result["committed_actions_inferred"])

    def test_result_label_without_geometry_override_stays_excluded(self):
        effect = {
            "kind": "stat_change", "field": "speed", "amount": 4,
            "phase": "preview", "preview": True, "awarded": False,
            "source_evidence": ["capture/frame.png"],
        }
        result = build_preview_observations([{
            "source_timestamp_ms": 1,
            "evidence": "capture/frame.png",
            "screen": "training_result",
            "completed_action": None,
            "facts": {
                "preview_overlay_proven": True,
                "preview_overlay_effects": [effect],
                # A registered basis alone is deliberately insufficient for
                # overriding an applied screen label.
                "preview_phase_proof": {
                    "phase": "preview", "menu_proven": True,
                    "result_proven": False, "basis": "current_training_grid",
                },
            },
            "stats": {},
        }])
        self.assertEqual(result["observations"], [])

    def test_result_marker_blocks_same_geometry_from_preview_channel(self):
        source = _training_source(success=True)
        self.assertIsNone(produce_preview_panel_from_lines(source))
        self.assertIsNone(preview_phase_proof(source))
        parsed = parse_preview_overlay(source)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])

    def test_missing_failure_badge_does_not_promote_signed_result_rows(self):
        source = _training_source()
        source["preview_lines"] = [
            line for line in source["preview_lines"]
            if line["text"] != "Failure"
        ]
        self.assertIsNone(produce_preview_panel_from_lines(source))
        self.assertIsNone(preview_phase_proof(source))
        self.assertFalse(parse_preview_overlay(source)["preview_overlay_proven"])

    def test_conflicting_same_row_amounts_abstain(self):
        source = _training_source()
        source["preview_lines"] = [
            line for line in source["preview_lines"]
            if line["text"] not in {"Training", "Wit Lvl 3", "+4", "+18", "Speed", "Wit", "Failure"}
        ] + [
            _line("Training", (150, 1, 227, 32)),
            _line("Wit Lvl 3", (230, 165, 365, 194)),
            _line("+10", (757, 667, 831, 705)),
            _line("+11", (757, 667, 831, 705)),
            _line("Speed", (301, 697, 361, 721)),
            _line("Wit", (671, 696, 732, 720)),
            _line("Skill Pts", (757, 695, 832, 720)),
            _line("Failure", (735, 767, 798, 794)),
        ]
        self.assertIsNone(produce_preview_panel_from_lines(source))
        self.assertIsNone(preview_phase_proof(source))
        parsed = parse_preview_overlay(source)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])


if __name__ == "__main__":
    unittest.main()
