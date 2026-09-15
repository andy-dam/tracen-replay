import unittest

from tracen_replay.gameplay import effects_from_lines
from tracen_replay.transactions import lesson_receipts
from tracen_replay.vision import parse


def line(text, box=(310, 807, 700, 837), confidence=99):
    return dict(text=text, box=list(box), confidence=confidence)


def raw(lines):
    return dict(lines=list(lines), regions={}, header="Career",
                current_grid=False, result_grid=False)


class OutcomeEffectGrammarTests(unittest.TestCase):
    def test_named_item_delivery_is_an_item_reward_with_unknown_quantity(self):
        effects = effects_from_lines([
            line("You've won third-prize!", box=(310, 805, 761, 836)),
            line("Here's your carrot!"),
        ])
        self.assertEqual(len(effects), 1)
        self.assertEqual(
            effects[0],
            {
                "kind": "item_reward",
                "name": "carrot",
                "quantity": None,
                "quantity_observed": False,
                "raw_text": "Here's your carrot!",
                "confidence": 99,
            },
        )

    def test_curly_apostrophe_and_period_are_supported_without_item_aliases(self):
        effects = effects_from_lines([
            line("You've received a reward!", box=(310, 805, 761, 836)),
            line("Here’s your mystery item."),
        ])
        self.assertEqual(effects[0]["kind"], "item_reward")
        self.assertEqual(effects[0]["name"], "mystery item")
        self.assertIsNone(effects[0]["quantity"])

    def test_incomplete_item_delivery_does_not_become_a_reward(self):
        award = line("You've won third-prize!", box=(310, 805, 761, 836))
        self.assertEqual(effects_from_lines([award, line("Here's your carrot")]), [])
        self.assertEqual(effects_from_lines([award, line("Here's your")]), [])

    def test_item_wording_without_delivery_context_is_ordinary_dialogue(self):
        for text in ("Here's your chance!", "Here's your answer.", "Here's your reward"):
            with self.subTest(text=text):
                self.assertEqual(effects_from_lines([line(text)]), [])

    def test_noncontiguous_award_dialogue_does_not_authorize_item(self):
        lines = [
            line("You've won third-prize!", box=(310, 805, 761, 836)),
            line("A new conversation starts here.", box=(310, 850, 700, 880)),
            line("Here's your carrot!", box=(310, 890, 491, 917)),
        ]
        self.assertEqual(effects_from_lines(lines), [])

    def test_negated_or_conditional_award_dialogue_does_not_authorize_item(self):
        for award in (
            "You haven't won a prize!",
            "You have not won a prize!",
            "If you won a prize!",
            "You would have won a prize!",
        ):
            with self.subTest(award=award):
                lines = [
                    line(award, box=(310, 805, 761, 836)),
                    line("Here's your carrot!"),
                ]
                self.assertEqual(effects_from_lines(lines), [])

    def test_award_language_outside_receipt_geometry_does_not_authorize_item(self):
        lines = [
            line("You've won third-prize!", box=(310, 600, 761, 631)),
            line("Here's your carrot!", box=(310, 805, 491, 835)),
        ]
        self.assertEqual(effects_from_lines(lines), [])

    def test_incomplete_skill_level_receipt_does_not_become_a_reward(self):
        self.assertEqual(effects_from_lines([line("Festive Miracle leveled")]), [])

    def test_skill_level_receipt_can_end_without_terminal_punctuation(self):
        effects = effects_from_lines([line("Festive Miracle leveled up")])
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["kind"], "skill_level_change")
        self.assertEqual(effects[0]["name"], "Festive Miracle")
        self.assertEqual(effects[0]["direction"], "up")

    def test_level_up_and_item_reward_survive_neural_parse(self):
        item = parse(raw([
            line("Raffle Clerk", box=(379, 765, 506, 792)),
            line("Would you lookie here! You've won third-prize!", box=(309, 805, 761, 836)),
            line("Here's your carrot!"),
        ]))
        skill = parse(raw([line("Festive Miracle leveled up")]))
        self.assertEqual(item["screen"], "event_outcome")
        self.assertEqual(item["effects"][0]["kind"], "item_reward")
        self.assertEqual(skill["screen"], "event_outcome")
        self.assertEqual(skill["effects"][0]["kind"], "skill_level_change")

    def test_ordinary_dialogue_does_not_become_item_reward(self):
        for text in ("Here's your chance!", "Here's your answer."):
            with self.subTest(text=text):
                got = parse(raw([
                    line("Trainer", box=(379, 765, 506, 792)),
                    line(text),
                ]))
                self.assertEqual(got["effects"], [])

    def test_item_reward_is_not_joined_to_lesson_transactions(self):
        row = dict(source_timestamp_ms=1000, screen="event_outcome",
                   evidence="receipt.png", effects=[dict(
                       kind="item_reward", name="carrot", quantity=None,
                       quantity_observed=False
                   )], facts={})
        self.assertEqual(lesson_receipts([row], [row]), [])


if __name__ == "__main__":
    unittest.main()
