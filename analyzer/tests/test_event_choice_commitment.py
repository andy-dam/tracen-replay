import unittest


from tracen_replay.choice_evidence import observe
from tracen_replay.event_choice_commitment import reconstruct_committed_choices


def card(text, top, *, confidence=99):
    return {
        "text": text,
        "confidence": confidence,
        "text_box": [317, top + 25, 600, top + 53],
        "card_y": [top, top + 80],
    }


def mark(top):
    return {
        "left": {"box": [258, top, 313, top + 33]},
        "right": {"box": [798, top, 853, top + 33]},
    }


def green_card(card_value):
    card_value = dict(card_value)
    card_value["selection_visual_proof"] = {
        "kind": "green_card_fill",
        "detector": "choice_evidence.green_card_fill_v1",
        "green_pixels": 30000,
    }
    return card_value


def menu_row(time, options, *, evidence=None, marks=(), complete=True):
    cards = [card(text, top) for text, top in options]
    return {
        "source_timestamp_ms": time,
        "evidence": evidence or f"frame-{time}.png",
        "offered_card_candidates": cards,
        "offered_card_slots": cards,
        "menu_text_complete": complete,
        "selection_mark_pairs": list(marks),
    }


class EventChoiceCommitmentTests(unittest.TestCase):
    def test_repeated_menu_and_bilateral_witness_promote_choice(self):
        rows = [
            menu_row(100, [("A deliberately arbitrary answer", 603),
                          ("A second arbitrary answer", 714)]),
            menu_row(250, [("A deliberately arbitrary answer", 603),
                          ("A second arbitrary answer", 714)]),
            {
                "source_timestamp_ms": 500,
                "evidence": "selection.png",
                "offered_card_candidates": [],
                "selection_mark_pairs": [mark(700)],
            },
        ]
        result = reconstruct_committed_choices(rows)
        self.assertEqual(len(result), 1)
        event = result[0]
        self.assertEqual(event["kind"], "dialogue_choice")
        self.assertEqual(event["selected_index"], 1)
        self.assertEqual(event["selected_text"], "A second arbitrary answer")
        self.assertEqual(event["selection_basis"], "bilateral_selection_marks")
        self.assertEqual(event["selection_state"], "committed")
        self.assertEqual(event["phase"], "committed")
        self.assertEqual(event["evidence"], ["frame-100.png", "frame-250.png", "selection.png"])

    def test_connecting_while_cards_are_visible_does_not_commit_early(self):
        rows = [
            menu_row(100, [("First option", 603), ("Second option", 714)]),
            menu_row(250, [("First option", 603), ("Second option", 714)]),
            menu_row(300, [("First option", 603), ("Second option", 714)],
                     evidence="connecting.png"),
            {
                "source_timestamp_ms": 500,
                "evidence": "selected.png",
                "offered_card_candidates": [],
                "selection_mark_pairs": [mark(700)],
            },
        ]
        result = reconstruct_committed_choices(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["selection_observed_ms"], 500)
        self.assertNotIn("connecting.png", result[0]["evidence"])

    def test_preview_menu_without_selected_state_stays_uncommitted(self):
        rows = [
            menu_row(100, [("First option", 603), ("Second option", 714)]),
            menu_row(250, [("First option", 603), ("Second option", 714)]),
        ]
        self.assertEqual(reconstruct_committed_choices(rows), [])

    def test_hover_or_highlight_text_without_bilateral_witness_is_preview(self):
        rows = [
            menu_row(100, [("First option", 603), ("Second option", 714)]),
            menu_row(250, [("First option", 603), ("Second option", 714)]),
            menu_row(500, [("First option", 603)], complete=True),
        ]
        # The menu shrinking is a visual transition, but it contains no
        # selected-state witness.  It must not become a commitment.
        self.assertEqual(reconstruct_committed_choices(rows), [])

    def test_green_collapse_requires_remaining_cards_to_match_stable_menu(self):
        rows = [
            menu_row(100, [("Stable A", 603), ("Stable B", 714)]),
            menu_row(250, [("Stable A", 603), ("Stable B", 714)]),
            {
                "source_timestamp_ms": 500,
                "evidence": "selected.png",
                "offered_card_candidates": [card("Changed B", 714)],
                "offered_card_slots": [card("Changed B", 714)],
                "menu_text_complete": False,
                "selected_card_candidates": [green_card(card("Stable A", 603))],
                "selection_mark_pairs": [],
            },
        ]
        self.assertEqual(reconstruct_committed_choices(rows), [])

    def test_low_confidence_remaining_menu_card_cannot_use_green_fallback(self):
        for confidence in (0, 50):
            with self.subTest(confidence=confidence):
                rows = [
                    menu_row(100, [("Stable A", 603), ("Stable B", 714)]),
                    menu_row(250, [("Stable A", 603), ("Stable B", 714)]),
                    {
                        "source_timestamp_ms": 500,
                        "evidence": "selected.png",
                        "offered_card_candidates": [card("Stable B", 714,
                                                            confidence=confidence)],
                        "offered_card_slots": [card("Stable B", 714,
                                                      confidence=confidence)],
                        "menu_text_complete": False,
                        "selected_card_candidates": [green_card(
                            card("Stable A", 603, confidence=96)
                        )],
                        "selection_mark_pairs": [],
                    },
                ]
                self.assertEqual(reconstruct_committed_choices(rows), [])

    def test_repeated_slot_menu_with_one_sub_gate_card_commits_on_green_collapse(self):
        # Actual first-recording shape (92750-97500 ms): the first card OCRs at
        # 96.1 while the highlight moves, so it is absent from the candidate
        # channel but present in the geometry-complete slot channel.  The
        # repeated slot menu still identifies the offered options; the later
        # green collapse then proves the selection.  Several earlier
        # implementations accepted this and a later one silently lost it.
        def slot_row(time):
            low = card("Give it all", 493, confidence=96.101)
            rest = [card("Do not overdo it", 605), card("Wipe the plate", 715)]
            return {
                "source_timestamp_ms": time,
                "evidence": f"frame-{time}.png",
                "offered_card_candidates": rest,
                "offered_card_slots": [low, *rest],
                "menu_text_complete": False,
                "selection_mark_pairs": [],
            }
        # The menu repeats every 250 ms (4 fps sampling) until the highlight
        # frame at 97500, so the 1500 ms menu gap rule never resets it.
        rows = [
            *(slot_row(time) for time in range(92750, 97500, 250)),
            {
                "source_timestamp_ms": 97500,
                "evidence": "selected.png",
                "offered_card_candidates": [card("Do not overdo it", 605),
                                            card("Wipe the plate", 715)],
                "offered_card_slots": [card("Do not overdo it", 605),
                                       card("Wipe the plate", 715)],
                "menu_text_complete": False,
                "selected_card_candidates": [green_card(card("Give it all", 493))],
                "selection_mark_pairs": [],
            },
        ]
        audit = {}
        result = reconstruct_committed_choices(rows, audit=audit)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["options"],
                         ["Give it all", "Do not overdo it", "Wipe the plate"])
        self.assertEqual(result[0]["selected_index"], 0)
        self.assertEqual(result[0]["first_seen_ms"], 92750)
        self.assertEqual(result[0]["selection_basis"],
                         "selected_card_highlight_and_transition")
        self.assertEqual(audit["menus"][0]["status"], "committed")
        self.assertEqual(audit["orphan_witnesses"], [])

    def test_collapsed_transition_remaining_card_still_needs_readable_ocr(self):
        # Restoring slot-based menu identity must not reopen the transition
        # path to unreadable remaining cards.
        rows = [
            menu_row(100, [("Stable A", 603), ("Stable B", 714)]),
            menu_row(250, [("Stable A", 603), ("Stable B", 714)]),
            {
                "source_timestamp_ms": 500,
                "evidence": "selected.png",
                "offered_card_candidates": [],
                "offered_card_slots": [card("Stable B", 714, confidence=80)],
                "menu_text_complete": False,
                "selected_card_candidates": [green_card(card("Stable A", 603))],
                "selection_mark_pairs": [],
            },
        ]
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(len(audit["menus"]), 1)
        self.assertEqual(audit["menus"][0]["status"], "selection_unsupported")
        self.assertIn("remaining_cards_do_not_match_stable_menu",
                      audit["menus"][0]["reasons"])

    def test_unbound_selected_card_is_audited_as_orphan_witness(self):
        rows = [
            {
                "source_timestamp_ms": 500,
                "evidence": "selected.png",
                "offered_card_candidates": [card("Other B", 714)],
                "offered_card_slots": [card("Other B", 714)],
                "menu_text_complete": False,
                "selected_card_candidates": [green_card(card("Alone A", 603))],
                "selection_mark_pairs": [],
            },
        ]
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(
            [item["reason"] for item in audit["orphan_witnesses"]],
            ["selection_witness_without_repeated_menu"],
        )
        self.assertEqual(audit["orphan_witnesses"][0]["source_timestamp_ms"], 500)

    def test_single_mark_without_repeated_menu_abstains(self):
        rows = [{
            "source_timestamp_ms": 100,
            "evidence": "selection.png",
            "offered_card_candidates": [card("Only seen once", 714)],
            "selection_mark_pairs": [mark(700)],
        }]
        self.assertEqual(reconstruct_committed_choices(rows), [])

    def test_one_ocr_spacing_error_does_not_discard_stable_menu(self):
        rows = [
            menu_row(100, [("Your dedication to research?", 603),
                          ("Your dedication to the potential of Umamusume?", 714)]),
            menu_row(250, [("Your dedication to research?", 603),
                          ("Your dedication to the potential of Umamusume?", 714)]),
            menu_row(500, [("Your dedication to research?", 603),
                          ("Your dedication to the pot ntial of Umamusume?", 714)]),
            {
                "source_timestamp_ms": 750,
                "evidence": "selection.png",
                "offered_card_candidates": [],
                "selection_mark_pairs": [mark(700)],
            },
        ]
        result = reconstruct_committed_choices(rows)
        self.assertEqual([event["selected_text"] for event in result],
                         ["Your dedication to the potential of Umamusume?"])
        # The source wording remains the canonical wording from a stable menu;
        # the noisy transition row never replaces it.
        self.assertEqual(result[0]["selected_index"], 1)

    def test_major_conflicting_menu_invalidates_old_identity_on_one_frame(self):
        rows = [
            menu_row(100, [("Alpha answer", 603), ("Beta answer", 714)]),
            menu_row(250, [("Alpha answer", 603), ("Beta answer", 714)]),
            menu_row(500, [("Completely different", 603), ("Another different", 714)]),
            {
                "source_timestamp_ms": 750,
                "evidence": "selection.png",
                "offered_card_candidates": [],
                "selection_mark_pairs": [mark(700)],
            },
        ]
        result = reconstruct_committed_choices(rows)
        self.assertEqual(result, [])

    def test_known_screen_boundary_clears_preview_identity(self):
        rows = [
            menu_row(100, [("Before", 603), ("Before two", 714)]),
            menu_row(250, [("Before", 603), ("Before two", 714)]),
            {"source_timestamp_ms": 300, "screen_boundary": True},
            menu_row(500, [("After", 603), ("After two", 714)]),
            {"source_timestamp_ms": 750, "evidence": "selection.png",
             "offered_card_candidates": [], "selection_mark_pairs": [mark(700)]},
        ]
        self.assertEqual(reconstruct_committed_choices(rows), [])

    def test_bare_selected_text_is_not_a_commitment_witness(self):
        rows = [
            menu_row(100, [("First", 603), ("Second", 714)]),
            menu_row(250, [("First", 603), ("Second", 714)]),
            {"source_timestamp_ms": 500, "evidence": "selected.png",
             "offered_card_candidates": [], "selected_text": "Second"},
        ]
        self.assertEqual(reconstruct_committed_choices(rows), [])

    def test_parser_owned_explicit_witness_requires_verification_and_source(self):
        base = [menu_row(100, [("First", 603), ("Second", 714)]),
                menu_row(250, [("First", 603), ("Second", 714)])]
        for state in (
            {"verified": False, "selected_text": "Second",
             "basis": "selected_card_highlight_and_transition", "evidence": ["selected.png"]},
            {"verified": True, "selected_text": "Second",
             "basis": "selected_card_highlight_and_transition", "evidence": []},
        ):
            rows = base + [{"source_timestamp_ms": 500, "evidence": "selected.png",
                            "offered_card_candidates": [], "selected_state": state}]
            self.assertEqual(reconstruct_committed_choices(rows), [])
        rows = base + [{"source_timestamp_ms": 500, "evidence": "selected.png",
                        "offered_card_candidates": [], "selected_state": {
                            "verified": True,
                            "selected_text": "Second",
                            "basis": "selected_card_highlight_and_transition",
                            "evidence": ["selected.png"],
                        }}]
        result = reconstruct_committed_choices(rows)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["selection_marks"])
        self.assertEqual(result[0]["selection_basis"], "selected_card_highlight_and_transition")


if __name__ == "__main__":
    unittest.main()
