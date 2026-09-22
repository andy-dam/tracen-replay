"""Tests of ``tests.test_skill_menu_observations`` that need locally preserved evidence; they run only where it is."""
from copy import deepcopy
import json
import unittest
from tracen_replay.skill_menu_observations import (
    SCHEMA,
    adapt_skill_menu_frame,
    build_skill_menu_observations,
)
from tests.test_skill_menu_observations import BASE


class SkillMenuObservationTests(unittest.TestCase):
    def test_actual_source_frame_reads_card_price_and_confirm_without_charge(self):
        path = BASE / "neural/part-010-frame-000329.json"
        gameplay = BASE / "gameplay/part-010-frame-000329.png"
        source_frame = BASE / "part-010/frames/000329.jpg"
        if not all(item.is_file() for item in (path, gameplay, source_frame)):
            self.skipTest("frozen independent-02 T053 source is not present")
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = adapt_skill_menu_frame(
            raw, gameplay_path=gameplay, source_frame_path=source_frame,
        )
        self.assertEqual(result["schema_version"], SCHEMA)
        self.assertEqual(result["screen"], "skill_selection")
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["committed"])
        self.assertTrue(result["receipt_required"])
        self.assertTrue(result["facts"]["confirm_available"])
        self.assertEqual(
            result["facts"]["visible_card_prices"],
            {"Watchful Eye": 160, "Subdued Front Runners": 117},
        )
        self.assertNotIn("skill_points_after", result["payload"])
        self.assertNotIn("charged", result)
        self.assertEqual(
            result["source_proof"]["gameplay_sha256"], raw["gameplay_sha256"],
        )
        self.assertEqual(
            result["source_proof"]["source_frame_sha256"], raw["source_frame_sha256"],
        )

    def test_actual_source_draft_keeps_counter_separate_until_explicit_selection_proof(self):
        path = BASE / "neural/part-010-frame-000375.json"
        if not path.is_file():
            self.skipTest("frozen independent-02 T053 source is not present")
        raw = json.loads(path.read_text(encoding="utf-8"))
        unselected = adapt_skill_menu_frame(raw)
        self.assertTrue(unselected["facts"]["confirm_available"])
        self.assertEqual(unselected["facts"]["displayed_skill_points"], 25)
        self.assertEqual(unselected["facts"]["selected_draft_names"], [])
        self.assertNotIn("skill_points_after", unselected["payload"])

        selected = deepcopy(raw)
        selected["skill_menu_selection"] = {
            "selected_names": ["Pace Chaser Savvy"],
            "basis": "source_selected_draft_card",
            "evidence": [raw["evidence"]],
        }
        result = adapt_skill_menu_frame(selected)
        self.assertEqual(result["payload"]["selected_draft_names"], ["Pace Chaser Savvy"])
        self.assertEqual(result["payload"]["selection_status"], "not_yet_confirmed")
        self.assertTrue(result["payload"]["confirm_available"])
        self.assertEqual(result["payload"]["skill_points_after"], 25)
        self.assertEqual(result["facts"]["skill_points_semantics"],
                         "displayed_menu_counter_after_explicit_draft")

    def test_actual_source_pixel_feedback_binds_draft_to_same_card(self):
        path = BASE / "neural/part-010-frame-000375.json"
        gameplay = BASE / "gameplay/part-010-frame-000375.png"
        source_frame = BASE / "part-010/frames/000375.jpg"
        if not all(item.is_file() for item in (path, gameplay, source_frame)):
            self.skipTest("frozen independent-02 T053 source is not present")
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = adapt_skill_menu_frame(
            raw, gameplay_path=gameplay, source_frame_path=source_frame,
        )
        self.assertEqual(result["payload"]["selected_draft_names"], ["Pace Chaser Savvy"])
        self.assertEqual(result["payload"]["selection_status"], "not_yet_confirmed")
        self.assertEqual(result["payload"]["skill_points_after"], 25)
        proof = result["payload"]["selected_draft_card_proof"]
        self.assertEqual([item["name"] for item in proof], ["Pace Chaser Savvy"])
        self.assertEqual(proof[0]["marker"]["method"], "source_pixel_action_star_v1")
        self.assertEqual(proof[0]["marker"]["coordinate_space"], "gameplay_crop")
        self.assertEqual(proof[0]["marker"]["votes"], 4)
        self.assertEqual(proof[0]["marker"]["box"], [633, 512, 648, 528])
        self.assertNotIn("charged", result["payload"])
        self.assertNotIn("acquired", result["payload"])

    def test_actual_cursor_animation_is_not_a_selection_marker(self):
        path = BASE / "neural/part-010-frame-000374.json"
        gameplay = BASE / "gameplay/part-010-frame-000374.png"
        if not all(item.is_file() for item in (path, gameplay)):
            self.skipTest("frozen independent-02 T053 source is not present")
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = adapt_skill_menu_frame(raw, gameplay_path=gameplay)
        self.assertNotIn("selected_draft_names", result["payload"])
        self.assertNotIn("selected_draft_card_proof", result["payload"])
        self.assertEqual(result["facts"]["selected_draft_names"], [])


    def test_normal_source_builder_resolves_gameplay_evidence_without_amount_join(self):
        paths = [
            BASE / "neural/part-010-frame-000374.json",
            BASE / "neural/part-010-frame-000375.json",
        ]
        if not all(path.is_file() for path in paths):
            self.skipTest("frozen independent-02 T053 source is not present")
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        result = build_skill_menu_observations(rows, source_root=BASE)
        self.assertEqual(len(result["observations"]), 2)
        payload = result["observations"][1]["payload"]
        self.assertEqual(payload["selected_draft_names"], ["Pace Chaser Savvy"])
        self.assertEqual(payload["skill_points_after"], 25)
        self.assertEqual(
            payload["selected_draft_card_proof"][0]["name"], "Pace Chaser Savvy",
        )

    def test_preview_available_and_post_receipt_never_promote_selection(self):
        preview_path = BASE / "neural/part-010-frame-000329.json"
        preview_gameplay = BASE / "gameplay/part-010-frame-000329.png"
        receipt_path = BASE / "neural/part-010-frame-000383.json"
        receipt_gameplay = BASE / "gameplay/part-010-frame-000383.png"
        if not all(item.is_file() for item in (preview_path, preview_gameplay, receipt_path, receipt_gameplay)):
            self.skipTest("frozen independent-02 T053 source is not present")
        preview = adapt_skill_menu_frame(
            json.loads(preview_path.read_text(encoding="utf-8")),
            gameplay_path=preview_gameplay,
        )
        self.assertNotIn("selected_draft_names", preview["payload"])
        receipt = adapt_skill_menu_frame(
            json.loads(receipt_path.read_text(encoding="utf-8")),
            gameplay_path=receipt_gameplay,
        )
        self.assertEqual(receipt["observations"], [])
        self.assertNotIn("payload", receipt)

    def test_actual_precommit_episode_collects_only_visible_obtained_statuses(self):
        paths = [
            BASE / f"neural/part-010-frame-{frame:06d}.json"
            for frame in (329, 333, 335, 337, 340, 342, 343, 347, 349, 353, 356)
        ]
        if not all(path.is_file() for path in paths):
            self.skipTest("frozen independent-02 T053 source is not present")
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        result = build_skill_menu_observations(rows)
        self.assertEqual(len(result["observations"]), 1)
        payload = result["observations"][0]["payload"]
        self.assertEqual(
            payload["visible_precommit_obtained_names"],
            [
                "Keen Eye", "Watchful Eye", "Subdued Front Runners", "Subdued Late Surgers",
                "Hesitant Late Surgers", "Subdued End Closers",
                "Flustered End Closers", "Dominator", "Tether",
            ],
        )
        self.assertEqual(payload["visible_card_prices"]["Pace Chaser Savvy"], 99)
        self.assertEqual(payload["visible_card_prices"]["Dominator"], 288)
        self.assertEqual(payload["price_status"], "partially_visible")
        self.assertTrue(payload["confirm_available"])
        self.assertNotIn("skill_points_after", payload)
        self.assertNotIn("acquired", payload)
        self.assertNotIn("charged", payload)
