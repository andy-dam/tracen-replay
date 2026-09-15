import copy
import unittest

from tracen_replay.receipt_names import collapse_visual_hint_variants


class HintVariantTests(unittest.TestCase):
    def sample(self):
        raw='Gained 2 hint level(s) for Example O.'
        strong=dict(kind='skill_hint_change',name='Example ○',amount=2,original_text=raw,
                    visual_symbol_observation=dict(method='strict_terminal_ring_geometry'))
        weak=dict(kind='skill_hint_change',name='Example O',amount=2,raw_text=raw)
        return dict(effects=[strong,weak],field_evidence={
            'skill_hint_change||Example ○':['a','b'],
            'skill_hint_change||Example O':['c','d']},conflicting_readings=[]),dict(a=0,b=250,c=500,d=750)

    def test_same_receipt_counts_once_without_promoting_alternate_proofs(self):
        event,times=self.sample()
        collapse_visual_hint_variants(event,times)
        self.assertEqual(len(event['effects']),1)
        effect=event['effects'][0]
        self.assertEqual(effect['observed_name_candidates'],['Example ○','Example O'])
        self.assertEqual(event['field_evidence']['skill_hint_change||Example ○'],['a','b'])
        self.assertEqual(effect['alternate_name_evidence'][0]['evidence'],['c','d'])

    def test_gaps_conflicts_single_proof_or_different_receipts_do_not_merge(self):
        for case in ('gap','conflict','single','amount','sentence','no_pixels'):
            with self.subTest(case=case):
                event,times=self.sample()
                if case=='gap':times.update(c=1000,d=1250)
                elif case=='conflict':event['conflicting_readings']=[dict(field='skill_hint_change||Example O')]
                elif case=='single':event['field_evidence']['skill_hint_change||Example ○']=['a']
                elif case=='amount':event['effects'][1]['amount']=3
                elif case=='sentence':event['effects'][1]['raw_text']='A different receipt.'
                else:event['effects'][0].pop('visual_symbol_observation')
                before=copy.deepcopy(event)
                collapse_visual_hint_variants(event,times)
                self.assertEqual(event,before)
