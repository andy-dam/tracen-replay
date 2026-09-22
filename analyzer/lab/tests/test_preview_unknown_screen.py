"""Tests of ``tests.test_preview_unknown_screen`` that need locally preserved evidence; they run only where it is."""
from __future__ import annotations
import json
import unittest
from tests import localdata
from tracen_replay.lesson_offer_adapter import adapt_lesson_offer_frame
from tracen_replay.preview_observations import build_preview_observations


class UnknownScreenPreviewTests(unittest.TestCase):










    def test_actual_lesson_hint_cards_keep_level_and_label_metadata(self):
        base = localdata.root("development_third_recording_baseline")
        source_sha256 = "a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313"
        expected = {
            "000094": ("Group Lesson Basics", 1),
            "000121": ("Group Lesson Intermediate", 2),
        }
        required = []
        for frame_id in expected:
            required.extend((
                base / f"neural/part-005-frame-{frame_id}.json",
                base / f"gameplay/part-005-frame-{frame_id}.png",
                base / f"part-005/frames/{frame_id}.jpg",
            ))
        if not all(path.is_file() for path in required):
            self.skipTest("frozen independent-02 lesson source is not present")

        for frame_id, (offer_name, amount) in expected.items():
            with self.subTest(frame_id=frame_id):
                raw = json.loads(
                    (base / f"neural/part-005-frame-{frame_id}.json").read_text(
                        encoding="utf-8"
                    )
                )
                adapted = adapt_lesson_offer_frame(
                    raw,
                    gameplay_path=base / f"gameplay/part-005-frame-{frame_id}.png",
                    source_frame_path=base / f"part-005/frames/{frame_id}.jpg",
                    source_sha256=source_sha256,
                )
                offer = next(item for item in adapted["offers"] if item["name"] == offer_name)
                hint = next(item for item in offer["effects"] if item["kind"] == "skill_hint_change")
                evidence = adapted["source_proof"]["evidence"]
                row = {
                    "source_timestamp_ms": int(frame_id),
                    "evidence": evidence,
                    "screen": "lesson_selection",
                    "completed_action": None,
                    "facts": {
                        "preview_effects": [
                            dict(
                                hint,
                                phase="preview",
                                preview=True,
                                awarded=False,
                                context_title=offer_name,
                                source_evidence=[evidence],
                            )
                        ]
                    },
                    "stats": {"preview_option": None},
                }
                result = build_preview_observations([row])
                self.assertEqual(len(result["observations"]), 1)
                self.assertEqual(result["observations"][0]["payload"], {
                    "kind": "skill_hint_change",
                    "amount": amount,
                    "category": "skill_hint",
                    "level": "medium",
                    "name_visible": False,
                    "raw_label": f"Skill Hint Lvl +{amount} (Medium)",
                })
