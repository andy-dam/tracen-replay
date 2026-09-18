import json
from pathlib import Path
import unittest

from tests import localdata
from tracen_replay.condition_removal_banner import (
    merge_condition_removal_effects,
    read_condition_cured_banner,
)
from tracen_replay.gameplay import effects_from_lines


def line(text, box, confidence=99.5):
    return {"text": text, "box": list(box), "confidence": confidence}


def banner_lines(name="Night Owl", *, heading="CONDITIONCURED!", heading_box=None,
                 name_box=None, name_confidence=99.5):
    return [
        line(heading, heading_box or (392, 605, 711, 640)),
        line(name, name_box or (485, 650, 613, 690), name_confidence),
    ]


class ConditionRemovalBannerTests(unittest.TestCase):

    def test_banner_accepts_arbitrary_name_without_catalog(self):
        result = read_condition_cured_banner(
            banner_lines("An Unlisted Condition"),
            source_timestamp_ms=100,
            evidence="gameplay/frame.png",
        )
        self.assertEqual(result["name"], "An Unlisted Condition")
        self.assertEqual(result["observation_basis"], "visible_condition_cured_banner")

    def test_heading_and_name_geometry_are_required(self):
        self.assertIsNone(read_condition_cured_banner(banner_lines()[:1]))
        self.assertIsNone(read_condition_cured_banner(
            banner_lines(name_box=(485, 750, 613, 790))))
        self.assertIsNone(read_condition_cured_banner(
            banner_lines(heading_box=(392, 190, 711, 225))))
        self.assertIsNone(read_condition_cured_banner(
            banner_lines(name_confidence=96.9)))

    def test_unrelated_heading_or_narrative_does_not_promote(self):
        self.assertIsNone(read_condition_cured_banner([
            line("CONDITION CURED!", (350, 600, 700, 635)),
            line("Recovered from Night Owl.", (315, 829, 650, 861)),
        ]))
        self.assertIsNone(read_condition_cured_banner([
            line("Condition cured during training", (350, 605, 700, 640)),
            line("Night Owl", (485, 650, 613, 690)),
        ]))
        self.assertIsNone(read_condition_cured_banner([
            line("CONDITIONCURED! Night Owl", (250, 605, 760, 640)),
        ]))

    def test_truncated_name_does_not_promote(self):
        self.assertIsNone(read_condition_cured_banner(
            banner_lines("Night Owl...")))
        self.assertIsNone(read_condition_cured_banner([
            line("CONDITIONCURED!", (392, 605, 711, 640)),
            line("Recovered from Night ", (485, 650, 613, 690)),
        ]))
        self.assertIsNone(read_condition_cured_banner(
            banner_lines(name_box=(290, 650, 613, 690))))

    def test_complete_receipt_and_banner_are_one_effect_with_both_proofs(self):
        lines = banner_lines("Night Owl") + [
            line("Energy recovered by 20.", (316, 807, 553, 837)),
            line("Recovered from Night Owl.", (315, 829, 650, 861)),
        ]
        receipt_effects = effects_from_lines(lines)
        banner = read_condition_cured_banner(
            lines, source_timestamp_ms=231000, evidence="gameplay/frame.png")
        merged = merge_condition_removal_effects(receipt_effects, banner)
        removals = [effect for effect in merged if effect["kind"] == "condition_removed"]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0]["name"], "Night Owl")
        self.assertEqual(removals[0]["raw_text"], "Recovered from Night Owl.")
        self.assertEqual(removals[0]["source_timestamp_ms"], 231000)
        self.assertEqual(removals[0]["evidence"], "gameplay/frame.png")
        self.assertEqual(removals[0]["source_proof"]["complete_receipt"][0]["text"],
                         "Recovered from Night Owl.")
        self.assertEqual(removals[0]["condition_banner_proof"]["condition_name"]["text"],
                         "Night Owl")
        self.assertEqual([effect["kind"] for effect in merged],
                         ["energy_change", "condition_removed"])

    def test_duplicate_receipt_rows_are_deduplicated_without_losing_raw_effect(self):
        lines = banner_lines("Night Owl") + [line("Recovered from Night Owl.",
                                                     (315, 829, 650, 861))]
        effect = effects_from_lines(lines)[0]
        banner = read_condition_cured_banner(lines)
        merged = merge_condition_removal_effects([effect, dict(effect)], banner)
        removals = [row for row in merged if row["kind"] == "condition_removed"]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0]["same_frame_effects"][0]["raw_text"],
                         "Recovered from Night Owl.")

    def test_conflicting_receipt_and_banner_names_abstain_as_one_conflict(self):
        receipt = {
            "kind": "condition_removed",
            "name": "Practice Poor",
            "raw_text": "Recovered from Practice Poor.",
        }
        banner = read_condition_cured_banner(banner_lines("Night Owl"),
                                             source_timestamp_ms=231000,
                                             evidence="gameplay/frame.png")
        merged = merge_condition_removal_effects([receipt], banner)
        self.assertEqual([row["kind"] for row in merged],
                         ["condition_removal_conflict"])
        self.assertEqual(set(merged[0]["names"]), {"Practice Poor", "Night Owl"})
        self.assertEqual(merged[0]["source_proof"]["receipt_effects"][0]["name"],
                         "Practice Poor")
        self.assertEqual(merged[0]["source_proof"]["banner"]["name"], "Night Owl")

    def test_reader_does_not_mutate_effects_when_banner_is_absent(self):
        effects = [{"kind": "energy_change", "amount": 20}]
        merged = merge_condition_removal_effects(effects, None)
        self.assertEqual(merged, effects)
        self.assertIsNot(merged[0], effects[0])


if __name__ == "__main__":
    unittest.main()
