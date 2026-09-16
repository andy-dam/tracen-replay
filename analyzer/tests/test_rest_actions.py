import copy
import unittest

from tracen_replay.rest_actions import reconstruct


PROMPT = (
    "Take the day off to let your trainee recover energy?",
    "This will take up the entire turn.",
)


def ocr(*texts, confidence=99):
    return {"neural": [{"text": text, "confidence": confidence}
                         for text in texts]}


def row(time, evidence, *, screen="unknown", calendar=None, turns=None,
        context=None, texts=(), effects=(), completed_action=None,
        confidence=99):
    return {
        "source_timestamp_ms": time,
        "evidence": evidence,
        "screen": screen,
        "context_title": context,
        "stats": {"calendar_text": calendar,
                  "turns_remaining_to_goal": turns},
        "ocr": ocr(*texts, confidence=confidence),
        "effects": [dict(effect) for effect in effects],
        "completed_action": completed_action,
    }


def event(first, last, *, amount=50, event_id="rest-event"):
    return {
        "id": event_id,
        "kind": "outcome",
        "first_seen_ms": first,
        "last_seen_ms": last,
        "context_title": "A source-visible but arbitrary result",
        "evidence": "event.png",
        "effects": [{"kind": "energy_change", "amount": amount,
                     "raw_text": f"Energy recovered by {amount}."}],
    }


def recovery(amount=50):
    return {"kind": "energy_change", "amount": amount,
            "raw_text": f"Energy recovered by {amount}."}


def dated_sequence():
    amount = recovery()
    readings = [
        row(500, "before-a.png", calendar="Senior Year Late Apr"),
        row(750, "before-b.png", calendar="Senior Year Late Apr"),
        row(1000, "confirm-a.png", screen="rest_confirmation",
            texts=PROMPT),
        row(1250, "confirm-b.png", screen="rest_confirmation",
            texts=PROMPT),
        row(1500, "bridge.png", calendar="Senior Year Late Apr"),
        row(2000, "result-a.png", screen="event_outcome",
            calendar="Senior Year Late Apr", texts=("Energy recovered by 50.",),
            effects=(amount,)),
        row(2250, "result-b.png", screen="event_outcome",
            calendar="Senior Year Late Apr", texts=("Energy recovered by 50.",),
            effects=(amount,)),
        # Scenario dialogue after the recovery result is not a competing turn.
        row(2500, "scenario.png", screen="event_outcome",
            calendar="Senior Year Late Apr", context="A later scenario"),
        row(2750, "next-a.png", calendar="Senior Year Early May"),
        row(3000, "next-b.png", calendar="Senior Year Early May"),
    ]
    return readings, [event(2000, 2250)]


def phase_sequence():
    amount = recovery(30)
    readings = [
        row(1000, "before-a.png", calendar="Junior Year Pre-Debut", turns=8),
        row(1250, "before-b.png", calendar="Junior Year Pre-Debut", turns=8),
        row(1500, "confirm-a.png", screen="rest_confirmation",
            texts=PROMPT),
        row(1750, "confirm-b.png", screen="rest_confirmation",
            texts=PROMPT),
        row(2000, "result-a.png", screen="event_outcome",
            calendar="Junior Year Pre-Debut", turns=8,
            texts=("Energy recovered by 30.",), effects=(amount,)),
        row(2250, "result-b.png", screen="event_outcome",
            calendar="Junior Year Pre-Debut", turns=8,
            texts=("Energy recovered by 30.",), effects=(amount,)),
        row(2500, "scenario-a.png", screen="event_outcome",
            calendar="Junior Year Pre-Debut", context="Scenario dialogue"),
        row(2750, "scenario-b.png", calendar="Junior Year Pre-Debut",
            context="Scenario dialogue"),
        row(3000, "after-a.png", calendar="Junior Year Pre-Debut", turns=7),
        row(3250, "after-b.png", calendar="Junior Year Pre-Debut", turns=7),
    ]
    return readings, [event(2000, 2250, amount=30)]


