"""The names a skill confirmation showed are the whole purchase when their prices sum to the cart's net cost."""
import unittest

from tracen_replay.transactions import _purchased_list


def batch(names, prices, net, **overrides):
    return dict(dict(
        spent_skill_points=None, committed_cart_bundles=[], unassigned_cart_changes=[dict(projected_charge_delta=-132)],
        visible_confirmation_names=list(names), cart_net_cost=net,
        selected_item_candidates=[dict(name=n, cost=c, basis='visible_confirmation_and_price') for n, c in prices.items()]
                                 + [dict(name='Standard Distance', cost=90, basis='cart_counter_and_unique_visible_price_candidate')]),
        **overrides)


class PurchasedListByCostTests(unittest.TestCase):
    def test_visible_names_whose_prices_sum_to_the_cart_net_are_the_purchase(self):
        t = batch(['Murmur', 'Pace Chaser Savvy', 'Escapades'], {'Murmur': 144, 'Pace Chaser Savvy': 117, 'Escapades': 160}, 421)
        _purchased_list(t)
        self.assertEqual((t['purchased_list_complete'], t['purchased_list_basis'], t['purchased_skill_names']),
                         (True, 'visible_names_cost_the_cart_net', ['Escapades', 'Murmur', 'Pace Chaser Savvy']))

    def test_a_price_short_of_the_cart_or_an_unpriced_name_leaves_the_list_open(self):
        t = batch(['Murmur', 'Pace Chaser Savvy', 'Escapades'], {'Murmur': 144, 'Pace Chaser Savvy': 117, 'Escapades': 160}, 500)
        _purchased_list(t)
        self.assertEqual((t['purchased_list_complete'], t['purchased_list_basis'], t['purchased_skill_names']),
                         (False, 'confirmation_list_may_scroll', None))
        t = batch(['Murmur', 'Pace Chaser Savvy', 'Escapades'], {'Murmur': 144, 'Pace Chaser Savvy': 117}, 261)
        _purchased_list(t)
        self.assertFalse(t['purchased_list_complete'])


if __name__ == '__main__':
    unittest.main()
