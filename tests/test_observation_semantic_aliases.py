import copy
import unittest

from tracen_replay.observation_evaluate import _equal, canonical


class ObservationSemanticAliasTests(unittest.TestCase):
    def test_typographic_quotes_are_display_aliases(self):
        self.assertTrue(_equal("Getaway! Fallin’ Love", "Getaway! Fallin' Love"))
        self.assertTrue(_equal('Learned the song “Present March”',
                               'Learned the song "Present March"'))

    def test_acquired_condition_kind_supplies_explicit_status(self):
        payload = {
            "kind": "condition_acquired",
            "name": "Hot Topic",
        }
        self.assertEqual(
            canonical(payload),
            {
                "kind": "condition_change",
                "name": "Hot Topic",
                "value": "acquired",
            },
        )
        self.assertEqual(payload, {
            "kind": "condition_acquired",
            "name": "Hot Topic",
        })

    def test_explicit_condition_value_is_not_overwritten(self):
        payload = {
            "kind": "condition_acquired",
            "name": "Hot Topic",
            "value": "removed",
        }
        self.assertEqual(canonical(payload)["value"], "removed")

    def test_hype_display_aliases_share_source_enums(self):
        self.assertEqual(
            canonical({"kind": "hype_status", "value": "Mild"})["value"],
            "mild_hype",
        )
        for value in ("maxed", "maxed out", "maximum", "MAXED_OUT"):
            with self.subTest(value=value):
                self.assertEqual(
                    canonical({"kind": "hype_status", "value": value})["value"],
                    "maxed",
                )

    def test_unknown_hype_enum_remains_unknown(self):
        payload = {"kind": "hype_status", "value": "high"}
        self.assertEqual(canonical(payload), payload)

    def test_modifier_value_alias_becomes_amount_without_balance_inference(self):
        payload = {
            "kind": "training_modifier_change",
            "field": "friendship_training_effectiveness",
            "value": 5,
            "source_offer": "Present March ♪",
        }
        original = copy.deepcopy(payload)
        self.assertEqual(
            canonical(payload),
            {
                "kind": "training_modifier_change",
                "field": "friendship_training_effectiveness",
                "amount": 5,
                "source_offer": "Present March ♪",
            },
        )
        self.assertEqual(payload, original)

    def test_modifier_amount_wins_when_both_names_are_present(self):
        payload = {
            "kind": "training_modifier_change",
            "field": "friendship_training_effectiveness",
            "amount": 5,
            "value": 9,
        }
        self.assertEqual(canonical(payload)["amount"], 5)
        self.assertEqual(canonical(payload)["value"], 9)


if __name__ == "__main__":
    unittest.main()
