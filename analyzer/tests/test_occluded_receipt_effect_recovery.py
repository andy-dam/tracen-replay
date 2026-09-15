"""Focused regressions for source-bound recovery of blocked receipt effects."""

from __future__ import annotations

import copy
import unittest

from tracen_replay.occluded_receipt_recovery import (
    _receipt_line_conflicts_with_clear_reread,
    _restored_occluded_receipt_row,
    plan,
    scoped_observations,
)


def _blocked_line(text: str, box: list[int], *, confidence: float = 99.0) -> dict:
    return {
        "text": text,
        "box": list(box),
        "confidence": confidence,
        "overlay_boxes": [[box[0] + 90, box[1] + 9, box[0] + 98, box[1] + 17]],
        "animated_overlay_boxes": [[box[0] + 90, box[1] + 9, box[0] + 98, box[1] + 17]],
        "animated_overlay_occluded": True,
        "recipient_name_occluded": False,
    }


def _base_row(text: str, *, box: list[int], context: str = "Shogi") -> dict:
    return {
        "source_timestamp_ms": 1000,
        "screen": "event_outcome",
        "context_title": context,
        "evidence": "gameplay/base.png",
        "effects": [],
        "facts": {"occluded_receipt_lines": [_blocked_line(text, box)]},
        "ocr": {"neural": []},
    }


def _owner(context: str = "Shogi") -> list[dict]:
    return [{
        "id": "outcome-1",
        "kind": "outcome",
        "first_seen_ms": 1000,
        "last_seen_ms": 1000,
        "context_title": context,
        "effects": [],
    }]


def _fresh_row(
    text: str,
    *,
    box: list[int],
    context: str = "Shogi",
    extra_lines: list[dict] | None = None,
    screen: str = "event_outcome",
) -> dict:
    blocked = _blocked_line(text, box)
    blocked["confidence"] = 0
    blocked["pre_occlusion_confidence"] = 99.0
    blocked["overlay_occluded"] = True
    lines = [blocked]
    if extra_lines:
        lines.extend(copy.deepcopy(extra_lines))
    return {
        "source_timestamp_ms": 1100,
        "screen": screen,
        "context_title": context,
        "evidence": "receipt-inspection/clear.png",
        "effects": [],
        "facts": {"occluded_receipt_lines": [_blocked_line(text, box)]},
        "ocr": {"neural": lines},
    }


