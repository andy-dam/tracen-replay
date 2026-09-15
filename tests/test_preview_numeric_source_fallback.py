"""Regression coverage for source-layout preview/stat and lesson effects."""

from __future__ import annotations

import copy
import unittest

from PIL import Image

from tests.test_preview_recovery import _FakeReader, _raw
from tracen_replay.gameplay import preview_effects
from tracen_replay.lesson_offer_adapter import _effect
from tracen_replay.preview_observations import build_preview_observations
from tracen_replay.preview_recovery import candidate_requests, recover_in_memory
from tracen_replay.vision import parse


class PreviewNumericSourceFallbackTests(unittest.TestCase):
    def test_labeled_training_layout_recovers_when_grid_and_failure_badge_are_missing(self):
        raw = _raw(header="Training", failure=False)
        raw["current_grid"] = False
        raw["result_grid"] = False

        requests = candidate_requests(raw)
        self.assertTrue(any(request["role"] == "main" for request in requests))
        self.assertTrue(all("expected" not in request for request in requests))

        before = copy.deepcopy(raw)
        recovered = recover_in_memory(
            raw,
            Image.new("RGB", (810, 1080)),
            reader=_FakeReader({"speed": "+5", "stamina": "+7"}),
        )
        self.assertEqual(raw, before)
        self.assertEqual(
            {(effect["field"], effect["amount"])
             for effect in recovered["preview_recovery"]["effects"]},
            {("speed", 5), ("stamina", 7)},
        )

        row = parse(recovered)
        row.update(source_timestamp_ms=42, evidence="gameplay.png")
        observations = build_preview_observations([row])["observations"]
        self.assertEqual(
            {(item["payload"]["field"], item["payload"]["amount"])
             for item in observations},
            {("speed", 5), ("stamina", 7)},
        )
        self.assertTrue(all(item["phase"] == "preview" for item in observations))

    def test_result_marker_vetoes_source_layout_fallback(self):
        raw = _raw(header="Training", failure=False)
        raw["current_grid"] = False
        raw["result_grid"] = True
        self.assertEqual(
            [request for request in candidate_requests(raw)
             if request.get("role") == "main"],
            [],
        )

    def test_lesson_parser_types_abbreviated_skill_points_and_energy(self):
        skill = _effect({
            "text": "Skill Pts +6",
            "confidence": 99.9,
            "box": [471, 723, 573, 749],
            "index": 1,
        })
        energy = _effect({
            "text": "Energy +20",
            "confidence": 99.9,
            "box": [471, 710, 583, 741],
            "index": 2,
        })
        self.assertEqual(
            {"kind": skill["kind"], "field": skill["field"], "amount": skill["amount"]},
            {"kind": "stat_change", "field": "skill_points", "amount": 6},
        )
        self.assertEqual(
            {"kind": energy["kind"], "field": energy["field"], "amount": energy["amount"]},
            {"kind": "energy_change", "field": "energy", "amount": 20},
        )

    def test_lesson_preview_effect_parser_returns_typed_same_frame_fields(self):
        effects = preview_effects([
            {"text": "Skill Pts +12", "confidence": 99,
             "box": [458, 140, 581, 170]},
            {"text": "Energy +20", "confidence": 99,
             "box": [471, 710, 583, 741]},
        ], typed=True)
        self.assertEqual(
            effects,
            [
                {"kind": "immediate_on_purchase", "raw_text": "Skill Pts +12",
                 "awarded": False, "field": "skill_points", "amount": 12},
                {"kind": "energy_change", "raw_text": "Energy +20",
                 "awarded": False, "field": "energy", "amount": 20},
            ],
        )

    def test_confirmation_projection_keeps_same_frame_offer_identity(self):
        row = {
            "source_timestamp_ms": 1,
            "evidence": "source/frame-1.png",
            "screen": "lesson_confirmation",
            "completed_action": None,
            "facts": {
                "projected_effects": [{
                    "kind": "immediate_on_purchase",
                    "raw_text": "Skill Pts +12",
                    "awarded": False,
                    "field": "skill_points",
                    "amount": 12,
                    "source_offer": "Watch a Top-Tier Idol's Concert",
                    "source_semantics": "lesson_offer",
                }],
            },
            "stats": {},
        }
        result = build_preview_observations([row])
        self.assertEqual(len(result["observations"]), 1)
        observation = result["observations"][0]
        self.assertEqual(observation["phase"], "preview")
        self.assertEqual(observation["payload"]["field"], "skill_points")
        self.assertEqual(observation["payload"]["amount"], 12)
        self.assertEqual(
            observation["payload"]["source_offer"],
            "Watch a Top-Tier Idol's Concert",
        )

    def test_confirmation_projection_without_parser_owned_identity_is_rejected(self):
        row = {
            "source_timestamp_ms": 1,
            "evidence": "source/frame-1.png",
            "screen": "lesson_confirmation",
            "completed_action": None,
            "facts": {
                "projected_effects": [{
                    "kind": "immediate_on_purchase",
                    "raw_text": "Skill Pts +12",
                    "awarded": False,
                    "field": "skill_points",
                    "amount": 12,
                    "source_offer": "Copied context text",
                }],
            },
            "stats": {},
        }
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["rejected_counts"], {"unproven_typed_source_offer": 1})


if __name__ == "__main__":
    unittest.main()
