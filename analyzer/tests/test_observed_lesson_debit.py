"""Actual balances can explain a committed lesson despite an unreadable projection."""
import copy
import unittest
from tracen_replay.gameplay import CURRENCIES
from tracen_replay.transactions import lesson_receipts
from tests.test_neural_transactions import row


def sequence():
    initial=dict(zip(CURRENCIES,(43,112,86,95,154)))
    final=dict(initial,visual=71)
    projected=dict(final,dance=None)
    receipt=dict(kind='named_acquisition',name='Makeup Advanced Class')
    rows=[row(t,'lesson_selection',{'performance_points':initial.copy()}) for t in (0,250)]
    rows += [row(t,'lesson_confirmation',{'name_candidates':['Makeup Advanced Class'],
             'projected_performance_points':projected.copy()}) for t in (500,750)]
    rows += [row(t,'event_outcome',effects=[receipt.copy()]) for t in (1000,1250)]
    rows += [row(t,'lesson_selection',{'performance_points':final.copy()}) for t in (1500,1750)]
    event=dict(id='outcome-1',first_seen_ms=1000,last_seen_ms=1250,evidence='1000.png',
               effects=[receipt],deltas={'guts':12})
    return rows,event


class ObservedLessonDebitTests(unittest.TestCase):
    def test_repeated_actual_balances_recover_missing_projection_without_filling_it(self):
        rows,event=sequence();original=copy.deepcopy(rows)
        got=lesson_receipts(rows,[event])[0]
        self.assertEqual(got['performance_cost'],dict.fromkeys(CURRENCIES,0)|{'visual':24})
        self.assertEqual(got['cost_basis'],'receipt_and_repeated_observed_balances')
        self.assertTrue(got['after_balance_observed'])
        self.assertFalse(got['complete_transaction_verified'])
        self.assertEqual(got['awarded_stats'],{'guts':12})
        self.assertEqual(got['balance_evidence'][0]['evidence'],['0.png','250.png'])
        self.assertEqual(got['balance_evidence'][1]['evidence'],['1500.png','1750.png'])
        self.assertEqual(rows,original)

    def test_a_stat_the_dialog_projected_and_the_receipt_never_showed_is_awarded_as_worked_out(self):
        rows,event=sequence()
        # The dialog projected Speed +6 (halved above the cap) on both of its
        # frames; the receipt showed the Guts line only.
        for r in rows[2:4]:r['facts']['projected_stat_gains']={'speed':6,'guts':12,'stamina':0}
        got=lesson_receipts(rows,[event])[0]
        self.assertEqual(got['projected_stat_gains'],{'speed':6,'guts':12})
        self.assertEqual(got['stat_gains_awarded_by_projection'],['speed'])
        self.assertEqual(got['awarded_stats'],{'guts':12,'speed':6})
        added=[e for e in event['effects'] if e.get('kind')=='stat_change']
        self.assertEqual([(e['field'],e['amount'],e['amount_basis'],e['projection_evidence']) for e in added],
                         [('speed',6,'lesson_confirmation_projection',['500.png','750.png'])])
        # Field evidence is the receipt's own frame, so the award is timed at the receipt.
        self.assertEqual(event['field_evidence']['stat_change|speed|'],['1000.png'])
        # One frame's projection, or a projection of nothing, awards nothing.
        rows,event=sequence()
        rows[2]['facts']['projected_stat_gains']={'speed':6}
        rows[3]['facts']['projected_stat_gains']={'speed':0}
        got=lesson_receipts(rows,[event])[0]
        self.assertEqual((got['projected_stat_gains'],got['stat_gains_awarded_by_projection'],event['deltas']),({},[],{'guts':12}))

    def test_the_debit_window_ends_where_the_new_balance_first_shows(self):
        rows,event=sequence()
        got=lesson_receipts(rows,[event])[0]
        # Request at 500; the debited balance first shows at 1500, its repeat
        # at 1750 only confirms it, so a comparison ending at 1500 contains the debit.
        self.assertEqual(got['debit_window_ms'],[500,1500])
        self.assertEqual(got['balance_evidence'][1]['timestamps_ms'],[1500,1750])

    def test_missing_spend_projection_can_be_recovered_from_actual_debit(self):
        rows,event=sequence()
        for r in rows[2:4]:r['facts']['projected_performance_points']['visual']=None
        self.assertEqual(lesson_receipts(rows,[event])[0]['performance_cost']['visual'],24)

    def test_confirmation_alone_cannot_commit_a_purchase(self):
        rows,_=sequence()
        self.assertEqual(lesson_receipts(rows,[]),[])

    def test_uncertain_or_contradictory_sequences_keep_unknown_cost(self):
        for case in ('one_before','one_after','conflicting_before','conflicting_after',
                     'missing_actual','projection_conflict','currency_reward','training_reward','different_receipt',
                     'returned_menu','different_confirmation','sampling_gap','negative_debit','zero_debit'):
            with self.subTest(case=case):
                rows,event=sequence()
                if case=='one_before':rows.pop(0)
                elif case=='one_after':rows.pop()
                elif case=='conflicting_before':rows[0]['facts']['performance_points']['visual']+=1
                elif case=='conflicting_after':rows[-1]['facts']['performance_points']['visual']+=1
                elif case=='missing_actual':rows[0]['facts']['performance_points']['dance']=None
                elif case=='projection_conflict':rows[2]['facts']['projected_performance_points']['visual']=72
                elif case=='currency_reward':rows[4]['effects'].append(dict(kind='performance_change',field='dance',amount=10))
                elif case=='training_reward':rows[4]['facts']['awarded_performance_gains']={'dance':10}
                elif case=='different_receipt':rows[4]['effects'].append(dict(kind='named_acquisition',name='Other Lesson'))
                elif case=='returned_menu':
                    rows[2]['screen']='lesson_selection';rows[3]['screen']='lesson_selection'
                    rows.insert(2,row(375,'lesson_confirmation',{'name_candidates':['Makeup Advanced Class'],
                                         'projected_performance_points':dict.fromkeys(CURRENCIES,None)}))
                elif case=='different_confirmation':
                    rows.insert(4,row(875,'lesson_confirmation',{'name_candidates':['Different Lesson']}))
                elif case=='sampling_gap':
                    for r in rows[4:]:r['source_timestamp_ms']+=750
                    event['first_seen_ms']+=750;event['last_seen_ms']+=750
                elif case in ('negative_debit','zero_debit'):
                    for r in rows[-2:]:r['facts']['performance_points']['visual']=100 if case=='negative_debit' else 95
                    for r in rows[2:4]:r['facts']['projected_performance_points']['visual']=None
                got=lesson_receipts(rows,[event])
                self.assertTrue(not got or got[0]['performance_cost'] is None)

    def test_one_menu_transition_frame_before_receipt_is_allowed(self):
        rows,event=sequence()
        rows.insert(4,row(875,'lesson_selection',{'performance_points':{'visual':95}}))
        self.assertEqual(lesson_receipts(rows,[event])[0]['performance_cost']['visual'],24)

    def test_multiple_acquisitions_cannot_share_one_inferred_cost(self):
        rows,event=sequence()
        event['effects'].append(dict(kind='named_acquisition',name='Another Lesson'))
        self.assertIsNone(lesson_receipts(rows,[event])[0]['performance_cost'])

    def test_exact_receipt_owner_survives_bounded_ocr_name_variant(self):
        rows,event=sequence()
        event['effects'].append(dict(kind='named_acquisition',name='Makeup Advnaced Class'))
        purchases=lesson_receipts(rows,[event])
        self.assertEqual(len(purchases),1)
        self.assertEqual(purchases[0]['performance_cost']['visual'],24)
        self.assertEqual([effect['name'] for effect in event['effects']],['Makeup Advanced Class'])
        self.assertTrue(purchases[0]['receipt_name_variants_resolved'])

    def test_unrelated_receipt_owner_is_not_collapsed_as_a_typo(self):
        rows,event=sequence()
        event['effects'].append(dict(kind='named_acquisition',name='Dance Training Advanced Class'))
        purchases=lesson_receipts(rows,[event])
        self.assertTrue(not purchases or purchases[0]['performance_cost'] is None)

    def test_malformed_projection_mapping_stays_unresolved(self):
        for value in (None,[],42,'unreadable'):
            with self.subTest(value=value):
                rows,event=sequence()
                rows[2]['facts']['projected_performance_points']=value
                self.assertIsNone(lesson_receipts(rows,[event])[0]['performance_cost'])

    def test_actual_counter_on_unexpected_screen_is_not_ignored(self):
        rows,event=sequence()
        rows[4]['facts']['performance_points']=dict(rows[-1]['facts']['performance_points'],visual=80)
        self.assertIsNone(lesson_receipts(rows,[event])[0]['performance_cost'])


