import copy
import unittest

from tracen_replay.preview_observations import (
    SCHEMA,
    accepted_preview_observations,
    build_preview_observations,
    parse_preview_overlay,
    produce_preview_panel_from_lines,
    preview_phase_proof,
)
from tracen_replay.vision import parse


def reading(timestamp, effects, *, option="speed", screen="training_preview",
            evidence=None, fact_key="preview_effects", context=None):
    facts = {fact_key: copy.deepcopy(effects)}
    if context is not None:
        facts["name_candidates"] = [context]
    row = {
        "source_timestamp_ms": timestamp,
        "evidence": evidence or f"frame-{timestamp}.png",
        "screen": screen,
        "facts": facts,
        "stats": {"training_preview": screen == "training_preview",
                  "preview_option": option},
        "completed_action": None,
    }
    return row


def song_modifier_source(*, marker=True, upper=True, success=False,
                         ambiguous=False):
    """Build a source-shaped current training menu for modifier tests."""

    lines = [
        {"text": "Training", "confidence": 99,
         "box": [150, 1, 228, 32]},
        {"text": "Wit Lvl 4", "confidence": 99,
         "box": [231, 169, 312, 193]},
    ]
    if marker:
        lines.extend([
            {"text": "Concert", "confidence": 99,
             "box": [184, 575, 260, 603]},
            {"text": "Bonuses", "confidence": 88.8,
             "box": [181, 592, 262, 619]},
        ])
    if upper:
        lines.extend([
            {"text": "+3", "confidence": 99,
             "box": [290, 633, 346, 672]},
            {"text": "+7", "confidence": 99,
             "box": [675, 636, 726, 670]},
            {"text": "+12", "confidence": 99,
             "box": [764, 636, 818, 668]},
        ])
        if ambiguous:
            lines.append({"text": "+8", "confidence": 99,
                          "box": [292, 620, 347, 650]})
    lines.extend([
        {"text": "+17", "confidence": 99,
         "box": [279, 665, 357, 706]},
        {"text": "+29", "confidence": 99,
         "box": [662, 669, 735, 705]},
        {"text": "+11", "confidence": 99,
         "box": [757, 670, 828, 703]},
        {"text": "Speed", "confidence": 99,
         "box": [301, 697, 361, 721]},
        {"text": "Wit", "confidence": 99,
         "box": [671, 696, 732, 720]},
        {"text": "Skill Pts", "confidence": 99,
         "box": [757, 695, 832, 720]},
        {"text": "Failure", "confidence": 99,
         "box": [735, 767, 798, 794]},
    ])
    if success:
        lines.append({"text": "SUCCESS", "confidence": 99,
                      "box": [200, 602, 547, 837]})
    return {
        "header": "Training",
        "evidence": "source/song-modifier.png",
        "current_grid": True,
        "result_grid": False,
        "lines": lines,
    }