def goal_race_followup_sequence():
    amount = recovery()
    readings = [
        row(1000, "phase-a.png", calendar="Junior Year Pre-Debut"),
        row(1250, "phase-b.png", calendar="Junior Year Pre-Debut"),
        row(1500, "confirm-a.png", screen="rest_confirmation",
            texts=PROMPT),
        row(1750, "confirm-b.png", screen="rest_confirmation",
            texts=PROMPT),
        # The goal countdown is still 1 after the accepted Rest prompt.  The
        # mandatory debut race then occurs before the next concrete date.
        row(2000, "countdown-a.png", calendar="Junior Year Pre-Debut",
            turns=1),
        row(2250, "countdown-b.png", calendar="Junior Year Pre-Debut",
            turns=1),
        row(2500, "result-a.png", screen="event_outcome",
            calendar="Junior Year Pre-Debut",
            texts=("Energy recovered by 50.",), effects=(amount,)),
        row(2750, "result-b.png", screen="event_outcome",
            calendar="Junior Year Pre-Debut",
            texts=("Energy recovered by 50.",), effects=(amount,)),
        row(3000, "scenario.png", screen="event_outcome",
            calendar="Junior Year Pre-Debut", context="A later scenario"),
        row(3250, "race-a.png", screen="race_result"),
        row(3500, "race-b.png", screen="race_result"),
        row(3750, "next-a.png", calendar="Junior Year Early Jul"),
        row(4000, "next-b.png", calendar="Junior Year Early Jul"),
    ]
    return readings, [event(2500, 2750)]


def lessons_then_next_date_sequence(*, interlude_screen="lesson_selection"):
    """A Rest whose next date only shows after a long stretch of lessons."""
    amount = recovery(30)
    readings = [
        row(500, "before-a.png", calendar="Senior Year Late Jun"),
        row(750, "before-b.png", calendar="Senior Year Late Jun"),
        row(1000, "confirm-a.png", screen="rest_confirmation", texts=PROMPT),
        row(1250, "confirm-b.png", screen="rest_confirmation", texts=PROMPT),
        row(2000, "result-a.png", screen="event_outcome",
            calendar="Senior Year Late Jun", texts=("Energy recovered by 30.",),
            effects=(amount,)),
        row(2250, "result-b.png", screen="event_outcome",
            calendar="Senior Year Late Jun", texts=("Energy recovered by 30.",),
            effects=(amount,)),
    ]
    # Forty-five seconds of point spending, one screen every five seconds.
    readings += [row(5000 + i * 5000, f"lesson-{i}.png", screen=interlude_screen)
                 for i in range(9)]
    readings += [
        row(52000, "next-a.png", calendar="Senior Year Early Jul"),
        row(52250, "next-b.png", calendar="Senior Year Early Jul"),
    ]
    return readings, [event(2000, 2250, amount=30)]


