"""Focused regressions for the early independent source controls.

These tests exercise the ordinary parser/transaction path with source-shaped
observations.  They deliberately do not use an expected effect, a balance
equation, or a recording-specific timestamp to manufacture a result.
"""

import json
from pathlib import Path
import unittest

from tracen_replay.animated_performance import candidates, reconcile
from tracen_replay.event_choice_commitment import reconstruct_committed_choices
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import NeuralReader, parse
from tracen_replay.event_choice_adapter import (
    build_choice_observations,
    same_frame_choice_observation,
)


def line(text, box, confidence=99):
    return {"text": text, "box": list(box), "confidence": confidence}


def choice_card(text, top, *, confidence=99):
    return {
        "text": text,
        "confidence": confidence,
        "text_box": [317, top + 25, 600, top + 53],
        "card_y": [top, top + 80],
    }


def menu_row(timestamp, options, *, evidence=None):
    cards = [choice_card(text, top) for text, top in options]
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence or f"gameplay/frame-{timestamp}.png",
        "offered_card_candidates": cards,
        "offered_card_slots": cards,
        "menu_text_complete": True,
        "selection_mark_pairs": [],
    }


def selected_row(timestamp, card, *, remaining, evidence="gameplay/selected.png"):
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        # The remaining white cards stay visible while the selected green card
        # moves.  The commitment reader uses this collapse as phase evidence.
        "offered_card_candidates": list(remaining),
        "offered_card_slots": list(remaining),
        "menu_text_complete": False,
        "selected_card_candidates": [card],
        "selection_mark_pairs": [],
    }


def green_proof(card, *, pixels=30000):
    """Attach the bounded parser-owned fill marker used by low-confidence OCR."""

    return dict(card, selection_visual_proof={
        "kind": "green_card_fill",
        "detector": "choice_evidence.green_card_fill_v1",
        "green_pixels": pixels,
    })


class EarlyComposureTests(unittest.TestCase):
    def _source_like_lines(self, *, caption="Composure went by 10.", label="Me Composure"):
        return [
            line("+10", (650, 455, 794, 528)),
            line(label, (609, 366, 832, 412)),
            line(caption, (315, 917, 578, 953), 95),
        ]

    def test_sparse_omitted_up_animation_is_promoted_from_two_signed_frames(self):
        rows = []
        for timestamp, label in ((636250, "Me Composure"), (636500, "Composure")):
            facts = {"animated_performance_candidates": candidates(
                self._source_like_lines(label=label), "event_outcome"
            )}
            self.assertEqual(facts["animated_performance_candidates"][0]["amount"], 10)
            self.assertEqual(
                facts["animated_performance_candidates"][0]["receipt_normalization"],
                "signed_positive_animation_repairs_omitted_up",
            )
            rows.append({
                "source_timestamp_ms": timestamp,
                "evidence": f"gameplay/frame-{timestamp}.png",
                "facts": facts,
            })
        event = {
            "first_seen_ms": 636000,
            "last_seen_ms": 636750,
            "effects": {},
            "field_evidence": {},
            "conflicting_readings": [],
        }
        reconcile(event, rows)
        effect = event["effects"]["performance_change|composure|"]
        self.assertEqual(effect["amount"], 10)
        self.assertEqual(len(event["animated_performance_evidence"]["composure"]), 2)

    def test_omitted_direction_requires_signed_positive_card_and_matching_amount(self):
        for caption, gain in (("Composure went by 10.", "+10"),
                              ("Composure wentby 10.", "+10")):
            self.assertEqual(
                candidates(self._source_like_lines(caption=caption), "event_outcome")[0]["amount"],
                10,
            )
        self.assertEqual(
            candidates(self._source_like_lines(caption="Composure went by 10.")[:2],
                       "event_outcome"),
            [],
        )
        self.assertEqual(
            candidates(self._source_like_lines(caption="Composure went by 5."),
                       "event_outcome"),
            [],
        )
        self.assertEqual(
            candidates(self._source_like_lines(caption="Composure went down by 10."),
                       "event_outcome"),
            [],
        )

    def test_actual_fourth_composure_frames_reach_normal_transactions(self):
        root = Path(".local/final-reliability-v1/worker-runs/untouched-fourth-v1")
        sidecars = [root / "neural" / f"part-005-frame-{number:06d}.json"
                    for number in (146, 147)]
        if not all(path.is_file() for path in sidecars):
            self.skipTest("untouched fourth source sidecars unavailable")
        readings = []
        for path in sidecars:
            raw = json.loads(path.read_text(encoding="utf-8"))
            parsed = parse(raw)
            readings.append(dict(
                parsed,
                source_timestamp_ms=raw["source_timestamp_ms"],
                evidence=raw["evidence"],
            ))
        events = outcome_events(readings)
        self.assertEqual(len(events), 1)
        composure = [effect for effect in events[0]["effects"]
                     if effect.get("kind") == "performance_change"
                     and effect.get("field") == "composure"]
        self.assertEqual(len(composure), 1)
        self.assertEqual(composure[0]["amount"], 10)
        self.assertEqual(
            events[0]["field_evidence"]["performance_change|composure|"],
            [
                "gameplay/part-005-frame-000146.png",
                "gameplay/part-005-frame-000147.png",
            ],
        )


