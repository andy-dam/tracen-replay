import copy
import unittest

import numpy as np
from PIL import Image

from tracen_replay.crop_provenance import resolve_gain_regions
from tracen_replay.training_gain_source_refinement import (
    SCHEMA,
    refinement_requests,
    refine_training_gain_regions,
    validate_training_gain_refinement,
)
from tracen_replay.vision import _training_gain_refinement_fields, parse


class TrainingGainSourceRefinementTests(unittest.TestCase):
    def setUp(self):
        self.pane = Image.fromarray(
            np.zeros((1080, 810, 3), dtype=np.uint8), mode="RGB"
        )

    def test_requests_are_relative_and_cover_each_requested_field(self):
        requests = refinement_requests(("speed", "wit"))

        self.assertEqual(
            [item[0] for item in requests],
            [
                "expanded_gain.inner.speed",
                "expanded_gain.outer.speed",
                "expanded_gain.inner.wit",
                "expanded_gain.outer.wit",
            ],
        )
        for _region, box, metadata in requests:
            self.assertEqual(len(box), 4)
            self.assertLess(box[0], box[2])
            self.assertLess(box[1], box[3])
            self.assertIn(metadata["source_refinement_role"], {"inner", "outer"})

    def test_source_reader_attaches_pixel_proof_and_resolves_complete_amount(self):
        calls = []

        def recognize(crops):
            calls.append(len(crops))
            return [("+36", 0.942), ("+86", 0.69)]

        refined = refine_training_gain_regions(
            self.pane,
            recognize=recognize,
            fields=("speed",),
            header="Training",
            result_grid=True,
            source_metadata={
                "source_timestamp_ms": 845717,
                "evidence": "training-inspection/frame.png",
                "source_frame_sha256": "a" * 64,
            },
        )

        self.assertEqual(calls, [2])
        self.assertEqual(refined["status"], "refined")
        inner = refined["regions"]["expanded_gain.inner.speed"]
        self.assertEqual(inner["amount"], 36)
        self.assertEqual(inner["source_refinement_schema"], SCHEMA)
        self.assertTrue(inner["source_refinement_verified"])
        self.assertTrue(inner["source_pixel_verified"])
        self.assertRegex(inner["source_crop_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(inner["source_timestamp_ms"], 845717)
        self.assertEqual(inner["evidence"], "training-inspection/frame.png")

        resolution = resolve_gain_regions(refined["regions"], "speed")
        self.assertEqual(resolution["canonical_amount"], 36)
        self.assertEqual(resolution["canonical_candidate"]["source_refinement_role"], "inner")

    def test_low_confidence_crop_stays_diagnostic_when_outer_crop_is_strong(self):
        refined = refine_training_gain_regions(
            self.pane,
            recognize=lambda crops: [("+3", 0.79), ("+32", 0.97)],
            fields=("speed",),
            header="Training",
            result_grid=True,
        )

        self.assertEqual(refined["status"], "refined")
        self.assertIn(
            "expanded_gain.inner.speed", refined["rejections"]
        )
        resolution = resolve_gain_regions(refined["regions"], "speed")
        self.assertEqual(resolution["canonical_amount"], 32)
        self.assertEqual(resolution["canonical_candidate"]["source_refinement_role"], "outer")

    def test_competing_complete_refinement_views_remain_unresolved(self):
        refined = refine_training_gain_regions(
            self.pane,
            recognize=lambda crops: [("+4", 0.99), ("+44", 0.99)],
            fields=("speed",),
            header="Training",
            result_grid=True,
        )

        self.assertEqual(refined["status"], "unresolved")
        self.assertEqual(refined["reason"], "conflicting_source_refinement_crops")
        resolution = resolve_gain_regions(refined["regions"], "speed")
        self.assertIsNone(resolution["canonical_amount"])
        self.assertEqual(
            resolution["conflict_state"],
            "unresolved_conflicting_source_refinement",
        )
        self.assertEqual(resolution["candidate_amounts"], [4, 44])

    def test_clipped_prefix_and_full_refinement_conflict_never_selects_by_length(self):
        refined = refine_training_gain_regions(
            self.pane,
            recognize=lambda crops: [("+6", 0.99), ("+65", 0.99)],
            fields=("wit",),
            header="Training",
            result_grid=True,
        )

        resolution = resolve_gain_regions(refined["regions"], "wit")
        self.assertIsNone(resolution["canonical_amount"])
        self.assertEqual(
            resolution["conflict_state"],
            "unresolved_conflicting_source_refinement",
        )
        self.assertEqual(resolution["candidate_amounts"], [6, 65])

    def test_gate_does_not_call_recognizer_for_preview_or_unproven_panel(self):
        calls = []

        def recognize(crops):
            calls.append(crops)
            return []

        for kwargs, reason in (
            ({"header": "Lessons", "result_grid": True}, "training_header_not_proven"),
            ({"header": "Training", "result_grid": False}, "training_result_grid_not_proven"),
            ({"header": "Training", "result_grid": True, "preview": True}, "preview_screen"),
        ):
            with self.subTest(kwargs=kwargs):
                result = refine_training_gain_regions(
                    self.pane, recognize=recognize, fields=("speed",), **kwargs
                )
                self.assertEqual(result["status"], "gated")
                self.assertEqual(result["reason"], reason)
        self.assertEqual(calls, [])

    def test_unbound_dotted_region_cannot_be_promoted(self):
        regions = {
            "expanded_gain.inner.speed": {
                "text": "+32",
                "confidence": 99,
                "box": [300, 820, 420, 890],
                "role": "amount_crop_candidate",
                "input_eligible": True,
            },
        }

        resolution = resolve_gain_regions(regions, "speed")
        candidate = resolution["candidates"][0]
        self.assertFalse(candidate["canonical_eligible"])
        self.assertIsNone(resolution["canonical_amount"])

    def test_vision_refinement_field_selection_preserves_native_conflict(self):
        regions = {
            "gain.speed": {
                "text": "+4", "confidence": 99, "box": [300, 832, 414, 890]
            },
            "wide_gain.speed": {
                "text": "+44", "confidence": 95, "box": [250, 812, 462, 909]
            },
            "gain.power": {
                "text": "+7", "confidence": 84, "box": [696, 832, 812, 890]
            },
        }

        # A strong native crop is left to the existing resolver, including
        # its conflict guard.  A weak field is eligible for the source pass.
        selected = _training_gain_refinement_fields(regions)
        self.assertNotIn("speed", selected)
        self.assertIn("power", selected)

    def test_parse_consumes_bound_refinement_without_result_or_balance_input(self):
        lines = [{"text": "Training", "confidence": 99, "box": [155, 0, 250, 29]}]
        regions = {
            "gain.speed": {
                "text": "+3", "confidence": 84, "box": [300, 832, 414, 890]
            },
            "expanded_gain.inner.speed": {
                "text": "+32", "confidence": 97,
                "box": [300, 820, 420, 890],
                "role": "amount_crop_candidate",
                "input_eligible": True,
                "source_refinement_schema": SCHEMA,
                "source_refinement_role": "inner",
                "source_refinement_geometry": "canonical_gain_relative_inner",
                "source_refinement_verified": True,
                "source_refinement_pixel_bound": True,
                "source_pixel_verified": True,
                "source_pixel_basis": "relative_training_gain_source_crop",
                "source_observation_basis": "source_pixel_refined_training_gain",
                "source_crop_sha256": "b" * 64,
            },
            "expanded_gain.outer.speed": {
                "text": "bad", "confidence": 40,
                "box": [250, 816, 475, 914],
                "role": "amount_crop_candidate",
                "input_eligible": True,
                "source_refinement_schema": SCHEMA,
                "source_refinement_role": "outer",
                "source_refinement_geometry": "wide_gain_relative_outer",
                "source_refinement_verified": True,
                "source_refinement_pixel_bound": True,
                "source_pixel_verified": True,
                "source_pixel_basis": "relative_training_gain_source_crop",
                "source_observation_basis": "source_pixel_refined_training_gain",
                "source_crop_sha256": "c" * 64,
            },
        }
        parsed = parse({
            "lines": lines,
            "regions": regions,
            "header": "Training",
            "result_grid": True,
            "current_grid": False,
            "inspection": "training_result_only",
        })

        self.assertEqual(parsed["facts"]["training_gains"]["speed"], 32)
        proof = parsed["facts"]["training_gain_crop_provenance"]["speed"]
        self.assertEqual(proof["canonical_amount"], 32)
        self.assertEqual(proof["canonical_candidate"]["source_refinement_role"], "inner")

    def _bound_raw(self, *, recognize=None, fields=("speed",)):
        if recognize is None:
            recognize = lambda crops: [("+36", 0.94), ("+36", 0.95)]
        refinement = refine_training_gain_regions(
            self.pane,
            recognize=recognize,
            fields=fields,
            header="Training",
            result_grid=True,
        )
        return {
            "gameplay_sha256": refinement["gameplay_sha256"],
            "regions": copy.deepcopy(refinement["regions"]),
            "training_gain_source_refinement": refinement,
        }

    def test_cached_refinement_validation_recomputes_role_boxes_and_crop_hashes(self):
        raw = self._bound_raw()

        checked = validate_training_gain_refinement(raw, self.pane)

        self.assertEqual(checked["status"], "validated")
        self.assertEqual(
            checked["validated_regions"],
            ["expanded_gain.inner.speed", "expanded_gain.outer.speed"],
        )
        inner = checked["regions"]["expanded_gain.inner.speed"]
        self.assertTrue(inner["source_refinement_pixel_bound"])
        self.assertEqual(inner["source_refinement_validation"], SCHEMA + "/pixel-binding-v1")

    def test_cached_refinement_rejects_self_rehashed_crop_and_preserves_no_regions(self):
        raw = self._bound_raw()
        raw["training_gain_source_refinement"]["regions"][
            "expanded_gain.inner.speed"
        ]["source_crop_sha256"] = "a" * 64
        raw["regions"]["expanded_gain.inner.speed"]["source_crop_sha256"] = "a" * 64

        checked = validate_training_gain_refinement(raw, self.pane)

        self.assertEqual(checked["status"], "rejected")
        self.assertEqual(checked["reason"], "Training gain refinement crop pixels disagree with source.")
        self.assertEqual(checked["regions"], {})

    def test_cached_refinement_rejects_box_replacement_even_with_original_crop_hash(self):
        raw = self._bound_raw()
        inner = raw["training_gain_source_refinement"]["regions"][
            "expanded_gain.inner.speed"
        ]
        inner["box"] = [301, 820, 421, 890]
        raw["regions"]["expanded_gain.inner.speed"]["box"] = list(inner["box"])

        checked = validate_training_gain_refinement(raw, self.pane)

        self.assertEqual(checked["status"], "rejected")
        self.assertIn("declared role box", checked["reason"])

    def test_cached_refinement_rejects_pane_with_different_pixels(self):
        raw = self._bound_raw()
        changed = self.pane.copy()
        changed.putpixel((310, 840), (255, 1, 1))

        checked = validate_training_gain_refinement(raw, changed)

        self.assertEqual(checked["status"], "rejected")
        self.assertIn("gameplay_pixels_changed", checked["reason"])

    def test_cached_refinement_accepts_valid_rejected_ocr_as_diagnostic_only(self):
        raw = self._bound_raw(recognize=lambda crops: [("+36", 0.94), ("+", 0.20)])

        checked = validate_training_gain_refinement(raw, self.pane)

        self.assertEqual(checked["status"], "validated")
        self.assertIsNone(checked["regions"]["expanded_gain.outer.speed"]["amount"])


if __name__ == "__main__":
    unittest.main()
