"""Focused gameplay-card positives and ownership negatives."""

import copy
import unittest

from tracen_replay.race_runner_scope import (
    evaluate_owner_evidence,
    read_runner_facts,
    runner_scoped_stats,
)


def _card(*, runner="Oguri Cap", bib=17, stats=None):
    return {
        "source_timestamp_ms": 320500,
        "source_frame_sha256": "a" * 64,
        "evidence": "gameplay/part-002-frame-000323.png",
        "race_name": "Hopeful Stakes",
        "race_grade": "G1",
        "runner_name": runner,
        "bib_number": bib,
        "stats": stats or {
            "speed": 377,
            "stamina": 269,
            "power": 311,
            "guts": 195,
            "wit": 285,
        },
        "aptitude": {"turf": "A", "medium": "A", "pace": "A"},
        "mood": "GREAT",
        "strategy_counts": {"end": 3, "late": 7, "pace": 4, "front": 3},
        "selected_card_controls": {
            "change": {
                "visible": True,
                "enabled": True,
                "state": "bright_green_enabled",
                "gameplay_crop_region": [794, 613, 863, 645],
            },
            "navigation": {
                "left_arrow_visible": True,
                "right_arrow_visible": True,
                "runners_button_visible": True,
            },
        },
    }


class RaceRunnerScopeTests(unittest.TestCase):
    def test_v1_source_positive_emits_readable_values_but_change_is_candidate(self):
        result = read_runner_facts(_card())

        self.assertTrue(result["runner_scoped"])
        self.assertEqual(result["event"], "race_runner_attributes")
        self.assertEqual(result["runner_name"], "Oguri Cap")
        self.assertEqual(result["bib_number"], 17)
        self.assertEqual(result["stats"], {
            "speed": 377, "stamina": 269, "power": 311, "guts": 195, "wit": 285,
        })
        self.assertEqual(result["identity_status"], "visible_identity_unverified")
        self.assertFalse(result["owner_verified"])
        self.assertFalse(result["promotion"]["to_trainee"])
        self.assertEqual(
            result["selected_card_controls"]["change"]["owner_affordance_status"],
            "candidate_only",
        )
        self.assertFalse(result["excluded_auxiliary_context"]["used_for_binding"])

    def test_independent_cross_run_runner_is_also_readable_without_name_binding(self):
        row = _card(
            runner="Agnes Tachyon",
            bib=7,
            stats={"speed": 189, "stamina": 193, "power": 216, "guts": 158, "wit": 258},
        )
        result = runner_scoped_stats(row)

        self.assertEqual(result["runner_name"], "Agnes Tachyon")
        self.assertEqual(result["stats"]["speed"], 189)
        self.assertEqual(result["identity_status"], "visible_identity_unverified")
        self.assertFalse(result["promotion"]["allowed"])

    def test_missing_card_values_are_null_with_precise_visibility_limit(self):
        row = _card()
        row["stats"] = {"speed": 377, "stamina": 269, "power": None, "guts": 195}
        row["strategy_counts"] = {"end": None, "late": 7, "pace": 4, "front": 3}
        row.pop("mood")

        result = read_runner_facts(row)

        self.assertEqual(result["stats"]["power"], None)
        self.assertEqual(result["strategy_counts"]["end"], None)
        self.assertIsNone(result["mood"])
        self.assertEqual(
            result["visibility_limit"]["unknown_fields"],
            ["power", "wit", "mood", "strategy_counts.end"],
        )
        self.assertEqual(
            result["visibility_limit"]["not_visible_fields"],
            ["skill_points", "performance_points"],
        )
        self.assertFalse(result["promotion"]["to_trainee"])

    def test_name_favorite_default_and_expected_stats_never_bind_owner(self):
        row = _card()
        row.update({
            "favorite_label": "No. 1 Fav",
            "selected_card_index": 0,
            "expected_stats": copy.deepcopy(row["stats"]),
            "portrait_matches_run_context": True,
        })

        result = read_runner_facts(row)

        self.assertEqual(result["identity_status"], "visible_identity_unverified")
        self.assertFalse(result["owner_verified"])
        self.assertFalse(result["promotion"]["allowed"])
        self.assertNotIn("favorite_label", result)
        self.assertNotIn("selected_card_index", result)

    def test_result_continuity_is_unverified_owner_status(self):
        row = _card()
        row["result_corroboration"] = {
            "gameplay_only": True,
            "observed": True,
            "race_name": "Hopeful Stakes",
            "runner_name": "Oguri Cap",
            "bib_number": 17,
            "source_timestamp_ms": 323500,
        }

        result = read_runner_facts(row)

        self.assertEqual(result["identity_status"], "run_continuity_owner_unverified")
        self.assertTrue(result["result_corroboration"]["observed"])
        self.assertFalse(result["result_corroboration"]["promotes_owner"])
        self.assertFalse(result["owner_verified"])

    def test_auxiliary_profile_text_is_explicitly_ignored(self):
        row = _card()
        row["auxiliary_context"] = {
            "trainee": "[Ashen Miracle] Oguri Cap",
            "gameplay_only": False,
            "owner_evidence": {
                "kind": "direct_marker",
                "marker": "Trainee",
                "verified": True,
                "gameplay_only": False,
            },
        }

        result = read_runner_facts(row)

        self.assertEqual(result["identity_status"], "visible_identity_unverified")
        self.assertFalse(result["owner_verified"])
        self.assertNotIn("auxiliary_context", result)
        self.assertFalse(result["excluded_auxiliary_context"]["used_for_binding"])

    def test_gameplay_direct_marker_requires_two_reviewed_frames(self):
        evidence = {
            "kind": "direct_marker",
            "marker": "Own",
            "gameplay_only": True,
            "verified": True,
            "frames": [320500, 320750],
        }
        result = read_runner_facts(_card(), owner_evidence=evidence)

        self.assertEqual(result["identity_status"], "trainee_owner_verified")
        self.assertTrue(result["owner_verified"])
        self.assertTrue(result["promotion"]["to_trainee"])
        self.assertEqual(result["owner_binding"]["marker"], "Own")

        one_frame = dict(evidence, frames=[320500])
        status = evaluate_owner_evidence(one_frame)
        self.assertFalse(status["verified"])
        self.assertIn("two distinct source frames", status["reason"])

        one_observation = dict(evidence, frames=[{
            "source_timestamp_ms": 320500,
            "evidence": "gameplay/part-002-frame-000323.png",
        }])
        status = evaluate_owner_evidence(one_observation)
        self.assertEqual(status["source_frame_count"], 1)
        self.assertFalse(status["verified"])
        self.assertIn("two distinct source frames", status["reason"])

    def test_change_requires_target_and_alternate_proof_before_promotion(self):
        evidence = {
            "kind": "exclusive_control",
            "control": "Change",
            "gameplay_only": True,
            "verified": True,
            "exclusive_to_trainee_proven": True,
            "positive_frames": [320500, 320750],
            "negative_frames": [320000, 320250],
        }
        result = read_runner_facts(_card(), owner_evidence=evidence)

        self.assertEqual(result["identity_status"], "trainee_owner_verified")
        self.assertEqual(
            result["owner_binding"]["owner_affordance_status"],
            "verified_owner_exclusive",
        )

        candidate = dict(evidence, exclusive_to_trainee_proven=False)
        status = evaluate_owner_evidence(candidate)
        self.assertFalse(status["verified"])
        self.assertIn("exclusivity was not source-proven", status["reason"])

        overlap = dict(evidence, negative_frames=[{
            "source_timestamp_ms": 320500,
            "evidence": "gameplay/part-002-frame-000323.png",
        }, 321000])
        status = evaluate_owner_evidence(overlap)
        self.assertFalse(status["verified"])
        self.assertIn("frames overlap", status["reason"])

    def test_non_gameplay_marker_cannot_bind_even_if_label_says_trainee(self):
        evidence = {
            "kind": "direct_marker",
            "marker": "Trainee",
            "gameplay_only": False,
            "verified": True,
            "frames": [320500, 320750],
        }

        result = read_runner_facts(_card(), owner_evidence=evidence)

        self.assertEqual(result["identity_status"], "visible_identity_unverified")
        self.assertFalse(result["owner_verified"])
        self.assertFalse(result["promotion"]["allowed"])

    def test_outer_non_gameplay_input_blocks_even_gameplay_shaped_owner_proof(self):
        row = _card()
        row["gameplay_only"] = False
        row["owner_evidence"] = {
            "kind": "direct_marker",
            "marker": "Own",
            "gameplay_only": True,
            "verified": True,
            "frames": [320500, 320750],
        }

        result = read_runner_facts(row)

        self.assertFalse(result["gameplay_only"])
        self.assertEqual(result["evidence_scope"], "non_gameplay_rejected")
        self.assertEqual(result["identity_status"], "visible_identity_unverified")
        self.assertFalse(result["owner_verified"])

    def test_invalid_numeric_values_remain_unknown(self):
        row = _card(stats={
            "speed": "377",
            "stamina": -1,
            "power": True,
            "guts": 195,
            "wit": 285,
        })

        result = read_runner_facts(row)

        self.assertEqual(result["stats"]["speed"], None)
        self.assertEqual(result["stats"]["stamina"], None)
        self.assertEqual(result["stats"]["power"], None)
        self.assertEqual(
            result["visibility_limit"]["unknown_fields"],
            ["speed", "stamina", "power"],
        )


if __name__ == "__main__":
    unittest.main()
