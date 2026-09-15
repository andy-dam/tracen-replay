import unittest

from tracen_replay.crop_provenance import (
    collect_gain_candidates,
    resolve_gain_candidates,
    resolve_gain_regions,
    source_geometry_overlaps,
    source_amounts,
    source_signed_candidates,
)


def observation(text, confidence, box, **extra):
    value = dict(text=text, confidence=confidence, box=list(box))
    value.update(extra)
    return value


class CropProvenanceTests(unittest.TestCase):
    def test_result_crop_is_retained_as_diagnostic_and_cannot_override_badge(self):
        regions = {
            "gain.skill_points": observation("+7", 99.518, (696, 950, 812, 1008)),
            "wide_gain.skill_points": observation("+71", 85.831, (646, 930, 858, 1027)),
            "result.skill_points": observation("+72", 99.016, (708, 952, 810, 990)),
        }

        result = resolve_gain_regions(regions, "skill_points")

        self.assertEqual(result["canonical_amount"], 7)
        self.assertEqual(result["candidate_amounts"], [7, 71, 72])
        diagnostic = next(item for item in result["candidates"]
                          if item["region"] == "result.skill_points")
        self.assertFalse(diagnostic["canonical_eligible"])
        self.assertEqual(diagnostic["source_role"], "result_crop_diagnostic_excluded")

    def test_wide_geometry_resolves_a_clipped_one_digit_prefix(self):
        regions = {
            "gain.speed": observation("+1", 99.742, (300, 832, 414, 890)),
            "wide_gain.speed": observation("+12", 92.219, (250, 812, 462, 909)),
        }

        result = resolve_gain_regions(regions, "speed")

        self.assertEqual(result["canonical_amount"], 12)
        self.assertEqual(result["conflict_state"], "resolved_broader_prefix")
        self.assertEqual(result["canonical_basis"],
                         "source_crop_family_geometry_prefix_resolution")

    def test_expanded_geometry_accepts_a_signed_digit_prefix_with_noise(self):
        regions = {
            "gain.speed": observation("+1", 99.782, (300, 832, 414, 890)),
            "expanded_gain.speed": observation("+11c", 84.735, (215, 790, 600, 930)),
        }

        result = resolve_gain_regions(regions, "speed")

        self.assertEqual(result["canonical_amount"], 11)
        expanded = next(item for item in result["candidates"]
                        if item["region"] == "expanded_gain.speed")
        self.assertEqual(expanded["normalization"], "signed_amount_prefix")
        self.assertEqual(expanded["suffix"], "c")

    def test_low_quality_wide_reading_does_not_displace_complete_tight_badge(self):
        regions = {
            "gain.skill_points": observation("+7", 99.0, (696, 950, 812, 1008)),
            "wide_gain.skill_points": observation("+71", 85.0, (646, 930, 858, 1027)),
        }

        result = resolve_gain_regions(regions, "skill_points")

        self.assertEqual(result["canonical_amount"], 7)
        self.assertEqual(result["conflict_state"], "resolved_same_amount_across_source_crops")

    def test_nonprefix_amount_conflict_is_unresolved(self):
        regions = {
            "gain.speed": observation("+7", 99.0, (300, 832, 414, 890)),
            "wide_gain.speed": observation("+9", 99.0, (250, 812, 462, 909)),
        }

        result = resolve_gain_regions(regions, "speed")

        self.assertIsNone(result["canonical_amount"])
        self.assertEqual(result["candidate_amounts"], [7, 9])
        self.assertEqual(result["conflict_state"],
                         "unresolved_nonprefix_or_equal_geometry_conflict")

    def test_relocated_wide_candidate_does_not_promote_by_area_alone(self):
        regions = {
            "gain.speed": observation("+1", 99.0, (300, 832, 414, 890)),
            "wide_gain.speed": observation("+12", 99.0, (20, 20, 232, 117)),
        }

        result = resolve_gain_regions(regions, "speed")

        self.assertIsNone(result["canonical_amount"])
        self.assertEqual(result["conflict_state"],
                         "unresolved_invalid_crop_geometry")

    def test_explicitly_excluded_amount_role_stays_diagnostic(self):
        regions = {
            "gain.speed": observation(
                "+12", 99.0, (300, 832, 414, 890),
                role="state_or_balance_text_excluded", input_eligible=True,
            ),
        }

        result = resolve_gain_regions(regions, "speed")

        self.assertIsNone(result["canonical_amount"])
        self.assertFalse(result["candidates"][0]["canonical_eligible"])
        self.assertEqual(result["candidate_amounts"], [12])

    def test_unmarked_merged_expression_is_preserved_without_amount(self):
        regions = {
            "wide_gain.speed": observation(
                "4+1", 99.0, (250, 812, 462, 909),
                role="unmarked_amount_candidate_excluded", input_eligible=False,
            ),
        }

        candidates = collect_gain_candidates(regions, "speed")

        self.assertEqual(len(candidates), 1)
        self.assertIsNone(candidates[0]["amount"])
        self.assertFalse(candidates[0]["canonical_eligible"])
        self.assertEqual(candidates[0]["raw_text"], "4+1")

    def test_multiple_broad_crops_agreeing_on_longer_prefix_resolve_once(self):
        candidates = [
            {
                "region": "gain.speed", "crop_family": "gain", "raw_text": "+1",
                "confidence": 99, "box": [300, 832, 414, 890],
                "source_role": "amount_crop_candidate", "input_eligible": True,
                "canonical_eligible": True, "amount": 1,
            },
            {
                "region": "wide_gain.speed", "crop_family": "wide_gain", "raw_text": "+12",
                "confidence": 92, "box": [250, 812, 462, 909],
                "source_role": "amount_crop_candidate", "input_eligible": True,
                "canonical_eligible": True, "amount": 12,
            },
            {
                "region": "expanded_gain.speed", "crop_family": "expanded_gain", "raw_text": "+12c",
                "confidence": 84, "box": [215, 790, 600, 930],
                "source_role": "amount_crop_candidate", "input_eligible": True,
                "canonical_eligible": True, "amount": 12,
            },
        ]

        result = resolve_gain_candidates(candidates)

        self.assertEqual(result["canonical_amount"], 12)
        self.assertEqual(result["conflict_state"], "resolved_broader_prefix")
        self.assertEqual(result["canonical_candidate"]["crop_family"], "expanded_gain")

    def test_transaction_alternatives_exclude_result_and_low_quality_raw_digits(self):
        regions = {
            "gain.speed": observation("+1", 99.0, (300, 832, 414, 890)),
            "wide_gain.speed": observation("+71", 85.0, (250, 812, 462, 909)),
            "expanded_gain.speed": observation("+11c", 84.0, (215, 790, 600, 930)),
            "result.speed": observation("+72", 99.0, (322, 834, 448, 876)),
        }
        result = resolve_gain_regions(regions, "speed")

        self.assertEqual(source_amounts(result["candidates"], result), [1, 11])

    def test_scaled_crop_prefix_consensus_remains_supported(self):
        regions = {
            "scaled_gain_0.speed": observation("+1", 99.0, (250, 800, 515, 925)),
            "scaled_gain_1.speed": observation("+11", 99.0, (250, 800, 515, 925)),
            "scaled_gain_2.speed": observation("+11", 99.0, (250, 800, 600, 920)),
        }
        result = resolve_gain_regions(regions, "speed")

        self.assertEqual(result["canonical_amount"], 11)
        self.assertEqual(result["conflict_state"], "resolved_same_family_prefix")

    def test_signed_result_overlay_is_exposed_only_when_requested(self):
        candidates = collect_gain_candidates(
            {
                "gain.wit": observation(
                    "+6", 99.4, (498, 950, 610, 1008),
                ),
                "result.wit": observation(
                    "+65", 98.9, (518, 952, 644, 994),
                ),
            },
            "wit",
        )

        self.assertEqual(
            [item["crop_family"] for item in source_signed_candidates(candidates)],
            ["gain"],
        )
        overlays = source_signed_candidates(candidates, include_result=True)
        self.assertEqual(
            {item["amount"] for item in overlays},
            {6, 65},
        )
        overlay = next(item for item in overlays if item["crop_family"] == "result")
        self.assertEqual(overlay["source_observation_role"], "signed_overlay_diagnostic")
        self.assertFalse(overlay["canonical_eligible"])

    def test_result_overlay_geometry_can_associate_with_shifted_tight_crop(self):
        self.assertTrue(
            source_geometry_overlaps(
                {"box": [518, 952, 644, 994]},
                {"box": [498, 950, 610, 1008]},
            )
        )
        self.assertFalse(
            source_geometry_overlaps(
                {"box": [20, 20, 232, 117]},
                {"box": [498, 950, 610, 1008]},
            )
        )

    def test_source_geometry_rejects_nonfinite_or_out_of_pane_boxes(self):
        valid = {"box": [518, 952, 644, 994]}
        for invalid in (
            [float("nan"), 952, 644, 994],
            [518, float("inf"), 644, 994],
            [-1, 952, 644, 994],
            [518, 952, 959, 994],
            [148, 0, 1000000, 1080],
            [518, 952, 518, 994],
        ):
            self.assertFalse(source_geometry_overlaps(valid, {"box": invalid}), invalid)

    def test_signed_source_candidate_rechecks_raw_text_before_exposing_amount(self):
        forged = {
            "region": "result.wit",
            "crop_family": "result",
            "raw_text": "+6",
            "confidence": 99.0,
            "box": [518, 952, 644, 994],
            "source_role": "result_crop_diagnostic_excluded",
            "input_eligible": False,
            "canonical_eligible": False,
            "amount": 65,
            "normalization": "signed_amount",
        }

        self.assertEqual(source_signed_candidates([forged], include_result=True), [])

    def test_signed_source_candidate_requires_region_family_and_result_role_metadata(self):
        candidates = collect_gain_candidates(
            {
                "gain.wit": observation(
                    "+6", 99.4, (498, 950, 610, 1008),
                ),
                "result.wit": observation(
                    "+65", 98.9, (518, 952, 644, 994),
                ),
            },
            "wit",
        )
        result = next(item for item in candidates if item["crop_family"] == "result")
        result["region"] = "fake.wit"
        filtered = source_signed_candidates(candidates, include_result=True)
        self.assertEqual([item["crop_family"] for item in filtered], ["gain"])
        self.assertEqual(filtered[0]["amount"], 6)

        candidates = collect_gain_candidates(
            {
                "result.wit": observation(
                    "+65", 98.9, (518, 952, 644, 994),
                    role="signed_overlay_diagnostic", input_eligible=False,
                ),
            },
            "wit",
        )
        self.assertEqual(source_signed_candidates(candidates, include_result=True), [])

    def test_signed_prefix_normalization_is_only_valid_for_expanded_family(self):
        common = {
            "confidence": 99.0,
            "box": [498, 950, 610, 1008],
            "amount": 65,
            "normalization": "signed_amount_prefix",
            "canonical_eligible": False,
        }
        for family, role, input_eligible in (
            ("gain", "amount_crop_candidate", True),
            ("wide_gain", "amount_crop_candidate", True),
            ("result", "result_crop_diagnostic_excluded", False),
        ):
            candidate = {
                **common,
                "region": f"{family}.wit",
                "crop_family": family,
                "raw_text": "+65x",
                "source_role": role,
                "input_eligible": input_eligible,
            }
            self.assertEqual(
                source_signed_candidates([candidate], include_result=True), [],
                family,
            )

        expanded = {
            **common,
            "region": "expanded_gain.wit",
            "crop_family": "expanded_gain",
            "raw_text": "+65x",
            "source_role": "amount_crop_candidate",
            "input_eligible": True,
        }
        self.assertEqual(
            [item["amount"] for item in source_signed_candidates([expanded])],
            [65],
        )


if __name__ == "__main__":
    unittest.main()
