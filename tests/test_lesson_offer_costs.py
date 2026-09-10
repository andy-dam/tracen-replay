"""Tests for source-backed lesson price/projection joins."""

from __future__ import annotations

import copy
import unittest

from tracen_replay.gameplay import CURRENCIES
from tracen_replay.lesson_offer_costs import join_lesson_cost


NAME = "Zero Is Where the Center Stands!"
INITIAL = dict(zip(CURRENCIES, (38, 7, 46, 21, 63)))
PROJECTED = dict(zip(CURRENCIES, (17, 7, 46, None, 63)))
OFFER = dict(zip(CURRENCIES, (21, None, None, 21, 0)))


def row(timestamp, screen, facts=None, effects=()):
    return dict(
        source_timestamp_ms=timestamp,
        evidence=f"{timestamp}.png",
        screen=screen,
        facts=facts or {},
        effects=list(effects),
    )


def offer(name=NAME, prices=OFFER, card_index=2):
    return dict(
        card_index=card_index,
        title={"text": name, "confidence": 99.5},
        prices=[
            dict(
                field=field,
                value=prices[field],
                status="accepted" if prices[field] is not None else "unknown",
                **({"confidence": 99.5} if prices[field] is not None else {}),
            )
            for field in CURRENCIES
        ],
    )


def sequence(*, offer_values=None, offer_name=NAME):
    values = OFFER if offer_values is None else offer_values
    before = [
        row(
            timestamp,
            "lesson_selection",
            {"performance_points": INITIAL.copy(), "lesson_offer_observations": [offer(offer_name, values)]},
        )
        for timestamp in (0, 250)
    ]
    group = [
        row(
            timestamp,
            "lesson_confirmation",
            {"name_candidates": [NAME], "projected_performance_points": PROJECTED.copy()},
        )
        for timestamp in (500, 750)
    ]
    event = dict(
        id="outcome-1",
        first_seen_ms=1000,
        last_seen_ms=1250,
        evidence="receipt.png",
        effects=[dict(kind="named_acquisition", name=NAME)],
    )
    return before + group, event, group, before, INITIAL.copy()