if __name__=='__main__':unittest.main()


class HiddenMenuBalanceTests(unittest.TestCase):
    """A cursor can hide one menu balance field; the same visit still shows it."""

    def test_hidden_menu_field_is_filled_from_the_transition_frame(self):
        rows,event=sequence()
        for r in rows[:2]:r['facts']['performance_points']['dance']=None
        # projection complete, so the cost comes from initial - projected
        for r in rows[2:4]:r['facts']['projected_performance_points']['dance']=43
        rows.insert(4,row(875,'lesson_selection',{'performance_points':dict(zip(CURRENCIES,(43,112,86,95,154)))}))
        got=lesson_receipts(rows,[event])[0]
        self.assertEqual(got['performance_cost']['visual'],24)
        self.assertEqual(got['performance_cost']['dance'],0)
        self.assertEqual(got['initial_balance_fill_fields'],['dance'])

    def test_fill_needs_every_readable_field_to_agree(self):
        rows,event=sequence()
        for r in rows[:2]:r['facts']['performance_points']['dance']=None
        for r in rows[2:4]:r['facts']['projected_performance_points']['dance']=43
        rows.insert(4,row(875,'lesson_selection',{'performance_points':dict(zip(CURRENCIES,(43,112,86,90,154)))}))
        self.assertIsNone(lesson_receipts(rows,[event])[0]['performance_cost'])

    def test_stale_menu_frame_after_the_receipt_is_skipped(self):
        rows,event=sequence()
        for r in rows[2:4]:r['facts']['projected_performance_points']['visual']=None
        stale=row(1375,'lesson_selection',{'performance_points':dict(zip(CURRENCIES,(43,112,86,95,154)))})
        rows.insert(6,stale)
        got=lesson_receipts(rows,[event])[0]
        self.assertEqual(got['performance_cost']['visual'],24)
        self.assertEqual(got['cost_basis'],'receipt_and_repeated_observed_balances')
