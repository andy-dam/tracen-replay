from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from tests.test_gameplay import workspace_temp

from PIL import Image, ImageDraw

from tracen_replay.skill_menu_observations import (
    SCHEMA,
    SkillMenuSourceError,
    adapt_skill_menu_frame,
    build_skill_menu_observations,
    detect_skill_selection_marker,
)


BASE = Path(".local/full-recording/independent-02/initial-baseline")


def _line(text, box, confidence=99):
    return {"text": text, "confidence": confidence, "box": list(box)}


def _raw(*lines, timestamp=100, evidence="gameplay/frame.png", **extra):
    return {
        "header": "Learn",
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "lines": list(lines),
        "current_grid": False,
        "result_grid": False,
        **extra,
    }


def _card_frame(name="Example Skill", cost=40, *, status="available", timestamp=100,
                evidence=None, selected=False, points=None):
    title = _line(name, (360, 450, 500, 478))
    control = _line("Obtained" if status == "obtained_or_selected" else str(cost),
                    (710, 500, 770, 532))
    lines = [
        _line("Learn", (153, 6, 208, 28)),
        _line("Skill Points", (525, 339, 620, 366)),
        _line(str(points if points is not None else 100), (729, 338, 773, 370)),
        title, control, _line("Confirm", (506, 896, 601, 928)),
    ]
    result = _raw(*lines, timestamp=timestamp, evidence=evidence or f"frame-{timestamp}.png")
    if selected:
        result["skill_menu_selection"] = {
            "selected_names": [name],
            "basis": "source_selected_draft_card",
            "evidence": [result["evidence"]],
        }
    return result


