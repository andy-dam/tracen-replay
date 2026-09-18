from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image

from tests import localdata
from tracen_replay.energy_popup import (
    merge_energy_popup_effects,
    read_energy_popup,
)


def line(text, box, confidence=99.5):
    return {"text": text, "box": list(box), "confidence": confidence}


def raw(lines, **kwargs):
    value = dict(
        lines=list(lines),
        regions={},
        header="Career",
        current_grid=True,
        result_grid=False,
        **kwargs,
    )
    return value


def popup_lines(amount="+20", amount_box=(469, 565, 624, 641), label_box=(479, 633, 617, 700)):
    return [line(amount, amount_box), line("Energy", label_box)]


class EnergyPopupTests(unittest.TestCase):
    def test_source_popup_is_read_as_an_energy_change(self):
        source = raw(
            popup_lines(),
            source_timestamp_ms=230750,
            evidence="gameplay/part-001-frame-000444.png",
            gameplay_sha256="a" * 64,
            source_frame_sha256="b" * 64,
        )
        result = read_energy_popup(source)
        self.assertIsNotNone(result)
        self.assertEqual(result["kind"], "energy_change")
        self.assertEqual(result["amount"], 20)
        self.assertEqual(result["raw_text"], "+20 Energy")
        self.assertEqual(result["source_timestamp_ms"], 230750)
        self.assertEqual(result["evidence"], "gameplay/part-001-frame-000444.png")
        self.assertEqual(result["source_proof"]["amount"]["text"], "+20")
        self.assertEqual(result["source_proof"]["label"]["text"], "Energy")
        self.assertEqual(result["source_proof"]["source_identity"]["source_frame_sha256"], "b" * 64)

    def test_fading_popup_with_a_visible_gap_keeps_the_same_geometry_contract(self):
        result = read_energy_popup(raw(popup_lines(
            amount_box=(460, 525, 629, 623),
            label_box=(480, 648, 615, 713),
        )))
        self.assertIsNotNone(result)
        self.assertEqual(result["amount"], 20)
        self.assertLess(
            result["source_proof"]["amount"]["box"][3],
            result["source_proof"]["label"]["box"][1],
        )


    def test_popup_survives_suppressed_lower_receipt_without_using_it(self):
        source = raw(
            popup_lines()
            + [
                line("Energy recovered by 20.", (316, 807, 553, 837), 0),
            ],
            occluded_receipt_lines=[
                {
                    "text": "Energy recovered by 20.",
                    "box": [316, 807, 553, 837],
                    "confidence": 99.955,
                    "animated_overlay_occluded": True,
                }
            ],
            receipt_overlay_evidence={"animated_overlay_boxes": [[545, 822, 565, 849]]},
        )
        result = read_energy_popup(source)
        self.assertEqual(result["amount"], 20)
        self.assertNotIn("Energy recovered by 20.", result["raw_text"])
        self.assertEqual(result["source_proof"]["amount"]["text"], "+20")

    def test_same_amount_receipt_and_popup_are_one_effect_with_both_proofs(self):
        receipt = {
            "kind": "energy_change",
            "amount": 20,
            "raw_text": "Energy recovered by 20.",
            "confidence": 99.955,
        }
        popup = read_energy_popup(raw(popup_lines(), source_timestamp_ms=230750,
                                      evidence="gameplay/frame.png"))
        merged = merge_energy_popup_effects([receipt], popup)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["raw_text"], "Energy recovered by 20.")
        self.assertEqual(merged[0]["energy_popup_proof"]["amount"]["text"], "+20")

    def test_different_amounts_are_retained_for_later_conflict_reconciliation(self):
        receipt = {
            "kind": "energy_change",
            "amount": 10,
            "raw_text": "Energy recovered by 10.",
            "confidence": 99.955,
        }
        popup = read_energy_popup(raw(popup_lines()))
        merged = merge_energy_popup_effects([receipt], popup)
        self.assertEqual([effect["amount"] for effect in merged], [10, 20])

    def test_persistent_status_and_lower_receipt_alone_do_not_form_popup(self):
        source = raw(
            [
                line("Energy", (382, 121, 441, 153)),
                line("Energy recovered by 20.", (316, 807, 553, 837)),
            ]
        )
        self.assertIsNone(read_energy_popup(source))

    def test_preview_and_menu_screens_are_not_promoted(self):
        for screen in ("training_preview", "lesson_selection", "skill_selection"):
            with self.subTest(screen=screen):
                self.assertIsNone(read_energy_popup(raw(popup_lines(), screen=screen)))

    def test_unrelated_text_requires_exact_two_role_lines_and_geometry(self):
        self.assertIsNone(read_energy_popup(raw([line("+20 Energy", (469, 565, 624, 641))])))
        self.assertIsNone(read_energy_popup(raw([line("+20", (469, 565, 624, 641)),
                                              line("Power", (479, 633, 617, 700))])))
        self.assertIsNone(read_energy_popup(raw([line("+20", (100, 565, 220, 641)),
                                              line("Energy", (110, 633, 210, 700))])))

    def test_competing_amount_or_label_candidates_remain_unknown(self):
        self.assertIsNone(read_energy_popup(raw(
            popup_lines() + [line("+21", (470, 565, 625, 641))]
        )))
        self.assertIsNone(read_energy_popup(raw(
            popup_lines() + [line("Energy", (480, 633, 618, 700))]
        )))

    def test_low_confidence_malformed_and_nonfinite_rows_are_rejected(self):
        self.assertIsNone(read_energy_popup(raw([
            line("+20", (469, 565, 624, 641), 96.99),
            line("Energy", (479, 633, 617, 700)),
        ])))
        self.assertIsNone(read_energy_popup(raw([
            line("+20", (469, 565, 624, 641)),
            line("Energy", (479, 633, 617, 700), float("nan")),
        ])))
        self.assertIsNone(read_energy_popup(raw([
            line("+20", (float("nan"), 565, 624, 641)),
            line("Energy", (479, 633, 617, 700)),
        ])))
        self.assertIsNone(read_energy_popup(raw([
            line("+20", (469, 565, 624, 641)),
            line("Energy", (479, 633, 960, 700)),
        ])))

    def test_source_proof_is_a_deep_copy_and_zero_is_not_recovery(self):
        source = raw(popup_lines(amount="+0"))
        self.assertIsNone(read_energy_popup(source))
        source = raw(popup_lines())
        result = read_energy_popup(source)
        source["lines"][0]["text"] = "+99"
        self.assertEqual(result["source_proof"]["amount"]["text"], "+20")


if __name__ == "__main__":
    unittest.main()