class RestActionTests(unittest.TestCase):
    def test_lessons_and_concert_after_the_result_do_not_lose_the_rest(self):
        readings, events = lessons_then_next_date_sequence()
        actions = reconstruct(readings, events)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["next_calendar"], "Senior Year Early Jul")
        self.assertEqual(actions[0]["post_result_interlude_end_ms"], 45000)
        readings, events = lessons_then_next_date_sequence(interlude_screen="concert_confirmation")
        self.assertEqual(len(reconstruct(readings, events)), 1)
        # A gap longer than the boundary wait, or a turn action in between,
        # still leaves the Rest unproved.
        readings, events = lessons_then_next_date_sequence()
        readings = [r for r in readings if not (10000 <= r["source_timestamp_ms"] <= 40000)]
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = lessons_then_next_date_sequence(interlude_screen="training_preview")
        self.assertEqual(reconstruct(readings, events), [])

    def test_dated_rest_requires_prompt_receipt_and_next_date(self):
        readings, events = dated_sequence()
        original = copy.deepcopy(readings)
        actions = reconstruct(readings, events)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["kind"], "rest")
        self.assertEqual(action["source_timestamp_ms"], 2000)
        self.assertEqual(action["boundary_kind"], "calendar_date")
        self.assertEqual(action["next_date_timestamp_ms"], 2750)
        self.assertEqual(action["recovery_effects"][0]["amount"], 50)
        self.assertEqual(action["confirmation_evidence"],
                         ["confirm-a.png", "confirm-b.png"])
        self.assertEqual(action["result_evidence"],
                         ["result-a.png", "result-b.png"])
        self.assertEqual(readings, original)

    def test_phase_countdown_closes_predebut_rest_after_scenario_dialogue(self):
        readings, events = phase_sequence()
        actions = reconstruct(readings, events)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["boundary_kind"], "phase_countdown")
        self.assertEqual((action["turns_before"], action["turns_after"]),
                         (8, 7))
        self.assertEqual(action["next_date_timestamp_ms"], 3000)
        self.assertEqual(action["turn_transition_evidence"],
                         ["before-a.png", "before-b.png", "after-a.png",
                          "after-b.png"])

    def test_recovery_uses_shared_energy_grammar(self):
        from tracen_replay.gameplay import effects_from_lines
        readings, events = dated_sequence()
        for item in readings[5:7]:
            item['ocr'] = ocr('Energy went up by 50.')
            item['effects'] = effects_from_lines(item['ocr']['neural'])
        events[0]['effects'] = copy.deepcopy(readings[5]['effects'])
        self.assertEqual(len(reconstruct(readings, events)), 1)

    def test_conflicting_source_energy_amounts_cannot_confirm_rest(self):
        readings, events = dated_sequence()
        for item in readings[5:7]:
            item['ocr'] = ocr('Energy recovered by 50.', 'Energy went down by 10.')
        self.assertEqual(reconstruct(readings, events), [])

    def test_phase_to_calendar_boundary_is_supported_when_countdown_is_unreadable(self):
        readings, events = phase_sequence()
        for item in readings[-2:]:
            item["stats"] = {"calendar_text": "Junior Year Early Jul",
                             "turns_remaining_to_goal": None}
        action = reconstruct(readings, events)[0]
        self.assertEqual(action["boundary_kind"], "phase_date")
        self.assertEqual(action["next_calendar"], "Junior Year Early Jul")
        self.assertEqual(action["next_date_timestamp_ms"], 3000)

    def test_goal_race_after_completed_rest_is_a_later_action(self):
        readings, events = goal_race_followup_sequence()
        actions = reconstruct(readings, events)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["source_timestamp_ms"], 2500)
        self.assertEqual(action["post_result_action_kind"], "goal_race")
        self.assertEqual(action["next_date_timestamp_ms"], 3750)
        self.assertEqual(
            action["post_result_action_evidence"],
            ["countdown-a.png", "countdown-b.png", "race-a.png", "race-b.png"],
        )

    def test_later_race_does_not_open_bridge_without_final_goal_countdown(self):
        readings, events = goal_race_followup_sequence()
        for item in readings:
            if item["evidence"] in {"countdown-a.png", "countdown-b.png"}:
                item["stats"]["turns_remaining_to_goal"] = 2
        self.assertEqual(reconstruct(readings, events), [])

    def test_competing_action_still_breaks_goal_race_bridge(self):
        readings, events = goal_race_followup_sequence()
        readings.insert(3125, row(3125, "training.png", screen="training_result"))
        self.assertEqual(reconstruct(readings, events), [])

    def test_missing_source_prompt_is_not_replaced_by_screen_label(self):
        readings, events = dated_sequence()
        readings[2]["ocr"] = ocr("Rest", "Cancel", "OK")
        readings[3]["ocr"] = ocr("Rest", "Cancel", "OK")
        self.assertEqual(reconstruct(readings, events), [])

    def test_repeated_result_requires_distinct_timestamps_and_evidence(self):
        readings, events = dated_sequence()
        readings[6]["source_timestamp_ms"] = readings[5]["source_timestamp_ms"]
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = dated_sequence()
        readings[6]["evidence"] = readings[5]["evidence"]
        self.assertEqual(reconstruct(readings, events), [])

    def test_event_effect_without_source_receipts_is_not_a_rest(self):
        readings, events = dated_sequence()
        for result in readings[5:7]:
            result["effects"] = []
            result["ocr"] = ocr("Result")
        self.assertEqual(reconstruct(readings, events), [])

    def test_low_confidence_prompt_or_receipt_stays_unknown(self):
        readings, events = dated_sequence()
        readings[2]["ocr"] = ocr(*PROMPT, confidence=89)
        readings[3]["ocr"] = ocr(*PROMPT, confidence=89)
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = dated_sequence()
        readings[5]["ocr"] = ocr("Energy recovered by 50.", confidence=89)
        self.assertEqual(reconstruct(readings, events), [])

    def test_cancellation_and_competing_action_break_the_path(self):
        readings, events = dated_sequence()
        readings.insert(4, row(1750, "cancel.png", texts=("Rest was cancelled.",)))
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = dated_sequence()
        readings.insert(4, row(1750, "training.png", screen="training_result"))
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = dated_sequence()
        readings.insert(4, row(1750, "other-event.png", screen="event_outcome",
                                context="Another event"))
        self.assertEqual(reconstruct(readings, events), [])

    def test_missing_or_invalid_next_boundary_stays_unknown(self):
        readings, events = dated_sequence()
        readings[-2]["stats"]["calendar_text"] = "Senior Year Late Apr"
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = phase_sequence()
        readings[-2]["stats"]["turns_remaining_to_goal"] = 8
        readings[-1]["stats"]["turns_remaining_to_goal"] = 8
        self.assertEqual(reconstruct(readings, events), [])


if __name__ == "__main__":
    unittest.main()