class LessonOfferCostTests(unittest.TestCase):
    def test_joins_projection_and_repeated_offer_price_without_creating_purchase(self):
        readings, event, group, before, initial = sequence()
        got = join_lesson_cost(readings, event, group, before, initial)

        self.assertIsNotNone(got)
        self.assertEqual(got["cost"], dict(zip(CURRENCIES, (21, 0, 0, 21, 0))))
        self.assertEqual(got["total_cost"], 42)
        self.assertEqual(got["fields"]["dance"]["basis"],
                         "repeated_request_projection_difference_confirmed_by_repeated_offer_price")
        self.assertEqual(got["fields"]["visual"]["basis"], "repeated_named_offer_price")
        self.assertEqual(got["fields"]["visual"]["offer_price"], 21)
        self.assertEqual(got["offer"]["timestamps_ms"], [0, 250])
        self.assertEqual(got["request"]["timestamps_ms"], [500, 750])
        self.assertNotIn("purchase", got)

    def test_rejects_conflicting_known_offer_and_projection(self):
        values = OFFER.copy()
        values["visual"] = 20
        readings, event, group, before, initial = sequence(offer_values=values)
        for request_row in group:
            request_row["facts"]["projected_performance_points"]["visual"] = 0
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_conflicting_repeated_offer_prices(self):
        readings, event, group, before, initial = sequence()
        before[1]["facts"]["lesson_offer_observations"][0]["prices"][0]["value"] = 22
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_requires_two_distinct_offer_timestamps(self):
        readings, event, group, before, initial = sequence()
        before.pop(1)
        readings = [item for item in readings if item["source_timestamp_ms"] != 250]
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_duplicate_timestamp_does_not_count_as_repetition(self):
        readings, event, group, before, initial = sequence()
        duplicate = copy.deepcopy(before[0])
        before[1] = duplicate
        readings = [item for item in readings if item["source_timestamp_ms"] != 250]
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_same_offer_image_at_distinct_timestamps_does_not_count_as_repetition(self):
        readings, event, group, before, initial = sequence()
        before[1]["evidence"] = before[0]["evidence"]
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_requires_two_matching_initial_balance_rows(self):
        readings, event, group, before, initial = sequence()
        before[1]["facts"]["performance_points"]["dance"] -= 1
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_unknown_name_row_still_participates_in_projection_consistency(self):
        readings, event, group, before, initial = sequence()
        extra = row(
            625,
            "unknown",
            {
                "name_candidates": [],
                "projected_performance_points": dict(PROJECTED, visual=1),
            },
        )
        group.append(extra)
        readings.append(extra)
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_unusable_offer_metadata(self):
        for mutation in (
            lambda card: card["title"].update(confidence=96),
            lambda card: card.update(unknown_reasons=["ambiguous_cost_label"]),
            lambda card: card.update(unknown_reasons=["clipped_or_invalid_cost_row"]),
            lambda card: card["prices"][0].update(status="unknown"),
        ):
            readings, event, group, before, initial = sequence()
            for menu_row in before:
                mutation(menu_row["facts"]["lesson_offer_observations"][0])
            self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_cost_label_with_low_confidence(self):
        readings, event, group, before, initial = sequence()
        for menu_row in before:
            card = menu_row["facts"]["lesson_offer_observations"][0]
            card["cost_label"] = {"text": "Performance Point Cost", "confidence": 96}
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_requires_two_distinct_request_titles(self):
        readings, event, group, before, initial = sequence()
        group.pop()
        readings = [item for item in readings if item["source_timestamp_ms"] != 750]
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_request_title_conflict(self):
        readings, event, group, before, initial = sequence()
        group[1]["facts"]["name_candidates"] = ["Other Lesson"]
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_duplicate_named_receipt(self):
        readings, event, group, before, initial = sequence()
        event["effects"].append(dict(kind="named_acquisition", name=NAME))
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_other_screen_between_request_and_receipt(self):
        readings, event, group, before, initial = sequence()
        readings.insert(4, row(875, "training_result", {"training_gains": {"speed": 5}}))
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_sustained_return_to_menu_or_other_receipt(self):
        readings, event, group, before, initial = sequence()
        readings.insert(4, row(800, "lesson_selection", {"performance_points": INITIAL.copy()}))
        readings.insert(5, row(900, "lesson_selection", {"performance_points": INITIAL.copy()}))
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_allows_one_nearby_transition_menu_frame(self):
        readings, event, group, before, initial = sequence()
        readings.insert(4, row(875, "lesson_selection", {"performance_points": INITIAL.copy()}))
        self.assertIsNotNone(join_lesson_cost(readings, event, group, before, initial))

    def test_missing_field_stays_unresolved_without_inventing_zero(self):
        readings, event, group, before, initial = sequence()
        for menu_row in before:
            menu_row["facts"]["lesson_offer_observations"][0]["prices"][3]["value"] = None
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

    def test_rejects_unaffordable_offer_and_nonpositive_total(self):
        readings, event, group, before, initial = sequence()
        values = OFFER.copy()
        values["visual"] = 22
        readings, event, group, before, initial = sequence(offer_values=values)
        self.assertIsNone(join_lesson_cost(readings, event, group, before, initial))

        readings, event, group, before, initial = sequence()
        zero_initial = dict.fromkeys(CURRENCIES, 1)
        for menu_row in before:
            menu_row["facts"]["performance_points"] = zero_initial.copy()
        for request_row in group:
            request_row["facts"]["projected_performance_points"] = zero_initial.copy()
        self.assertIsNone(join_lesson_cost(readings, event, group, before, zero_initial))

    def test_known_offer_can_fill_field_when_projection_is_unknown(self):
        readings, event, group, before, initial = sequence()
        for request_row in group:
            request_row["facts"]["projected_performance_points"]["visual"] = None
        got = join_lesson_cost(readings, event, group, before, initial)
        self.assertIsNotNone(got)
        self.assertEqual(got["cost"]["visual"], 21)
        self.assertIsNone(got["fields"]["visual"]["projected_value"])
        self.assertEqual(got["fields"]["visual"]["offer_evidence"][0]["timestamp_ms"], 0)

    def test_inputs_are_not_mutated(self):
        readings, event, group, before, initial = sequence()
        original = copy.deepcopy((readings, event, group, before, initial))
        join_lesson_cost(readings, event, group, before, initial)
        self.assertEqual((readings, event, group, before, initial), original)


if __name__ == "__main__":
    unittest.main()
