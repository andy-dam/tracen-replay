"""A skill purchase priced by the balances the menu shows with its cart empty, when the purchase agrees."""
import unittest

from tests.test_neural_transactions import row
from tracen_replay.transactions import skill_transactions


def card(name, cost, status):
    return dict(name=name, displayed_cost=cost, menu_status=status, variant='single_circle')


def menu(t, value, *cards):
    facts = {'skill_cards': list(cards)}
    if value is not None:
        facts['displayed_skill_points'] = value
    return row(t, 'skill_selection', facts)


def finish(t, points):
    return row(t, 'career_finish_confirmation', ocr={'neural': [dict(text='Remaining Skill Points', confidence=99, box=[0, 0, 1, 1]),
                                                                dict(text=f'{points} pts', confidence=99, box=[0, 0, 1, 1])]})


def career_end():
    """A lesson's skill points just before the menu, then two purchases; no state after the first one."""
    first, second = card('First', 200, 'available'), card('Second', 100, 'available')
    rows = [row(100, 'event_outcome', effects=[dict(kind='stat_change', field='skill_points', amount=12)]),
            row(250, 'lesson_selection'),
            # The menu opens with its cart empty, and the counter is read on two frames.
            menu(500, 1012, first, second), menu(750, 1012, first, second),
            menu(1000, 812, card('First', None, 'obtained_or_selected'), second),
            menu(1250, 812, card('First', None, 'obtained_or_selected'), second),
            row(1500, 'skill_confirmation', {'visible_skill_names': ['First']}), row(1750, 'skill_receipt'),
            # Right after the receipt the cart is empty again.
            menu(2000, 812, second), menu(2250, 812, second),
            # The second cart's last counter was never read.
            menu(2500, None, card('Second', None, 'obtained_or_selected')),
            row(2750, 'skill_confirmation', {'visible_skill_names': ['Second']}), row(3000, 'skill_receipt'),
            finish(3250, 712), finish(3500, 712)]
    states = [dict(first_seen_ms=0, last_seen_ms=50, values={'skill_points': 1000}, evidence='state.png')]
    return rows, states


class EmptyCartBalanceTests(unittest.TestCase):
    def test_purchases_between_empty_cart_balances_are_priced(self):
        rows, states = career_end()
        first, second = skill_transactions(rows, states)
        # First: the cart's balance at the confirmation (812) is the balance after.
        # Second: its last counter unread, but the one skill it named costs the difference.
        self.assertEqual([(b['spent_skill_points'], b['cost_basis']) for b in (first, second)],
                         [(200, 'empty_cart_balances_around_the_receipt'), (100, 'empty_cart_balances_around_the_receipt')])
        self.assertEqual([[(e['role'], e['skill_points']) for e in b['balance_evidence']] for b in (first, second)],
                         [[('before', 1012), ('after', 812)], [('before', 812), ('after', 712)]])

    def test_a_menu_read_after_a_receipt_that_holds_the_next_cart_prices_nothing(self):
        # The next skill already in the cart on the first frames after the
        # receipt: the counter shows 712, not the 812 left, and neither the
        # cart (812) nor the skill named (200) agrees with 1012 - 712.
        rows, states = career_end()
        for r in rows:
            if r['source_timestamp_ms'] in (2000, 2250):
                r['facts']['displayed_skill_points'] = 712
        first = skill_transactions(rows, states)[0]
        self.assertIsNone(first['spent_skill_points'])

    def test_points_moving_between_the_balances_price_nothing(self):
        rows, states = career_end()
        rows.insert(4, row(900, 'unknown', effects=[dict(kind='stat_change', field='skill_points', amount=5)]))
        rows.sort(key=lambda r: r['source_timestamp_ms'])
        self.assertIsNone(skill_transactions(rows, states)[0]['spent_skill_points'])


if __name__ == '__main__':
    unittest.main()
