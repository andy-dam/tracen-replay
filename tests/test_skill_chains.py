import unittest
from copy import deepcopy
from tests.test_neural_transactions import row
from tracen_replay.transactions import skill_transactions
from tracen_replay.skill_chains import final_cart,reconcile_skill_chains
from tracen_replay.gameplay import screen_summary


class SkillChainTests(unittest.TestCase):
    def sequence(self):
        def card(name,cost,status):
            return dict(name=name,displayed_cost=cost,menu_status=status,variant='single_circle')
        initial=[card('First',200,'available'),card('Second',100,'available')]
        first=[card('First',None,'obtained_or_selected'),card('Second',100,'available')]
        second=[card('First',None,'obtained_or_selected'),card('Second',None,'obtained_or_selected')]
        rows=[row(t,'skill_selection',{'displayed_skill_points':value,'skill_cards':cards})
              for t,value,cards in ((250,1000,initial),(500,1000,initial),(750,800,first),(1000,800,first),
                                    (2000,700,second),(2250,700,second))]
        rows += [row(1250,'skill_confirmation',{'visible_skill_names':['First']}),row(1500,'skill_receipt'),row(1750,'skill_receipt'),
                 row(2500,'skill_confirmation',{'visible_skill_names':['Second']}),row(2750,'skill_receipt'),row(3000,'skill_receipt')]
        states=[dict(first_seen_ms=0,last_seen_ms=0,values={'skill_points':1000},evidence='before.png'),
                dict(first_seen_ms=5000,last_seen_ms=5250,values={'skill_points':700},evidence='after.png')]
        return sorted(rows,key=lambda r:r['source_timestamp_ms']),states

    def test_consecutive_receipts_share_balances_but_not_committed_carts(self):
        rows,states=self.sequence();batches=skill_transactions(rows,states)
        self.assertEqual([b['spent_skill_points'] for b in batches],[200,100])
        self.assertEqual([b['cart_net_cost'] for b in batches],[200,100])
        self.assertEqual([[x['target_name'] for x in b['committed_cart_bundles']] for b in batches],[['First'],['Second']])
        self.assertEqual([s['name'] for s in batches[1]['selected_item_candidates']],['Second'])
        self.assertTrue(all(b['bundle_charge_assignment_complete'] for b in batches))
        self.assertTrue(all(b['cost_basis']=='receipt_cart_chain_between_observed_balances' for b in batches))
        self.assertFalse(batches[0]['joint_charge_verification']['intermediate_balances_independently_observed'])
        self.assertEqual(batches[1]['committed_cart_bundles'][0]['selected_variant'],'single_circle')

    def test_missing_final_counter_or_wrong_settled_balance_abstains(self):
        rows,states=self.sequence()
        missing=[r for r in rows if r['source_timestamp_ms']!=2250]
        self.assertTrue(all(b['spent_skill_points'] is None for b in skill_transactions(missing,states)))
        states[-1]['values']['skill_points']=699
        self.assertTrue(all(b['spent_skill_points'] is None for b in skill_transactions(rows,states)))

    def test_extra_sp_event_or_receipt_cannot_be_absorbed_by_chain(self):
        rows,states=self.sequence()
        for extra in (row(3500,effects=[dict(kind='stat_change',field='skill_points',amount=10)]),row(4000,'skill_receipt')):
            with self.subTest(extra=extra):
                changed=sorted(rows+[extra],key=lambda r:r['source_timestamp_ms'])
                self.assertTrue(all(b['spent_skill_points'] is None for b in skill_transactions(changed,states)))

    def test_duplicate_frame_or_counter_conflict_is_not_repeated_evidence(self):
        observations=[row(250,'skill_selection',{'displayed_skill_points':8})]*2
        self.assertIsNone(final_cart(observations,500,0))
        observations=[row(100,'skill_selection',{'displayed_skill_points':8}),
                      row(250,'skill_selection',{'displayed_skill_points':8}),
                      row(300,'skill_selection',{'skill_point_conflict':[8,9]})]
        self.assertIsNone(final_cart(observations,500,0))

    def test_chain_does_not_override_a_conflicting_verified_charge(self):
        rows,states=self.sequence();batches=skill_transactions(rows,states)
        batches=deepcopy(batches);batches[0]['spent_skill_points']=201;batches[1]['spent_skill_points']=None
        result=reconcile_skill_chains(batches,rows,screen_summary(rows))
        self.assertEqual(result[0]['spent_skill_points'],201)
        self.assertIsNone(result[1]['spent_skill_points'])
