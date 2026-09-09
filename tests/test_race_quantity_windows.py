import unittest

from PIL import Image, ImageEnhance

from tracen_replay.race_quantity_refinement import (
    FIXED_QUANTITY_CROP_VARIANT_POLICY,
    FIXED_QUANTITY_WINDOW_EARLY_RELATIVE_BOX,
    FIXED_QUANTITY_WINDOW_MID_RELATIVE_BOX,
    SLOT_BY_ID,
    _fixed_quantity_variant,
    _fixed_quantity_window_crop_box,
    _quantity_preprocessed_crop,
    _source_crop_variants,
    _source_quantity_line_proof,
    _variant_role,
    aggregate_slot,
)


class RaceQuantityWindowTests(unittest.TestCase):
    def _raw_with_quantity_line(self):
        return {
            "lines": [
                {
                    "text": "x400",
                    "confidence": 99.8,
                    "box": [425, 699, 489, 727],
                },
            ],
        }

    def _observation(self, timestamp, text, quantity, confidence, *, role, proof):
        coverage = (
            "fixed_layout_leading_digit_safe"
            if role == "broad"
            else "focused_may_clip_leading_digit"
        )
        proof = {
            "method": "source_quantity_line_containment_or_fixed_layout",
            "coverage": coverage,
            **proof,
        }
        return {
            "timestamp_ms": timestamp,
            "text": text,
            "quantity": quantity,
            "confidence": confidence,
            "crop_variant": "quantity_window_early" if role == "broad" else "quantity_window_mid",
            "crop_variant_role": role,
            "quantity_coverage": coverage,
            "quantity_coverage_proof": proof,
        }

    def test_early_window_has_containment_proof_but_mid_window_is_focused(self):
        spec = SLOT_BY_ID["items-1"]
        raw = self._raw_with_quantity_line()
        early = _fixed_quantity_window_crop_box(
            spec["source_box"], FIXED_QUANTITY_WINDOW_EARLY_RELATIVE_BOX,
        )
        mid = _fixed_quantity_window_crop_box(
            spec["source_box"], FIXED_QUANTITY_WINDOW_MID_RELATIVE_BOX,
        )

        early_variant = _fixed_quantity_variant(
            raw, spec, "quantity_window_early", early,
            canonical_source_box=spec["source_box"],
        )
        mid_variant = _fixed_quantity_variant(
            raw, spec, "quantity_window_mid", mid,
            canonical_source_box=spec["source_box"],
        )

        self.assertEqual(early_variant["role"], "broad")
        self.assertTrue(early_variant["quantity_coverage_proof"]["leading_digit_safe"])
        self.assertEqual(early_variant["quantity_coverage_proof"]["source_line_status"], "observed")
        self.assertEqual(mid_variant["role"], "focused")
        self.assertFalse(mid_variant["quantity_coverage_proof"]["leading_digit_safe"])

    def test_broad_label_without_positive_proof_cannot_authorize_multidigit_suffix(self):
        proof = {
            "method": "source_quantity_line_containment_or_fixed_layout",
            "leading_digit_safe": False,
        }
        observation = self._observation(1000, "x400", 400, 99.0, role="broad", proof=proof)

        self.assertEqual(_variant_role(observation), "focused")
        result = aggregate_slot([observation])
        self.assertEqual(result["status"], "no_high_confidence_consensus")
        self.assertEqual(result["frame_observations"][0]["status"], "clipping_risk")

    def test_broad_and_focused_conflict_abstains_even_when_focused_matches(self):
        broad_proof = {"leading_digit_safe": True}
        observations = [
            self._observation(1000, "x400", 400, 99.0, role="broad", proof=broad_proof),
            self._observation(1000, "x1", 1, 99.2, role="focused", proof={"leading_digit_safe": False}),
            self._observation(1250, "x400", 400, 99.0, role="broad", proof=broad_proof),
            self._observation(1250, "x1", 1, 99.2, role="focused", proof={"leading_digit_safe": False}),
        ]

        result = aggregate_slot(observations)
        self.assertEqual(result["status"], "conflict")
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["conflicts"][0]["quantities"], [1, 400])

    def test_correlated_views_need_two_distinct_source_timestamps(self):
        proof = {"leading_digit_safe": True}
        one_frame = [
            self._observation(1000, "x400", 400, 99.0, role="broad", proof=proof),
            self._observation(1000, "x400", 400, 99.1, role="broad", proof=proof),
        ]
        result = aggregate_slot(one_frame)
        self.assertEqual(result["status"], "insufficient_distinct_timestamps")
        self.assertIsNone(result["accepted"])

    def test_fixed_policy_keeps_fallbacks_focused(self):
        variants = FIXED_QUANTITY_CROP_VARIANT_POLICY["variants"]
        self.assertEqual(variants["quantity_window_early"]["role"], "broad")
        self.assertEqual(variants["quantity_window_mid"]["role"], "focused")
        self.assertEqual(variants["fallback"]["role"], "focused")
        self.assertEqual(variants["fallback_quantity_window"]["role"], "focused")

    def test_contrast_view_is_deterministic_and_declared(self):
        crop = Image.new("RGB", (12, 8), (120, 130, 140))
        transform = {"name": "contrast", "factor": 2.0}
        actual = _quantity_preprocessed_crop(crop, transform)
        expected = ImageEnhance.Contrast(crop).enhance(2.0).convert("RGB")
        self.assertEqual(actual.size, expected.size)
        self.assertEqual(actual.tobytes(), expected.tobytes())
        self.assertEqual(
            FIXED_QUANTITY_CROP_VARIANT_POLICY["variants"]["badge_contrast20"]["preprocessing"],
            transform,
        )

    def test_malformed_quantity_like_line_still_blocks_clipped_crop(self):
        spec = SLOT_BY_ID["items-1"]
        raw = {
            "lines": [{
                # The reader saw the quantity's digits but failed to retain
                # the leading ``x``.  Its geometry is still evidence that a
                # crop beginning inside the line cannot be broad-safe.
                "text": "400",
                "confidence": 98.0,
                "box": [425, 699, 489, 727],
            }],
        }
        proof = _source_quantity_line_proof(
            raw,
            spec["source_box"],
            (280, 695, 350, 734),
            declared_safe=True,
            coverage="fixed_layout_leading_digit_safe",
        )

        self.assertEqual(proof["source_line_status"], "observed")
        self.assertFalse(proof["source_quantity_lines"][0]["parseable"])
        self.assertFalse(proof["source_quantity_lines"][0]["contained"])
        self.assertFalse(proof["leading_digit_safe"])

    def test_detector_box_cannot_establish_multidigit_completeness(self):
        spec = SLOT_BY_ID["items-2"]
        raw = {
            "lines": [{
                "text": "x400",
                "confidence": 99.0,
                "box": [565, 705, 596, 725],
            }],
        }
        variants = _source_crop_variants(
            raw,
            spec,
            quantity_policy=FIXED_QUANTITY_CROP_VARIANT_POLICY,
        )
        by_name = {variant["variant"]: variant for variant in variants}
        self.assertEqual(by_name["detector"]["role"], "focused")
        self.assertFalse(by_name["detector"]["quantity_coverage_proof"]["leading_digit_safe"])
        self.assertEqual(by_name["badge"]["role"], "broad")
        self.assertTrue(by_name["badge"]["quantity_coverage_proof"]["leading_digit_safe"])

        observations = []
        for timestamp in (1000, 1250):
            observations.append(self._observation(
                timestamp,
                "x400",
                400,
                99.0,
                role="focused",
                proof={
                    "method": "source_quantity_line_containment_or_fixed_layout",
                    "coverage": "detector_line_observed",
                    "leading_digit_safe": False,
                },
            ) | {"crop_variant": "detector"})
        result = aggregate_slot(observations)
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["frame_observations"][0]["status"], "clipping_risk")


if __name__ == "__main__":
    unittest.main()