class EarlyChoiceCommitmentTests(unittest.TestCase):
    OPTIONS = [
        ("You're just going to power through it?", 606),
        ("Sounds like you're writing a pretty complicated thesis.", 716),
    ]

    def test_repeated_menu_plus_collapsing_selected_card_commits_visual_choice(self):
        rows = [
            menu_row(330933, self.OPTIONS),
            menu_row(331000, self.OPTIONS),
            selected_row(
                331267,
                choice_card("You're just going to per through it?", 609),
                remaining=[choice_card(self.OPTIONS[1][0], 716)],
            ),
        ]
        audit = {}
        result = reconstruct_committed_choices(rows, audit=audit)
        self.assertEqual(len(result), 1)
        event = result[0]
        self.assertEqual(event["selected_index"], 0)
        self.assertEqual(event["selected_text"], self.OPTIONS[0][0])
        self.assertEqual(event["selection_basis"], "selected_card_highlight_and_transition")
        self.assertIsNone(event["selection_marks"])
        self.assertEqual(event["selection_state"], "committed")
        self.assertEqual(event["phase"], "committed")
        self.assertEqual(event["selected_card_proof"]["card"]["card_y"], [609, 689])
        self.assertEqual(audit["committed_count"], 1)
        self.assertEqual(audit["menus"][0]["status"], "committed")

    def test_hover_or_static_green_card_is_not_a_commitment(self):
        options = self.OPTIONS
        # A green-looking card in a full, unchanged menu is still only a
        # preview.  The source fill marker cannot bypass the collapse witness.
        static_green = green_proof(choice_card(options[0][0], 606))
        rows = [
            dict(menu_row(100, options), selected_card_candidates=[static_green]),
            dict(menu_row(250, options), selected_card_candidates=[static_green]),
        ]
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(audit["menus"][0]["status"], "selection_unobserved")
        self.assertEqual(audit["unobserved_menu_count"], 1)

    def test_selected_card_without_stable_menu_is_unsupported_and_outcome_does_not_help(self):
        rows = [selected_row(
            500,
            choice_card("A choice seen once", 606),
            remaining=[choice_card("Another option", 716)],
        )]
        rows[0]["effects"] = [{"kind": "energy_change", "amount": 20}]
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        # A transition candidate without a repeated full menu is insufficient;
        # the downstream effect is intentionally ignored.
        self.assertEqual(audit["menus"], [])

    def test_conflicting_or_ambiguous_selected_cards_abstain(self):
        options = self.OPTIONS
        rows = [menu_row(100, options), menu_row(250, options)]
        rows.append(selected_row(
            500,
            choice_card(options[0][0], 606),
            remaining=[choice_card(options[1][0], 716)],
        ))
        rows[-1]["selected_card_candidates"].append(choice_card(options[1][0], 716))
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(audit["menus"][0]["status"], "selection_unsupported")
        self.assertIn("multiple_selected_cards", audit["menus"][0]["reasons"])

    def test_low_confidence_selected_card_is_not_promoted(self):
        options = self.OPTIONS
        rows = [menu_row(100, options), menu_row(250, options)]
        rows.append(selected_row(
            500,
            choice_card(options[0][0], 606, confidence=96),
            remaining=[choice_card(options[1][0], 716)],
        ))
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(audit["menus"][0]["status"], "selection_unobserved")

    def test_low_confidence_selected_card_with_green_source_proof_is_promoted(self):
        options = self.OPTIONS
        rows = [menu_row(100, options), menu_row(250, options)]
        rows.append(selected_row(
            500,
            green_proof(choice_card(options[0][0], 606, confidence=96)),
            remaining=[choice_card(options[1][0], 716)],
        ))
        result = reconstruct_committed_choices(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["selected_index"], 0)
        self.assertEqual(
            result[0]["selected_card_proof"]["card"]["selection_visual_proof"],
            {
                "kind": "green_card_fill",
                "detector": "choice_evidence.green_card_fill_v1",
                "green_pixels": 30000,
            },
        )

    def test_low_confidence_green_proof_with_changed_text_abstains(self):
        options = self.OPTIONS
        rows = [menu_row(100, options), menu_row(250, options)]
        rows.append(selected_row(
            500,
            green_proof(choice_card("A different option entirely", 606, confidence=96)),
            remaining=[choice_card(options[1][0], 716)],
        ))
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(audit["menus"][0]["status"], "selection_unsupported")
        self.assertIn(
            "selected_card_not_unique_in_stable_menu",
            audit["menus"][0]["reasons"],
        )

    def test_menu_disappearance_without_green_proof_does_not_commit(self):
        options = self.OPTIONS
        rows = [menu_row(100, options), menu_row(250, options)]
        rows.append({
            "source_timestamp_ms": 500,
            "evidence": "gameplay/disappeared.png",
            "offered_card_candidates": [choice_card(options[1][0], 716)],
            "offered_card_slots": [choice_card(options[1][0], 716)],
            "menu_text_complete": False,
            "selection_mark_pairs": [],
            "effects": [{"kind": "energy_change", "amount": 20}],
        })
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(audit["menus"][0]["status"], "selection_unobserved")

    @unittest.skipUnless(
        Path(".local/final-reliability-v1/fourth-source-controls-early-v1/native/")
        .is_dir() and Path(".local/models/rapidocr").is_dir(),
        "early source images or the pinned OCR model are unavailable",
    )
    def test_actual_native_choice_transition_uses_green_collapse_witness(self):
        root = Path(".local/final-reliability-v1/fourth-source-controls-early-v1")
        reader = NeuralReader(".local/models/rapidocr")
        specs = [
            ("native/window-002/frames/frame-000092.png", 330933),
            ("native/window-002/frames/frame-000093.png", 331000),
            ("native/window-002-choice-native/frames/frame-000017.png", 331267),
        ]
        rows = []
        for relative, timestamp in specs:
            image_path = root / relative
            with open(image_path, "rb") as handle:
                from PIL import Image
                with Image.open(handle) as image:
                    pane = image.convert("RGB")
            raw = reader.read(pane)
            observation = same_frame_choice_observation(
                pane, raw["lines"], source_timestamp_ms=timestamp, evidence=relative
            )
            self.assertIsNotNone(observation)
            rows.append(observation)
        self.assertEqual(len(rows[-1]["selected_card_candidates"]), 1)
        self.assertLess(len(rows[-1]["offered_card_slots"]), 2)
        result = reconstruct_committed_choices(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["selected_index"], 0)
        self.assertEqual(result[0]["selected_text"], self.OPTIONS[0][0])
        self.assertEqual(result[0]["selection_basis"],
                         "selected_card_highlight_and_transition")

    @unittest.skipUnless(
        Path(".local/final-reliability-v1/fourth-source-controls-early-v1/native/")
        .is_dir() and Path(".local/models/rapidocr").is_dir(),
        "early source images or the pinned OCR model are unavailable",
    )
    def test_actual_native_hover_without_selection_witness_stays_uncommitted(self):
        root = Path(".local/final-reliability-v1/fourth-source-controls-early-v1")
        reader = NeuralReader(".local/models/rapidocr")
        specs = [
            ("native/window-003-choice-native/frames/frame-000012.png", 639933),
            ("native/window-003-choice-native/frames/frame-000013.png", 640000),
            ("native/window-003-choice-native/frames/frame-000014.png", 640067),
        ]
        rows = []
        for relative, timestamp in specs:
            image_path = root / relative
            with open(image_path, "rb") as handle:
                from PIL import Image
                with Image.open(handle) as image:
                    pane = image.convert("RGB")
            raw = reader.read(pane)
            observation = same_frame_choice_observation(
                pane, raw["lines"], source_timestamp_ms=timestamp, evidence=relative
            )
            self.assertIsNotNone(observation)
            rows.append(observation)
        # The source frame has a yellow hover card and two remaining white
        # cards, but no parser-owned selected green transition.  The gold
        # label alone cannot be promoted as a click.
        self.assertEqual(rows[-1]["selected_card_candidates"], [])
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(audit["menus"][0]["status"], "selection_unobserved")

    @unittest.skipUnless(
        Path(".local/final-reliability-v1/worker-runs/fourth-declared-retest-v11/neural/").is_dir(),
        "fourth worker source sidecars are unavailable",
    )
    def test_actual_worker_green_transition_survives_96_816_ocr(self):
        root = Path(".local/final-reliability-v1/worker-runs/fourth-declared-retest-v11")
        required = [
            root / "neural" / f"part-002-frame-{number:06d}.json"
            for number in (364, 365, 366)
        ]
        if not all(path.is_file() for path in required):
            self.skipTest("fourth worker choice sidecars unavailable")
        readings = []
        for path in required:
            raw = json.loads(path.read_text(encoding="utf-8"))
            parsed = parse(raw)
            parsed.update(
                source_timestamp_ms=raw["source_timestamp_ms"],
                evidence=raw["evidence"],
            )
            readings.append(parsed)
        result = build_choice_observations(readings, root)
        self.assertEqual(result["committed_choice_count"], 1)
        event = result["committed_choices"][0]
        self.assertEqual(event["selected_index"], 0)
        self.assertEqual(event["selection_observed_ms"], 331250)
        self.assertEqual(event["selection_basis"], "selected_card_highlight_and_transition")
        proof_card = event["selected_card_proof"]["card"]
        self.assertEqual(proof_card["confidence"], 96.816)
        self.assertEqual(proof_card["selection_visual_proof"]["kind"], "green_card_fill")
        self.assertEqual(
            event["selected_card_proof"]["evidence"],
            ["gameplay/part-002-frame-000366.png"],
        )


if __name__ == "__main__":
    unittest.main()
