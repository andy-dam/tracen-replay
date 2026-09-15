import unittest

from tests.test_neural_transactions import row
from tracen_replay.transactions import skill_transactions
from tracen_replay.evaluation_adapters import _skill_purchase_payload


def fixture():
    states = [dict(first_seen_ms=0, last_seen_ms=0,
                   values={'skill_points':1000}, evidence='before.png')]
    readings = [row(250, 'skill_selection', {'displayed_skill_points':700}),
                row(500, 'skill_selection', {'displayed_skill_points':700}),
                row(750, 'skill_confirmation'), row(1000, 'skill_receipt'),
                row(1250, 'skill_selection', {'displayed_skill_points':700}),
                row(1500, 'skill_selection', {'displayed_skill_points':700})]
    return readings, states


class SkillPostBalanceTests(unittest.TestCase):
    def test_verified_cart_post_balance_is_preserved_without_rederiving(self):
        readings, states = fixture()
        batch = skill_transactions(readings, states)[0]
        self.assertEqual(batch['spent_skill_points'], 300)
        after = next(p for p in batch['balance_evidence'] if p['role'] == 'after')
        self.assertEqual(after['skill_points'], 700)
        self.assertEqual(after['evidence'], [r['evidence'] for r in readings[-2:]])
        self.assertEqual(after['first_seen_ms'], 1250)
        payload, proofs = _skill_purchase_payload(batch)
        self.assertEqual(payload['skill_points_after'], 700)
        self.assertEqual(proofs, after['evidence'])
        self.assertFalse(batch['complete_transaction_verified'])

    def test_preview_only_balance_does_not_become_post_purchase_state(self):
        readings, states = fixture()
        batch = skill_transactions(readings[:-2], states)[0]
        self.assertNotIn('after', [p['role'] for p in batch['balance_evidence']])
        self.assertNotIn('skill_points_after', _skill_purchase_payload(batch)[0])

    def test_disagreeing_post_balances_stay_unknown(self):
        readings, states = fixture()
        readings[-1]['facts']['displayed_skill_points'] = 699
        batch = skill_transactions(readings, states)[0]
        self.assertIsNone(batch['spent_skill_points'])
        self.assertNotIn('skill_points_after', _skill_purchase_payload(batch)[0])


if __name__ == '__main__':
    unittest.main()