def _synthetic_action_image(path, *, selected=False, control_box=(710, 500, 770, 532)):
    """Create a pane with the stable card control geometry used by the parser."""

    left, top, right, bottom = control_box
    cx = left - 65
    cy = round((top + bottom) / 2 - 2)
    image = Image.new("RGB", (810, 1080), (190, 190, 196))
    draw = ImageDraw.Draw(image)
    polygon = [
        (cx, cy - 18), (cx + 18, cy - 5), (cx + 18, cy + 11),
        (cx + 10, cy + 18), (cx - 10, cy + 18),
        (cx - 18, cy + 11), (cx - 18, cy - 5),
    ]
    draw.polygon(polygon, fill=(132, 208, 37), outline=(45, 100, 20))
    if selected:
        points = []
        import math
        for index in range(10):
            angle = -math.pi / 2 + index * math.pi / 5
            radius = 9 if index % 2 == 0 else 4
            points.append((cx - 10 + radius * math.cos(angle), cy + 6 + radius * math.sin(angle)))
        draw.polygon(points, fill=(250, 252, 242))
    else:
        draw.line((cx - 7, cy, cx + 7, cy), fill=(245, 250, 235), width=3)
        draw.line((cx, cy - 7, cx, cy + 7), fill=(245, 250, 235), width=3)
    image.save(path)


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

    def test_synthetic_marker_requires_star_shape_and_same_card_control(self):
        with workspace_temp() as directory:
            selected_path = Path(directory) / "selected.png"
            available_path = Path(directory) / "available.png"
            _synthetic_action_image(selected_path, selected=True)
            _synthetic_action_image(available_path, selected=False)
            control = [710, 500, 770, 532]
            selected = detect_skill_selection_marker(selected_path, control)
            self.assertIsNotNone(selected)
            self.assertEqual(selected["method"], "source_pixel_action_star_v1")
            self.assertEqual(selected["votes"], 4)
            self.assertIsNone(detect_skill_selection_marker(available_path, control))

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

    def test_explicit_draft_is_grouped_inside_a_menu_episode_and_not_a_purchase(self):
        rows = [
            _card_frame("Alpha Skill", 40, timestamp=100),
            _card_frame("Alpha Skill", 40, timestamp=350),
            _card_frame("Alpha Skill", 40, timestamp=600, selected=True, points=60),
        ]
        result = build_skill_menu_observations(rows)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(result["observations"][0]["payload"]["visible_card_prices"], {"Alpha Skill": 40})
        payload = result["observations"][1]["payload"]
        self.assertEqual(payload["selected_draft_names"], ["Alpha Skill"])
        self.assertEqual(payload["selection_status"], "not_yet_confirmed")
        self.assertEqual(payload["skill_points_after"], 60)
        observation = result["observations"][1]
        self.assertTrue(observation["preview_only"])
        self.assertFalse(observation["committed"])
        self.assertTrue(observation["receipt_required"])
        self.assertNotIn("acquired", observation)
        self.assertNotIn("charged", observation)

    def test_balance_drop_does_not_choose_a_draft_name(self):
        rows = [
            _card_frame("Alpha Skill", 40, timestamp=100, points=100),
            _card_frame("Beta Skill", 40, timestamp=350, points=60),
        ]
        result = build_skill_menu_observations(rows)
        self.assertEqual(len(result["observations"]), 1)
        payload = result["observations"][0]["payload"]
        self.assertNotIn("selected_draft_names", payload)
        self.assertNotIn("skill_points_after", payload)

    def test_clipped_badge_requires_nearly_complete_label_in_control_geometry(self):
        raw=_card_frame("Badge Skill",40)
        price=next(line for line in raw['lines'] if line['text']=='40')
        price['text']='Obtaine'
        parsed=adapt_skill_menu_frame(raw)
        self.assertEqual(parsed['payload']['visible_precommit_obtained_names'],['Badge Skill'])
        self.assertNotIn('selected_draft_names',parsed['payload'])
        self.assertFalse(parsed['committed'])
        for invalid in ('Obtain','Obtainable','Available'):
            price['text']=invalid
            self.assertNotIn('visible_precommit_obtained_names',adapt_skill_menu_frame(raw)['payload'])

    def test_conflicting_prices_are_withheld_and_retained_as_conflict(self):
        rows = [
            _card_frame("Same Skill", 40, timestamp=100),
            _card_frame("Same Skill", 50, timestamp=350),
        ]
        payload = build_skill_menu_observations(rows)["observations"][0]["payload"]
        self.assertNotIn("Same Skill", payload.get("visible_card_prices", {}))
        self.assertEqual(payload["visible_card_price_conflicts"], {"Same Skill": [40, 50]})
        self.assertTrue(build_skill_menu_observations(rows)["observations"][0]["uncertain"])

    def test_close_ocr_name_variant_uses_repeated_source_spelling(self):
        rows = [
            _card_frame("Keen tye", 288, timestamp=100),
            _card_frame("Keen Eye", 288, timestamp=350),
            _card_frame("Keen Eye", 288, timestamp=600),
        ]
        payload = build_skill_menu_observations(rows)["observations"][0]["payload"]
        self.assertEqual(payload["visible_card_prices"], {"Keen Eye": 288})

    def test_receipt_closes_precommit_and_post_receipt_menu_is_not_promoted(self):
        rows = [
            _card_frame("Before Skill", 40, timestamp=100),
            {"screen": "skill_receipt", "source_timestamp_ms": 500,
             "evidence": "receipt.png"},
            _card_frame("After Skill", 40, timestamp=750),
        ]
        result = build_skill_menu_observations(rows)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(
            result["observations"][0]["payload"]["visible_card_prices"],
            {"Before Skill": 40},
        )
        self.assertNotIn("visible_card_prices", result["observations"][1]["payload"])
        self.assertTrue(result["observations"][1]["uncertain"])

    def test_wrong_screen_and_receipt_text_fail_closed(self):
        raw = _card_frame("Wrong Screen", 40)
        wrong = deepcopy(raw)
        wrong["screen"] = "skill_receipt"
        self.assertEqual(adapt_skill_menu_frame(wrong)["observations"], [])
        stale_result = deepcopy(raw)
        stale_result["screen"] = "skill_selection"
        stale_result["result_grid"] = True
        self.assertEqual(adapt_skill_menu_frame(stale_result)["observations"], [])
        receipt = deepcopy(raw)
        receipt["lines"].append(_line("Your trainee learned new skills!", (430, 600, 700, 640)))
        self.assertEqual(adapt_skill_menu_frame(receipt)["observations"], [])

    def test_conflicting_explicit_confirm_values_are_unknown(self):
        raw = _card_frame("Confirm Conflict", 40)
        raw["facts"] = {"confirm_available": True}
        raw["confirm_available"] = False
        result = adapt_skill_menu_frame(raw)
        self.assertNotIn("confirm_available", result["payload"])
        self.assertTrue(result["payload"]["confirm_conflict"])
        self.assertTrue(result["facts"]["confirm_available"] is None)

    def test_hash_mismatch_is_rejected_before_projection(self):
        raw = _card_frame("Hash Skill", 40)
        raw["gameplay_sha256"] = "0" * 64
        with self.assertRaisesRegex(SkillMenuSourceError, "gameplay"):
            adapt_skill_menu_frame(raw, gameplay_path=BASE / "gameplay/part-010-frame-000329.png")

    def test_source_proof_is_an_immutable_copy(self):
        raw = _card_frame("Immutable Skill", 40)
        original = deepcopy(raw)
        result = adapt_skill_menu_frame(raw)
        raw["lines"][3]["text"] = "Changed"
        self.assertEqual(result["cards"][0]["name"], "Immutable Skill")
        self.assertEqual(
            result["source_proof"]["raw_sha256"],
            hashlib.sha256(json.dumps(
                original, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