class OccludedReceiptEffectRecoveryTests(unittest.TestCase):
    def test_energy_receipt_is_recovered_from_its_blocked_source_line(self):
        box = [317, 808, 542, 835]
        base = _base_row("Energy recovered by 9.", box=box)
        fresh = _fresh_row("Energy recovered by 9.", box=box)

        promoted = scoped_observations([base], [fresh], plan([base], _owner(), 5000))

        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["effects"][0]["kind"], "energy_change")
        self.assertEqual(promoted[0]["effects"][0]["amount"], 9)
        self.assertEqual(
            promoted[0]["effects"][0]["source_bound_receipt_proof"]["basis"],
            "source_bound_occluded_receipt_line",
        )

    def test_performance_receipt_uses_signed_card_when_up_token_is_missing(self):
        box = [314, 915, 578, 954]
        base = _base_row("Composure went by 10.", box=box, context="New Supporters!")
        fresh = _fresh_row(
            "Composure went by 10.",
            box=box,
            context="New Supporters!",
            extra_lines=[
                {"text": "+10", "confidence": 99.8, "box": [647, 279, 796, 359]},
                {"text": "Composure", "confidence": 99.9, "box": [648, 362, 831, 409]},
            ],
        )

        promoted = scoped_observations(
            [base], [fresh], plan([base], _owner("New Supporters!"), 5000)
        )

        self.assertEqual(len(promoted), 1)
        effect = promoted[0]["effects"][0]
        self.assertEqual(
            (effect["kind"], effect["field"], effect["amount"]),
            ("performance_change", "composure", 10),
        )
        proof = effect["source_bound_receipt_proof"]
        self.assertEqual(proof["direction_basis"], "same_frame_signed_positive_gain")
        self.assertEqual(proof["receipt_normalization"], "signed_positive_animation_repairs_omitted_up")

    def test_source_amount_mismatch_is_rejected_even_when_geometry_matches(self):
        box = [317, 808, 542, 835]
        base = _base_row("Energy recovered by 9.", box=box)
        fresh = _fresh_row("Energy recovered by 8.", box=box)

        self.assertEqual(
            scoped_observations([base], [fresh], plan([base], _owner(), 5000)),
            [],
        )

    def test_source_direction_mismatch_is_rejected_even_when_amount_matches(self):
        box = [317, 808, 542, 835]
        base = _base_row("Energy recovered by 9.", box=box)
        fresh = _fresh_row("Energy went down by 9.", box=box)

        self.assertEqual(
            scoped_observations([base], [fresh], plan([base], _owner(), 5000)),
            [],
        )

    def test_same_box_clear_direction_conflict_cannot_be_erased_by_source_restore(self):
        box = [317, 807, 560, 834]
        source_line = _blocked_line("Energy went down by 20.", box)
        clear_line = {
            "text": "Energy went up by 20.",
            "box": list(box),
            "confidence": 99.0,
            "overlay_occluded": False,
            "animated_overlay_occluded": False,
        }
        conflicts = _receipt_line_conflicts_with_clear_reread(
            source_line, [clear_line]
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            conflicts[0]["basis"],
            "same_physical_receipt_line_semantic_conflict",
        )

        base = _base_row(source_line["text"], box=box)
        fresh = _fresh_row(source_line["text"], box=box)
        fresh["ocr"]["neural"].append(clear_line)
        self.assertIsNone(_restored_occluded_receipt_row(fresh, source_line))
        self.assertEqual(
            scoped_observations([base], [fresh], plan([base], _owner(), 5000)),
            [],
        )

    def test_same_box_agreeing_clear_reread_still_allows_source_restore(self):
        box = [317, 807, 560, 834]
        source_line = _blocked_line("Energy went down by 20.", box)
        clear_line = {
            "text": "Energy went down by 20.",
            "box": list(box),
            "confidence": 99.0,
            "overlay_occluded": False,
            "animated_overlay_occluded": False,
        }
        self.assertEqual(
            _receipt_line_conflicts_with_clear_reread(source_line, [clear_line]),
            [],
        )
        base = _base_row(source_line["text"], box=box)
        fresh = _fresh_row(source_line["text"], box=box)
        fresh["ocr"]["neural"].append(clear_line)
        promoted = scoped_observations(
            [base], [fresh], plan([base], _owner(), 5000)
        )
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["effects"][0]["amount"], -20)

    def test_same_box_performance_direction_conflict_is_unresolved(self):
        box = [314, 915, 578, 954]
        source_line = _blocked_line("Composure went down by 10.", box)
        clear_line = {
            "text": "Composure went up by 10.",
            "box": list(box),
            "confidence": 99.0,
            "overlay_occluded": False,
            "animated_overlay_occluded": False,
        }
        conflicts = _receipt_line_conflicts_with_clear_reread(
            source_line, [clear_line]
        )
        self.assertEqual(len(conflicts), 2)
        self.assertEqual(
            {conflict["basis"] for conflict in conflicts},
            {
                "same_physical_receipt_line_semantic_conflict",
                "same_physical_receipt_line_performance_conflict",
            },
        )

    def test_signed_card_amount_mismatch_does_not_repair_receipt(self):
        box = [314, 915, 578, 954]
        base = _base_row("Composure went by 10.", box=box, context="New Supporters!")
        fresh = _fresh_row(
            "Composure went by 10.",
            box=box,
            context="New Supporters!",
            extra_lines=[
                {"text": "+9", "confidence": 99.8, "box": [647, 279, 796, 359]},
                {"text": "Composure", "confidence": 99.9, "box": [648, 362, 831, 409]},
            ],
        )

        self.assertEqual(
            scoped_observations(
                [base], [fresh], plan([base], _owner("New Supporters!"), 5000)
            ),
            [],
        )

    def test_performance_source_amount_mismatch_is_rejected_before_card_repair(self):
        box = [314, 915, 578, 954]
        base = _base_row("Composure went by 10.", box=box, context="New Supporters!")
        fresh = _fresh_row(
            "Composure went by 9.",
            box=box,
            context="New Supporters!",
            extra_lines=[
                {"text": "+9", "confidence": 99.8, "box": [647, 279, 796, 359]},
                {"text": "Composure", "confidence": 99.9, "box": [648, 362, 831, 409]},
            ],
        )

        self.assertEqual(
            scoped_observations(
                [base], [fresh], plan([base], _owner("New Supporters!"), 5000)
            ),
            [],
        )

    def test_same_physical_receipt_across_dense_frames_is_promoted_once(self):
        box = [317, 808, 542, 835]
        base = _base_row("Energy recovered by 9.", box=box)
        first = _fresh_row("Energy recovered by 9.", box=box)
        second = copy.deepcopy(first)
        second["source_timestamp_ms"] = 1150
        second["facts"]["occluded_receipt_lines"][0]["box"] = [318, 809, 543, 836]
        second["ocr"]["neural"][0]["box"] = [318, 809, 543, 836]

        promoted = scoped_observations(
            [base], [first, second], plan([base], _owner(), 5000)
        )

        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["effects"][0]["amount"], 9)

    def test_clipped_wrapped_prefix_reread_is_not_promoted(self):
        # Actual first-recording shape (137250-139550 ms): the hint name wraps
        # onto a second visual line and the cursor damages the first.  Each
        # dense frame reread the first line only ("... for Pace Cnaser",
        # "... for Pace Shaser") and an earlier implementation promoted five
        # different hints for one receipt.  A restored line without a
        # sentence terminator is not a complete receipt and must not be
        # parsed.
        box = [317, 853, 681, 881]
        base = _base_row("Gained 1 hint level(s) for Pace Chaser", box=box)
        frames = []
        for offset, spelling in enumerate(("Cnaser", "Shaser", "Lhaser", "haser")):
            frame = _fresh_row(f"Gained 1 hint level(s) for Pace {spelling}", box=box)
            frame["source_timestamp_ms"] = 1100 + 50 * offset
            frames.append(frame)
        promoted = scoped_observations([base], frames, plan([base], _owner(), 5000))
        self.assertEqual(promoted, [])

    def test_reread_amount_conflicting_with_clear_base_line_is_not_promoted(self):
        # Actual first-recording shape (80067-80283 ms): the base read "Energy went down by
        # 18." clearly in two frames, then the cursor hid the "1" and later
        # frames were blocked as "Energy went down by 8.".  A dense reread of
        # the blocked frames agrees with the damaged text, not with the clear
        # reading; it must not be promoted and must not displace the -18.
        box = [317, 808, 560, 835]
        blocked = _base_row("Energy went down by 8.", box=box)
        blocked["source_timestamp_ms"] = 1050
        clear = {
            "source_timestamp_ms": 1000,
            "screen": "event_outcome",
            "context_title": "Shogi",
            "evidence": "gameplay/clear-base.png",
            "effects": [{"kind": "energy_change", "amount": -18,
                         "raw_text": "Energy went down by 18.", "confidence": 97.2}],
            "facts": {},
            "ocr": {"neural": [{"text": "Energy went down by 18.", "confidence": 97.2,
                                "box": [316, 808, 559, 835]}]},
        }
        owner = _owner()
        owner[0]["last_seen_ms"] = 1050
        fresh = _fresh_row("Energy went down by8.", box=box)
        promoted = scoped_observations(
            [clear, blocked], [fresh], plan([clear, blocked], owner, 5000)
        )
        self.assertEqual(promoted, [])

    def test_clear_base_reading_past_owner_span_still_dedupes_same_panel(self):
        # Actual first-recording shape (536750-538167 ms): the owner span known at planning
        # time ended at 538000, but a registered inspection frame at 538167
        # still showed the same outcome panel and read "Fine Motion" clearly.
        # A dense reread of the blocked frames must not add a second
        # friendship effect (nor a damaged "Fine Mstion" variant).
        box = [315, 854, 718, 885]
        blocked = _base_row("Friendship with Fine Mstion went up by 5.", box=box)
        later_clear = {
            "source_timestamp_ms": 1400,
            "screen": "event_outcome",
            "context_title": "Shogi",
            "evidence": "receipt-inspection/later-clear.png",
            "effects": [{"kind": "friendship_change", "name": "Fine Motion", "amount": 5,
                         "raw_text": "Friendship with Fine Motion went up by 5.",
                         "confidence": 98.8}],
            "facts": {},
            "ocr": {"neural": [{"text": "Friendship with Fine Motion went up by 5.",
                                "confidence": 98.8, "box": [315, 853, 718, 885]}]},
        }
        owner = _owner()  # owner span 1000-1000 only
        good = _fresh_row("Friendship with Fine Motion went up by 5.", box=box)
        good["facts"]["occluded_receipt_lines"][0]["confidence"] = 98.3
        damaged = _fresh_row("Friendship with Fine Mstion went up by 5.", box=box)
        damaged["source_timestamp_ms"] = 1150
        damaged["facts"]["occluded_receipt_lines"][0]["confidence"] = 96.9
        promoted = scoped_observations(
            [blocked, later_clear], [good, damaged],
            plan([blocked, later_clear], owner, 5000),
        )
        self.assertEqual(promoted, [])

    def test_same_slot_name_variants_promote_one_reading_with_explicit_candidates(self):
        # Without any clear base reading, dense frames that spell the damaged
        # recipient differently are one physical observation.  Neither
        # spelling repeats, so confidence breaks the tie and both spellings
        # stay visible as observed candidates; frame order does not decide.
        box = [316, 878, 743, 908]
        base = _base_row("Friendship with Nishino Flwer went up by 7.", box=box)
        weaker = _fresh_row("Friendship with Nishino Flwer went up by 7.", box=box)
        weaker["facts"]["occluded_receipt_lines"][0]["confidence"] = 97.6
        stronger = _fresh_row("Friendship with Nishino Flower went up by 7.", box=box)
        stronger["source_timestamp_ms"] = 1150
        stronger["facts"]["occluded_receipt_lines"][0]["confidence"] = 98.4
        promoted = scoped_observations(
            [base], [weaker, stronger], plan([base], _owner(), 5000)
        )
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["source_timestamp_ms"], 1150)
        effect = promoted[0]["effects"][0]
        self.assertEqual(effect["name"], "Nishino Flower")
        self.assertEqual(effect["observed_name_candidates"],
                         ["Nishino Flower", "Nishino Flwer"])
        proof = effect["source_bound_receipt_proof"]
        self.assertEqual(proof["name_identity_basis"],
                         "strongest_reading_identity_unresolved")
        self.assertEqual(proof["distinct_spelling_count"], 2)

    def test_repeated_spelling_beats_a_single_more_confident_variant(self):
        # Actual first-recording shape (833250 ms): eight dense frames spelled "Director
        # Akikawa" eight different ways; the single most confident frame read
        # "Akilawa".  A spelling seen at two distinct source timestamps is
        # independent repetition and wins over one confident misread.
        box = [315, 846, 767, 878]
        base = _base_row("Friendship with Director Aki awa went up by 5.", box=box)
        frames = []
        for time, spelling, confidence in (
            (1100, "Director Akilawa", 99.2),
            (1150, "Director Akikawa", 98.9),
            (1200, "Director Akikawa", 98.5),
        ):
            frame = _fresh_row(f"Friendship with {spelling} went up by 5.", box=box)
            frame["source_timestamp_ms"] = time
            frame["facts"]["occluded_receipt_lines"][0]["confidence"] = confidence
            frames.append(frame)
        promoted = scoped_observations([base], frames, plan([base], _owner(), 5000))
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["source_timestamp_ms"], 1150)
        effect = promoted[0]["effects"][0]
        self.assertEqual(effect["name"], "Director Akikawa")
        self.assertEqual(effect["observed_name_candidates"],
                         ["Director Akikawa", "Director Akilawa"])
        self.assertEqual(effect["source_bound_receipt_proof"]["name_identity_basis"],
                         "repeated_spelling_across_source_timestamps")

    def test_unoccluded_recipient_reading_preferred_when_no_spelling_repeats(self):
        box = [315, 846, 767, 878]
        base = _base_row("Friendship with Director Aki awa went up by 5.", box=box)
        covered = _fresh_row("Friendship with Director Akilawa went up by 5.", box=box)
        covered["facts"]["occluded_receipt_lines"][0]["confidence"] = 99.2
        covered["facts"]["occluded_receipt_lines"][0]["recipient_name_occluded"] = True
        clear_name = _fresh_row("Friendship with Director Akikawa went up by 5.", box=box)
        clear_name["source_timestamp_ms"] = 1150
        clear_name["facts"]["occluded_receipt_lines"][0]["confidence"] = 97.1
        clear_name["facts"]["occluded_receipt_lines"][0]["recipient_name_occluded"] = False
        promoted = scoped_observations(
            [base], [covered, clear_name], plan([base], _owner(), 5000)
        )
        self.assertEqual(len(promoted), 1)
        effect = promoted[0]["effects"][0]
        self.assertEqual(effect["name"], "Director Akikawa")
        self.assertEqual(effect["source_bound_receipt_proof"]["name_identity_basis"],
                         "recipient_glyphs_not_under_cursor")
        self.assertEqual(effect["source_bound_receipt_proof"]["recipient_name_occluded"], False)

    def test_clear_reading_of_same_slot_suppresses_cursor_read_variant(self):
        # Actual first-recording shape (1322250-1322617 ms): three dense frames read
        # "Friendship with Light Hello is maxed out." unblocked, a later frame
        # read the same slot under the cursor as "Light Hllo".  The clear
        # reading is canonical; the cursor-read spelling is not a second
        # status line even though it comes later in the window.
        box = [318, 856, 705, 881]
        base = _base_row("Friendship with Light Hllo is maxed out.", box=box)
        clear = {
            "source_timestamp_ms": 1100,
            "screen": "event_outcome",
            "context_title": "Shogi",
            "evidence": "receipt-inspection/clear-1100.png",
            "effects": [{"kind": "friendship_status", "name": "Light Hello",
                         "value": "maximum", "amount": None,
                         "raw_text": "Friendship with Light Hello is maxed out.",
                         "confidence": 98.5}],
            "facts": {"occluded_receipt_lines": [_blocked_line(
                "Friendship with Light Hllo is maxed out.", box)]},
            "ocr": {"neural": [{"text": "Friendship with Light Hello is maxed out.",
                                "confidence": 98.5, "box": [318, 856, 705, 881]}]},
        }
        damaged = _fresh_row("Friendship with Light Hllo is maxed out.", box=box)
        damaged["source_timestamp_ms"] = 1150
        promoted = scoped_observations(
            [base], [clear, damaged], plan([base], _owner(), 5000)
        )
        self.assertEqual([row["source_timestamp_ms"] for row in promoted], [1100])
        self.assertEqual(
            [(effect["kind"], effect["name"]) for effect in promoted[0]["effects"]],
            [("friendship_status", "Light Hello")],
        )

    def test_plurality_of_source_timestamps_ranks_competing_repeated_spellings(self):
        # Actual first-recording shape (764250 ms): "Unstoppable" read at seven distinct
        # timestamps, "Ustoppable" and "Urstoppable" at two each.  With more
        # than one repeated spelling, strictly stronger repetition wins but the
        # identity stays explicitly unresolved with every spelling recorded.
        box = [317, 871, 691, 899]
        base = _base_row("Gained 3 hint level(s) for Unstoppable.", box=box)
        frames = []
        spellings = ["Unstoppable"] * 3 + ["Ustoppable"] * 2 + ["Urstoppable"] * 2
        for offset, spelling in enumerate(spellings):
            frame = _fresh_row(f"Gained 3 hint level(s) for {spelling}.", box=box)
            # Dense rereads sit a few ms apart inside the planned window.
            frame["source_timestamp_ms"] = 1100 + 10 * offset
            frame["facts"]["occluded_receipt_lines"][0]["confidence"] = (
                99.4 if spelling == "Ustoppable" else 97.5
            )
            frames.append(frame)
        promoted = scoped_observations([base], frames, plan([base], _owner(), 5000))
        self.assertEqual(len(promoted), 1)
        effect = promoted[0]["effects"][0]
        self.assertEqual(effect["name"], "Unstoppable")
        self.assertEqual(effect["source_bound_receipt_proof"]["name_identity_basis"],
                         "plurality_of_source_timestamps_identity_unresolved")
        self.assertEqual(sorted(effect["observed_name_candidates"]),
                         ["Unstoppable", "Urstoppable", "Ustoppable"])

    def test_independent_dense_frame_carries_proof_over_same_time_base_reread(self):
        # Actual first-recording shape (765000 ms): the strongest reread of the blocked
        # hint line came from a dense frame whose timestamp equals a base
        # capture frame.  Merging it would rewrite that base row (which other
        # source-bound caches bind); an equally supported reread from an
        # independent dense frame carries the proof instead.
        box = [317, 871, 691, 899]
        base = _base_row("Gained 3 hint level(s) for Unstop able.", box=box)
        same_time = _fresh_row("Gained 3 hint level(s) for Unstoppable.", box=box)
        same_time["source_timestamp_ms"] = 1000  # equals the base frame
        same_time["facts"]["occluded_receipt_lines"][0]["confidence"] = 99.5
        independent = _fresh_row("Gained 3 hint level(s) for Unstoppable.", box=box)
        independent["source_timestamp_ms"] = 1067
        independent["facts"]["occluded_receipt_lines"][0]["confidence"] = 97.9
        promoted = scoped_observations(
            [base], [same_time, independent], plan([base], _owner(), 5000)
        )
        self.assertEqual([row["source_timestamp_ms"] for row in promoted], [1067])
        self.assertEqual(promoted[0]["effects"][0]["name"], "Unstoppable")

    def _clear_row(self, time, text, box, effect, *, evidence=None):
        return {
            "source_timestamp_ms": time,
            "screen": "event_outcome",
            "context_title": "Shogi",
            "evidence": evidence or f"gameplay/clear-{time}.png",
            "effects": [effect],
            "facts": {},
            "ocr": {"neural": [{"text": text, "confidence": 99.0, "box": list(box)}]},
        }

    def test_heavily_damaged_reread_of_a_clearly_read_slot_is_not_promoted(self):
        # Actual independent-01 shape (933000-933767 ms): the base read
        # 'Friendship with Director Akikawa went up by 4.' unblocked, later
        # base frames were blocked as 'Dire: or kikawa', and the dense reread
        # repeated the damaged spelling on the same slot.  The spelling is too
        # damaged for the bounded name comparison, but the slot itself was
        # read clearly, so the reread is redundant, not a second recipient.
        box = [315, 902, 766, 931]
        clear = self._clear_row(
            1000, "Friendship with Director Akikawa went up by 4.", [314, 904, 768, 938],
            {"kind": "friendship_change", "name": "Director Akikawa", "amount": 4,
             "raw_text": "Friendship with Director Akikawa went up by 4.", "confidence": 98.2},
        )
        blocked = _base_row("Friendship with Dire: or kikawa went up by 4.", box=box)
        blocked["source_timestamp_ms"] = 1050
        owner = _owner()
        owner[0]["last_seen_ms"] = 1050
        fresh = _fresh_row("Friendship with Dire: or kikawa went up by 4.", box=box)
        promoted = scoped_observations(
            [clear, blocked], [fresh], plan([clear, blocked], owner, 5000)
        )
        self.assertEqual(promoted, [])

    def test_shifted_panel_variant_of_a_clearly_read_line_is_not_promoted(self):
        # Actual independent-01 shape (1447667-1448000 ms): the receipt bubble
        # moved up one line between frames, so the blocked 'Kitasan Bla' status
        # at y=856 and the clear 'Kitasan Black' status at y=832 share a column
        # but not a slot.  Same column plus a bounded spelling variant of the
        # clear name identifies the same line.
        blocked_box = [317, 856, 728, 881]
        clear = self._clear_row(
            1050, "Friendship with Kitasan Black is maxed out.", [317, 832, 728, 858],
            {"kind": "friendship_status", "name": "Kitasan Black", "value": "maximum",
             "amount": None, "raw_text": "Friendship with Kitasan Black is maxed out.",
             "confidence": 98.7},
        )
        blocked = _base_row("Friendship with Kitasan Bla is maxed out.", box=blocked_box)
        owner = _owner()
        owner[0]["last_seen_ms"] = 1050
        fresh = _fresh_row("Friendship with Kitasan Bla is maxed out.", box=blocked_box)
        promoted = scoped_observations(
            [blocked, clear], [fresh], plan([blocked, clear], owner, 5000)
        )
        self.assertEqual(promoted, [])

    def test_shifted_panel_different_recipient_same_amount_is_still_promoted(self):
        # Two recipients with the same gain are separate lines; a column match
        # alone must not dedupe them when the names are unrelated.
        blocked_box = [317, 856, 728, 881]
        clear = self._clear_row(
            1050, "Friendship with Kitasan Black went up by 7.", [317, 832, 728, 858],
            {"kind": "friendship_change", "name": "Kitasan Black", "amount": 7,
             "raw_text": "Friendship with Kitasan Black went up by 7.", "confidence": 98.7},
        )
        blocked = _base_row("Friendship with Fine Motion went up by 7.", box=blocked_box)
        owner = _owner()
        owner[0]["last_seen_ms"] = 1050
        fresh = _fresh_row("Friendship with Fine Motion went up by 7.", box=blocked_box)
        promoted = scoped_observations(
            [blocked, clear], [fresh], plan([blocked, clear], owner, 5000)
        )
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["effects"][0]["name"], "Fine Motion")

    def test_scrolled_clear_reading_with_other_amount_vetoes_reread(self):
        # Actual independent-01 shape (1101500-1102750 ms): the inheritance
        # receipt scrolls; the base read 'Power cap went up by 11.' clearly at
        # y=807 while the blocked frames showed the same line at y=853 with the
        # cursor over the second digit ('by 1.').  A dense reread that repeats
        # the damaged amount must not be promoted (and must not displace 11).
        blocked_box = [316, 853, 566, 882]
        clear = self._clear_row(
            1050, "Power cap went up by 11.", [317, 807, 565, 834],
            {"kind": "stat_cap_change", "field": "power", "amount": 11,
             "raw_text": "Power cap went up by 11.", "confidence": 99.5},
        )
        blocked = _base_row("Power cap went up by 1.", box=blocked_box)
        owner = _owner()
        owner[0]["last_seen_ms"] = 1050
        fresh = _fresh_row("Power cap went up by 1.", box=blocked_box)
        promoted = scoped_observations(
            [blocked, clear], [fresh], plan([blocked, clear], owner, 5000)
        )
        self.assertEqual(promoted, [])

    def test_scrolled_clear_reading_of_another_field_is_not_a_conflict(self):
        blocked_box = [316, 853, 566, 882]
        clear = self._clear_row(
            1050, "Wit cap went up by 1.", [317, 807, 565, 834],
            {"kind": "stat_cap_change", "field": "wit", "amount": 1,
             "raw_text": "Wit cap went up by 1.", "confidence": 99.5},
        )
        blocked = _base_row("Power cap went up by 11.", box=blocked_box)
        owner = _owner()
        owner[0]["last_seen_ms"] = 1050
        fresh = _fresh_row("Power cap went up by 11.", box=blocked_box)
        promoted = scoped_observations(
            [blocked, clear], [fresh], plan([blocked, clear], owner, 5000)
        )
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["effects"][0]["amount"], 11)

    def test_non_outcome_preview_screen_cannot_promote_receipt_effect(self):
        box = [317, 808, 542, 835]
        base = _base_row("Energy recovered by 9.", box=box)
        fresh = _fresh_row("Energy recovered by 9.", box=box, screen="training_preview")

        self.assertEqual(
            scoped_observations([base], [fresh], plan([base], _owner(), 5000)),
            [],
        )


if __name__ == "__main__":
    unittest.main()