class PreviewObservationTests(unittest.TestCase):
    def test_missing_main_row_does_not_promote_upper_modifier(self):
        for marker in (True, False):
            with self.subTest(marker=marker):
                raw = song_modifier_source(marker=marker)
                raw["lines"] = [line for line in raw["lines"]
                                if line["text"] != "+17"]
                panel = produce_preview_panel_from_lines(raw)
                self.assertIsNotNone(panel)
                main = {(item["field"], item["amount"])
                        for item in panel["effects"]
                        if item["kind"] == "stat_change"}
                self.assertNotIn(("speed", 3), main)
                self.assertEqual(main, {("wit", 29), ("skill_points", 11)})

    def test_missing_main_row_does_not_conflict_with_clear_neighbor(self):
        rows = []
        for index in range(2):
            raw = song_modifier_source()
            raw["regions"] = {}
            raw["evidence"] = f"source/preview-{index}.png"
            if index:
                raw["lines"] = [line for line in raw["lines"]
                                if line["text"] != "+17"]
            row = parse(raw)
            row.update(source_timestamp_ms=1000 + index * 250,
                       evidence=raw["evidence"])
            rows.append(row)
        observations = build_preview_observations(rows)["observations"]
        speed = [item for item in observations
                 if item["payload"].get("kind") == "stat_change"
                 and item["payload"].get("field") == "speed"]
        self.assertEqual(len(speed), 1)
        self.assertEqual(speed[0]["payload"]["amount"], 17)
        self.assertFalse(speed[0]["uncertain"])
        self.assertEqual(speed[0]["evidence"], ["source/preview-0.png"])

    def test_source_geometry_does_not_promote_result_transition_components(self):
        # The training-result reader can report result_grid=True while the
        # selected menu and Failure badge are still visible.  The upper pink
        # rows and lower orange rows are separate result components; one static
        # crossfade cannot prove that the user is still browsing.
        raw = {
            "header": "Training",
            "evidence": "training-inspection/frame-013.png",
            "result_grid": True,
            "current_grid": False,
            "regions": {
                "option": {"text": "Stamina Lvl 2", "confidence": 98,
                            "box": [220, 162, 400, 198]},
                "performance_gain.passion": {
                    "text": "+24", "confidence": 99,
                    "box": [245, 352, 319, 389]},
            },
            "lines": [
                {"text": "+15", "confidence": 99,
                 "box": [375, 670, 451, 710]},
                {"text": "+29", "confidence": 99,
                 "box": [377, 707, 452, 743]},
                {"text": "+7", "confidence": 99,
                 "box": [579, 673, 631, 709]},
                {"text": "+15", "confidence": 99,
                 "box": [566, 706, 643, 742]},
                {"text": "+12", "confidence": 99,
                 "box": [760, 672, 829, 709]},
                {"text": "+10", "confidence": 99,
                 "box": [755, 707, 831, 742]},
                {"text": "Stamina", "confidence": 99,
                 "box": [393, 737, 460, 756]},
                {"text": "Guts", "confidence": 99,
                 "box": [596, 737, 640, 757]},
                {"text": "Failure", "confidence": 99,
                 "box": [415, 785, 476, 809]},
            ],
        }
        panel = produce_preview_panel_from_lines(raw)
        self.assertIsNone(panel)
        parsed = parse_preview_overlay(raw)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])
        self.assertIsNone(preview_phase_proof(raw))

    def test_source_geometry_producer_uses_lower_row_for_partial_component_layer(self):
        # An isolated upper row is retained as transition evidence and does not
        # become a second value.  The ordinary lower row is the typed preview.
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-250.png",
            "current_grid": True,
            "result_grid": False,
            "lines": [
                {"text": "Wit Lvl 4", "confidence": 99,
                 "box": [230, 169, 319, 193]},
                {"text": "+4", "confidence": 99,
                 "box": [290, 633, 346, 672]},
                {"text": "+10", "confidence": 99,
                 "box": [279, 665, 357, 706]},
                {"text": "+4", "confidence": 99,
                 "box": [675, 636, 726, 670]},
                {"text": "+28", "confidence": 99,
                 "box": [662, 669, 735, 705]},
                {"text": "+4", "confidence": 99,
                 "box": [764, 636, 818, 668]},
                {"text": "+11", "confidence": 99,
                 "box": [757, 670, 828, 703]},
                {"text": "Stamina", "confidence": 99,
                 "box": [393, 698, 461, 719]},
                {"text": "Power", "confidence": 99,
                 "box": [491, 696, 549, 720]},
                {"text": "Guts", "confidence": 99,
                 "box": [596, 697, 640, 719]},
                {"text": "Wit", "confidence": 99,
                 "box": [671, 696, 732, 720]},
                {"text": "Failure", "confidence": 99,
                 "box": [735, 767, 798, 794]},
            ],
        }
        panel = produce_preview_panel_from_lines(raw)
        self.assertIsNotNone(panel)
        self.assertEqual(
            {(row["field"], row["amount"]) for row in panel["effects"]
             if row["kind"] == "stat_change"},
            {("speed", 10), ("wit", 28), ("skill_points", 11)},
        )

    def test_song_modifier_rows_use_dedicated_preview_channel(self):
        raw = song_modifier_source()
        panel = produce_preview_panel_from_lines(raw)
        self.assertIsNotNone(panel)
        self.assertEqual(
            {(row["field"], row["amount"])
             for row in panel["effects"] if row["kind"] == "stat_change"},
            {("speed", 17), ("wit", 29), ("skill_points", 11)},
        )
        self.assertEqual(
            {(row["field"], row["amount"])
             for row in panel["modifier_effects"]},
            {("speed", 3), ("wit", 7), ("skill_points", 12)},
        )
        self.assertTrue(all(
            row["kind"] == "song_modifier_change"
            and row["awarded"] is False
            and row["preview_geometry"]["role"] == "song_modifier_row"
            for row in panel["modifier_effects"]
        ))

        parsed = parse_preview_overlay(raw)
        self.assertTrue(parsed["preview_modifier_proven"])
        self.assertEqual(
            {(row["field"], row["amount"])
             for row in parsed["preview_modifier_effects"]},
            {("speed", 3), ("wit", 7), ("skill_points", 12)},
        )
        row = {
            "source_timestamp_ms": 100,
            "evidence": raw["evidence"],
            "screen": "training_preview",
            "completed_action": None,
            "facts": parsed,
            "stats": {"preview_option": "wit"},
        }
        report = build_preview_observations([row])
        modifier_observations = [
            item for item in report["observations"]
            if item["payload"].get("modifier") == "song"
        ]
        self.assertEqual(
            {(item["payload"]["field"], item["payload"]["amount"])
             for item in modifier_observations},
            {("speed", 3), ("wit", 7), ("skill_points", 12)},
        )
        self.assertTrue(all(
            item["payload"]["kind"] == "training_modifier_change"
            and item["phase"] == "preview"
            and item["observation_basis"] == "accepted_typed_song_modifier_preview"
            for item in modifier_observations
        ))
        # The dedicated preview channel cannot create a committed effect.
        self.assertFalse(any(
            item["payload"].get("kind") == "stat_change"
            and item["payload"].get("amount") in {3, 7, 12}
            for item in report["observations"]
        ))

    def test_geometry_references_are_not_mixed_into_source_evidence(self):
        parsed = parse_preview_overlay(song_modifier_source())
        effects = parsed["preview_overlay_effects"] + parsed["preview_modifier_effects"]
        self.assertTrue(effects)
        for effect in effects:
            self.assertTrue(all(
                not value.casefold().startswith(("line:", "region:"))
                for value in effect["source_evidence"]
            ))
            self.assertTrue(effect["source_evidence"])
            self.assertTrue(effect["preview_geometry"].get("source_references"))
        marker_geometry = parsed["preview_modifier_effects"][0]["preview_geometry"]
        self.assertIn("marker_references", marker_geometry)
        self.assertNotIn("marker_evidence", marker_geometry)

    def test_legacy_mixed_typed_evidence_is_normalized_at_parser_boundary(self):
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-legacy.png",
            "preview_panel": {
                "menu_proven": True,
                "result_proven": False,
                "basis": "accepted_current_menu_geometry",
                "option": "Speed Lvl 2",
                "evidence": ["gameplay/frame-legacy.png", "line:13"],
                "effects": [{
                    "kind": "stat_change", "field": "speed", "amount": 4,
                    "source_evidence": ["gameplay/frame-legacy.png", "line:14"],
                }],
            },
            "lines": [],
        }
        parsed = parse_preview_overlay(raw)
        self.assertTrue(parsed["preview_overlay_proven"])
        effect = parsed["preview_overlay_effects"][0]
        self.assertEqual(effect["source_evidence"], ["gameplay/frame-legacy.png"])
        self.assertEqual(
            effect["preview_geometry"]["source_references"],
            ["line:13", "line:14"],
        )
        report = build_preview_observations([reading(
            100, parsed["preview_overlay_effects"],
            evidence="gameplay/frame-legacy.png",
        )])
        self.assertEqual(report["observations"][0]["evidence"],
                         ["gameplay/frame-legacy.png"])

    def test_windows_drive_paths_remain_physical_evidence(self):
        raw = song_modifier_source()
        raw["evidence"] = r"C:\captures\frame-042.png"
        parsed = parse_preview_overlay(raw)
        self.assertTrue(parsed["preview_overlay_effects"])
        self.assertTrue(all(
            effect["source_evidence"] == [r"C:\captures\frame-042.png"]
            for effect in parsed["preview_overlay_effects"]
        ))

    def test_song_modifier_marker_is_required(self):
        parsed = parse_preview_overlay(song_modifier_source(marker=False))
        self.assertFalse(parsed["preview_modifier_proven"])
        self.assertEqual(parsed["preview_modifier_effects"], [])
        self.assertEqual(
            {(row["field"], row["amount"])
             for row in parsed["preview_overlay_effects"]},
            {("speed", 17), ("wit", 29), ("skill_points", 11)},
        )

    def test_song_modifier_requires_main_row_pair(self):
        parsed = parse_preview_overlay(song_modifier_source(upper=False))
        self.assertFalse(parsed["preview_modifier_proven"])
        self.assertEqual(parsed["preview_modifier_effects"], [])

    def test_song_modifier_success_frame_is_rejected(self):
        raw = song_modifier_source(success=True)
        self.assertIsNone(produce_preview_panel_from_lines(raw))
        parsed = parse_preview_overlay(raw)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertFalse(parsed["preview_modifier_proven"])
        self.assertEqual(parsed["preview_modifier_effects"], [])
        self.assertIsNone(preview_phase_proof(raw))

    def test_song_modifier_cannot_enter_through_ordinary_effect_channel(self):
        geometry = {
            "basis": "source_song_modifier_row_geometry",
            "role": "song_modifier_row",
            "marker": "concert_bonuses",
            "marker_evidence": ["line:2", "line:3"],
        }
        row = reading(100, [{
            "kind": "song_modifier_change", "field": "speed", "amount": 4,
            "modifier": "song", "preview_geometry": geometry,
        }])
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])
        self.assertEqual(
            result["rejected_counts"], {"song_modifier_wrong_fact_channel": 1}
        )

    def test_song_modifier_ambiguous_upper_rows_abstain(self):
        parsed = parse_preview_overlay(song_modifier_source(ambiguous=True))
        self.assertTrue(parsed["preview_modifier_proven"])
        modifiers = {
            (row["field"], row["amount"])
            for row in parsed["preview_modifier_effects"]
        }
        # The ambiguous speed column is withheld while independent, fully
        # paired columns remain useful source observations.
        self.assertNotIn(("speed", 3), modifiers)
        self.assertEqual(modifiers, {("wit", 7), ("skill_points", 12)})

    def test_source_geometry_producer_rejects_success_banner_even_with_failure_text(self):
        raw = {
            "header": "Training",
            "current_grid": False,
            "result_grid": True,
            "lines": [
                {"text": "Speed Lvl 1", "confidence": 99,
                 "box": [230, 169, 319, 193]},
                {"text": "SU", "confidence": 99,
                 "box": [200, 602, 547, 837]},
                {"text": "+15", "confidence": 99,
                 "box": [206, 790, 507, 934]},
                {"text": "Failure", "confidence": 99,
                 "box": [415, 785, 476, 809]},
            ],
        }
        self.assertIsNone(produce_preview_panel_from_lines(raw))

    def test_parse_generates_typed_panel_from_normal_detector_lines(self):
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-source.png",
            "current_grid": True,
            "result_grid": False,
            "lines": [
                {"text": "Wit Lvl 4", "confidence": 99,
                 "box": [230, 169, 319, 193]},
                {"text": "+28", "confidence": 99,
                 "box": [662, 669, 735, 705]},
                {"text": "Wit", "confidence": 99,
                 "box": [671, 696, 732, 720]},
                {"text": "Failure", "confidence": 99,
                 "box": [735, 767, 798, 794]},
            ],
        }
        parsed = parse_preview_overlay(raw)
        self.assertTrue(parsed["preview_overlay_proven"])
        self.assertEqual(
            [(item["field"], item["amount"])
             for item in parsed["preview_overlay_effects"]],
            [("wit", 28)],
        )
        self.assertEqual(parsed["preview_overlay_effects"][0]["source_semantics"],
                         "typed_training_preview_panel")

    def test_vision_parse_carries_typed_preview_overlay_facts(self):
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-vision.png",
            "current_grid": True,
            "result_grid": False,
            "regions": {},
            "lines": [
                {"text": "Guts Lvl 1", "confidence": 99, "box": [230, 169, 319, 193]},
                {"text": "+2", "confidence": 99, "box": [565, 670, 642, 704]},
                {"text": "Guts", "confidence": 99, "box": [594, 698, 640, 719]},
            ],
        }
        facts = parse(raw)["facts"]
        self.assertTrue(facts["preview_overlay_proven"])
        self.assertEqual(
            [(effect["field"], effect["amount"])
             for effect in facts["preview_overlay_effects"]],
            [("guts", 2)],
        )
        self.assertEqual(facts["preview_option"], "guts")

    def test_numeric_parser_reads_labeled_stat_columns_from_training_preview(self):
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-133.png",
            "current_grid": True,
            "result_grid": False,
            "lines": [
                {"text": "Guts Lvl 1", "confidence": 99, "box": [230, 169, 319, 193]},
                {"text": "+2", "confidence": 99, "box": [290, 668, 346, 706]},
                {"text": "+2", "confidence": 99, "box": [481, 671, 546, 704]},
                {"text": "+10", "confidence": 99, "box": [565, 670, 642, 704]},
                {"text": "+4", "confidence": 99, "box": [767, 670, 821, 704]},
                {"text": "Speed", "confidence": 99, "box": [299, 697, 361, 721]},
                {"text": "Power", "confidence": 99, "box": [485, 695, 550, 720]},
                {"text": "Guts", "confidence": 99, "box": [594, 698, 640, 719]},
                {"text": "Skill Pts", "confidence": 99, "box": [755, 694, 831, 719]},
            ],
        }
        parsed = parse_preview_overlay(raw)
        self.assertTrue(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_option"], "guts")
        self.assertEqual(
            {(row["field"], row["amount"]) for row in parsed["preview_overlay_effects"]},
            {("speed", 2), ("power", 2), ("guts", 10), ("skill_points", 4)},
        )
        for effect in parsed["preview_overlay_effects"]:
            self.assertEqual(effect["phase"], "preview")
            self.assertFalse(effect["awarded"])
            self.assertEqual(effect["source_evidence"], ["gameplay/frame-133.png"])

    def test_numeric_parser_rejects_fading_overlay_inside_result_frame(self):
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-194.png",
            "current_grid": False,
            "result_grid": False,
            "lines": [
                {"text": "Wit Lvl 1", "confidence": 97, "box": [232, 171, 304, 192]},
                {"text": "+5", "confidence": 99, "box": [292, 721, 344, 754]},
                {"text": "+7", "confidence": 99, "box": [672, 719, 732, 753]},
                {"text": "+7", "confidence": 99, "box": [766, 725, 816, 753]},
                # Only one stat label survives the animation fade.  The
                # fixed column geometry still proves the other two fields.
                {"text": "Power", "confidence": 99, "box": [494, 749, 545, 770]},
            ],
        }
        parsed = parse_preview_overlay(raw)
        self.assertEqual(parsed["preview_option"], "wit")
        self.assertEqual(parsed["preview_overlay_effects"], [])
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertIn("missing_positive_menu_geometry", parsed["rejected_counts"])

    def test_result_reader_overlay_is_rejected_without_menu_proof(self):
        raw = {
            "header": "Training",
            "evidence": "training-inspection/frame-012.png",
            "result_grid": True,
            "current_grid": False,
            "regions": {
                "option": {"text": "Speed Lvl 2", "confidence": 98,
                            "box": [230, 165, 365, 194]},
                "gain.speed": {"text": "+19", "confidence": 99,
                                "box": [300, 832, 414, 890]},
                "gain.power": {"text": "+9", "confidence": 99,
                                "box": [696, 832, 812, 890]},
                "gain.skill_points": {"text": "+7", "confidence": 99,
                                       "box": [696, 950, 812, 1008]},
            },
            "lines": [],
        }
        parsed = parse_preview_overlay(raw)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])
        self.assertIsNone(preview_phase_proof(raw))

    def test_typed_menu_panel_requires_current_menu_metadata(self):
        raw = {
            "header": "Training",
            "evidence": "training-inspection/menu-frame.png",
            "result_grid": False,
            "current_grid": True,
            "preview_panel": {
                "menu_proven": True,
                "result_proven": False,
                "basis": "accepted_current_menu_geometry",
                "option": "Wit Lvl 3",
                "evidence": ["training-inspection/menu-frame.png"],
                "effects": [
                    {"kind": "stat_change", "field": "speed", "amount": 4,
                     "box": [290, 665, 350, 705]},
                    {"kind": "stat_change", "field": "wit", "amount": 18,
                     "box": [660, 665, 735, 705]},
                    {"kind": "performance_change", "field": "passion", "amount": 8,
                     "box": [190, 345, 315, 390]},
                ],
            },
            "lines": [],
        }
        parsed = parse_preview_overlay(raw)
        self.assertEqual(parsed["preview_option"], "wit")
        self.assertEqual(
            {(row["field"], row["amount"]) for row in parsed["preview_overlay_effects"]},
            {("speed", 4), ("wit", 18), ("passion", 8)},
        )
        self.assertEqual(preview_phase_proof(raw)["basis"], "accepted_current_menu_geometry")

        row = {
            "source_timestamp_ms": 100,
            "evidence": raw["evidence"],
            "screen": "training_preview",
            "completed_action": None,
            "facts": parsed,
            "stats": {"preview_option": "wit"},
        }
        result = build_preview_observations([row])
        self.assertEqual(
            {(item["payload"]["field"], item["payload"]["amount"])
             for item in result["observations"]},
            {("speed", 4), ("wit", 18), ("passion", 8)},
        )
        self.assertTrue(all(item["phase"] == "preview" for item in result["observations"]))
        self.assertTrue(all(item["observation_basis"] == "accepted_typed_preview_overlay"
                            for item in result["observations"]))

    def test_typed_menu_panel_rejects_result_marker_conflict(self):
        raw = {
            "header": "Training",
            "evidence": "training/frame-conflict.png",
            "preview_panel": {
                "menu_proven": True,
                "result_proven": True,
                "basis": "contradictory_geometry",
                "option": "Speed Lvl 1",
                "effects": [{"kind": "stat_change", "field": "speed", "amount": 5}],
            },
            "lines": [],
        }
        parsed = parse_preview_overlay(raw)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])

    def test_numeric_parser_reads_current_plus_projected_performance_rows(self):
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-390.png",
            "current_grid": True,
            "lines": [
                {"text": "Speed Lvl 2", "confidence": 99, "box": [230, 165, 365, 194]},
                {"text": "Performance", "confidence": 99, "box": [168, 262, 246, 282]},
                {"text": "Points", "confidence": 99, "box": [187, 277, 231, 295]},
                {"text": "Da", "confidence": 99, "box": [158, 308, 188, 332]},
                {"text": "Pa", "confidence": 99, "box": [158, 362, 190, 389]},
                {"text": "Vo", "confidence": 99, "box": [159, 418, 188, 443]},
                {"text": "Vi", "confidence": 99, "box": [161, 473, 185, 496]},
                {"text": "Co", "confidence": 99, "box": [161, 530, 187, 552]},
                {"text": "32+13", "confidence": 99, "box": [207, 348, 315, 389]},
                {"text": "84+15", "confidence": 99, "box": [209, 405, 316, 443]},
                {"text": "100+15", "confidence": 99, "box": [194, 515, 314, 552]},
            ],
        }
        parsed = parse_preview_overlay(raw)
        self.assertEqual(
            {(row["field"], row["amount"]) for row in parsed["preview_overlay_effects"]
             if row["kind"] == "performance_change"},
            {("passion", 13), ("vocal", 15), ("composure", 15)},
        )

    def test_numeric_parser_accepts_typed_projected_performance_crop(self):
        parsed = parse_preview_overlay({
            "header": "Training",
            "evidence": "lesson/frame-01.png",
            "regions": {
                "option": {"text": "Wit Lvl 3", "confidence": 98,
                            "box": [230, 165, 365, 194]},
                "projected_performance.dance": {
                    "text": "+12", "confidence": 99, "box": [392, 846, 425, 876]},
                "projected_performance.visual": {
                    "text": "+4", "confidence": 99, "box": [724, 846, 757, 876]},
            },
            "lines": [],
        })
        self.assertEqual(
            {(row["field"], row["amount"]) for row in parsed["preview_overlay_effects"]},
            {("dance", 12), ("visual", 4)},
        )

    def test_numeric_parser_rejects_unproved_plus_and_malformed_shapes(self):
        raw = {
            "header": "Training",
            "evidence": "gameplay/frame-bad.png",
            "lines": [
                {"text": "Speed Lvl 1", "confidence": 99, "box": [230, 165, 365, 194]},
                {"text": "+8", "confidence": 99, "box": [20, 300, 70, 340]},
                {"text": "5+", "confidence": 99, "box": [310, 668, 360, 706]},
                {"text": "+3 +10", "confidence": 99, "box": [500, 668, 645, 706]},
            ],
        }
        parsed = parse_preview_overlay(raw)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])

    def test_current_grid_conflicting_rows_abstain_until_typed_panel_resolves_them(self):
        # A visible menu can overlap two signed rows during a scenario
        # transition.  Position alone cannot say which row is the preview;
        # accepting the nearest row would turn an applied/bonus value into a
        # false preview observation.
        raw = {
            "header": "Training",
            "evidence": "gameplay/transition-frame.png",
            "current_grid": True,
            "result_grid": False,
            "lines": [
                {"text": "Wit Lvl 4", "confidence": 99,
                 "box": [230, 169, 319, 193]},
                {"text": "+4", "confidence": 99,
                 "box": [290, 633, 346, 672]},
                {"text": "+10", "confidence": 99,
                 "box": [279, 665, 357, 706]},
                {"text": "Speed", "confidence": 99,
                 "box": [301, 697, 361, 721]},
                {"text": "+2", "confidence": 99,
                 "box": [481, 633, 546, 672]},
                {"text": "+8", "confidence": 99,
                 "box": [481, 665, 546, 706]},
                {"text": "Power", "confidence": 99,
                 "box": [485, 697, 550, 721]},
            ],
        }
        parsed = parse_preview_overlay(raw)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])
        self.assertEqual(parsed["rejected_counts"].get("conflicting_current_preview_rows"), 2)

    def test_repeated_typed_stat_and_performance_frames_are_one_preview(self):
        source = [
            reading(100, [
                {"kind": "stat_change", "field": "speed", "amount": 5,
                 "awarded": False},
                {"kind": "performance_change", "field": "visual", "amount": 7,
                 "awarded": False},
            ], evidence="frame-a.png"),
            reading(250, [
                {"kind": "stat_change", "field": "speed", "amount": 5,
                 "awarded": False},
                {"kind": "performance_change", "field": "visual", "amount": 7,
                 "awarded": False},
            ], evidence="frame-b.png"),
        ]
        original = copy.deepcopy(source)
        result = build_preview_observations(source)

        self.assertEqual(result["schema_version"], SCHEMA)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(
            [(row["payload"], row["start_ms"], row["end_ms"], row["option"])
             for row in result["observations"]],
            [
                ({"kind": "stat_change", "field": "speed", "amount": 5}, 100, 250, "speed"),
                ({"kind": "performance_change", "field": "visual", "amount": 7}, 100, 250, "speed"),
            ],
        )
        for row in result["observations"]:
            self.assertEqual(row["phase"], "preview")
            self.assertFalse(row["uncertain"])
            self.assertEqual(row["evidence"], ["frame-a.png", "frame-b.png"])
            self.assertEqual(row["observation_basis"], "accepted_typed_preview_fact")
        self.assertEqual(source, original)

    def test_browsing_option_switches_produce_distinct_occurrences(self):
        rows = [
            reading(100, [{"kind": "stat_change", "field": "speed", "amount": 5}], option="speed"),
            reading(200, [{"kind": "stat_change", "field": "speed", "amount": 5}], option="power"),
            reading(300, [{"kind": "stat_change", "field": "speed", "amount": 5}], option="speed"),
        ]
        result = build_preview_observations(rows)
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual([row["option"] for row in result["observations"]],
                         ["speed", "power", "speed"])
        self.assertEqual([row["evidence"] for row in result["observations"]],
                         [["frame-100.png"], ["frame-200.png"], ["frame-300.png"]])
        self.assertEqual(len({row["occurrence_key"] for row in result["observations"]}), 3)

    def test_known_committed_or_applied_frame_never_becomes_preview(self):
        row = reading(100, [{"kind": "stat_change", "field": "speed", "amount": 5}],
                      screen="training_result")
        row["completed_action"] = "training"
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])
        self.assertTrue(result["committed_actions_inferred"] is False)

        row = reading(200, [{"kind": "performance_change", "field": "visual", "amount": 7}],
                      screen="lesson_confirmation")
        row["phase"] = "committed"
        self.assertEqual(accepted_preview_observations([row]), [])

    def test_raw_text_and_unawarded_candidates_are_not_parsed_or_promoted(self):
        rows = [
            reading(100, [{"kind": "immediate_on_purchase", "raw_text": "Speed +5",
                           "awarded": False}]),
            reading(200, [{"raw_text": "Speed +5", "field": "speed", "amount": 5}]),
            reading(300, [{"kind": "stat_change", "field": "speed", "amount": 5,
                           "awarded": True}]),
        ]
        result = build_preview_observations(rows)
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["rejected_counts"], {
            "already_awarded_or_unknown": 1,
            "missing_typed_kind": 1,
            "untyped_purchase_projection": 1,
        })

    def test_conflicting_typed_amounts_are_ambiguous_and_not_chosen(self):
        rows = [reading(100, [
            {"kind": "stat_change", "field": "speed", "amount": 5},
            {"kind": "stat_change", "field": "speed", "amount": 6},
        ])]
        result = build_preview_observations(rows)
        self.assertEqual(result["observations"], [])
        self.assertEqual(len(result["ambiguities"]), 1)
        ambiguity = result["ambiguities"][0]
        self.assertEqual(ambiguity["reason"], "conflicting_typed_preview_values")
        self.assertEqual(ambiguity["identity"]["field"], "speed")
        self.assertEqual([item["amount"] for item in ambiguity["alternatives"]], [5, 6])
        self.assertEqual(ambiguity["source_interval_ms"], [100, 100])

    def test_gap_breaks_deduplication_even_when_payload_and_option_match(self):
        rows = [
            reading(100, [{"kind": "stat_change", "field": "speed", "amount": 5}]),
            reading(900, [{"kind": "stat_change", "field": "speed", "amount": 5}]),
        ]
        result = build_preview_observations(rows, maximum_gap_ms=500)
        self.assertEqual(len(result["observations"]), 2)

    def test_typed_offer_and_hint_require_ui_semantics(self):
        rows = [
            reading(100, [{"kind": "lesson", "name": "Group Lesson Basics"}],
                    screen="lesson_selection", option=None),
            reading(200, [{"kind": "skill_hint_change", "amount": 2}],
                    screen="lesson_selection", option=None),
            reading(300, [{"kind": "skill", "name": "Keen Eye"}],
                    screen="skill_selection", option=None),
            reading(400, [{"kind": "skill", "name": "Unqualified"}],
                    screen="training_preview", option=None),
        ]
        result = build_preview_observations(rows)
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual([row["category"] for row in result["observations"]],
                         ["purchase", "effect", "purchase"])
        self.assertEqual([row["payload"] for row in result["observations"]], [
            {"kind": "lesson", "name": "Group Lesson Basics"},
            {"kind": "skill_hint_change", "amount": 2},
            {"kind": "skill", "name": "Keen Eye"},
        ])
        self.assertEqual(result["rejected_counts"], {"unsupported_purchase_semantics": 1})

    def test_name_candidate_cannot_supply_canonical_purchase_identity(self):
        row = reading(100, [{"kind": "lesson"}], screen="lesson_selection",
                      option=None, context="Candidate-only title")
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["rejected_counts"], {"missing_offer_identity": 1})

    def test_same_frame_duplicate_fact_lists_are_coalesced(self):
        row = reading(100, [{"kind": "stat_change", "field": "speed", "amount": 5}],
                      fact_key="projected_effects")
        row["facts"]["available_effects"] = copy.deepcopy(row["facts"]["projected_effects"])
        result = build_preview_observations([row])
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["source_fact_keys"],
                         ["projected_effects", "available_effects"])

    def test_direction_is_normalized_only_when_explicit(self):
        row = reading(100, [{"kind": "stat_change", "field": "speed", "amount": 5,
                            "direction": "down"}])
        result = build_preview_observations([row])
        self.assertEqual(result["observations"][0]["payload"]["amount"], -5)


if __name__ == "__main__":
    unittest.main()
