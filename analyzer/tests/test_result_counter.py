import copy
import json
import unittest
from pathlib import Path
from tracen_replay.result_counter import partial_counter
from tracen_replay.refine_results import apply_result_refinement
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.vision import parse
from tracen_replay.transactions import training_events
from tests.test_neural_transactions import raw,row


class ResultCounterTests(unittest.TestCase):
    def test_source_training_result_with_obscured_cap(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/partial-result-counter-v1.json').read_text(encoding='utf-8'))
        rows=list(fixture['prefix_readings'])
        for frame in fixture['frames']:
            original=frame['raw'];parsed=parse(apply_result_refinement(original,frame['refinement']))
            parsed.update(source_timestamp_ms=original['source_timestamp_ms'],evidence=original['evidence'])
            rows.append(parsed)
        event=training_events(rows,[fixture['before']])[0]
        self.assertEqual(event['deltas']['wit'],fixture['expected_wit_gain'])
        self.assertEqual(len(event['partial_result_counter_evidence']['wit']),3)
        self.assertNotIn('wit',training_events(rows[:-1],[fixture['before']])[0]['deltas'])

    def test_explicit_slash_retains_value_without_inventing_cap(self):
        for text in ('576/','576/1','576/13','576/130'):
            got=partial_counter(dict(text=text,confidence=99,box=[518,952,644,994]))
            self.assertEqual(got['value'],576);self.assertTrue(got['cap_unknown'])
        for text in ('576','+32','576/1300','576/0','576/13.0','576/13x','-5/'):
            self.assertIsNone(partial_counter(dict(text=text,confidence=99)))
        self.assertIsNone(partial_counter(dict(text='576/',confidence=96)))

    def test_refined_numerator_is_separate_from_complete_result_and_cap(self):
        sample=raw();sample.update(header='Training',result_grid=True)
        sample['regions']['result.wit']=dict(text='576/',confidence=99)
        original=copy.deepcopy(sample)
        extra=dict(raw_sha256=fingerprint(sample),regions={'result.wit':dict(text='576/13',confidence=99)})
        refined=apply_result_refinement(sample,extra);facts=parse(refined)['facts']
        self.assertEqual(sample,original)
        self.assertEqual(facts['result_numerator_candidates']['wit'],[576])
        self.assertNotIn('wit',facts['result_values']);self.assertNotIn('wit',facts['stat_caps'])
        extra['regions']['result.wit']['text']='578/13'
        self.assertEqual(parse(apply_result_refinement(sample,extra))['facts']['result_numerator_candidates']['wit'],[576,578])

    def training(self):
        states=[dict(first_seen_ms=0,last_seen_ms=500,values={'wit':544},evidence='before.png')]
        rows=[row(1000,'training_result',{'training_gain_candidates':{'wit':[32,82]}},training_option='wit')]
        for t in (1050,1075):
            partial=partial_counter(dict(text='576/13',confidence=99))
            rows.append(row(t,'training_result',{'result_numerator_candidates':{'wit':[576]},'partial_result_counter_readings':{'wit':[partial]}},training_option='wit'))
        rows.append(row(1100,'training_result',{'result_values':{'wit':576},'result_value_candidates':{'wit':576}},training_option='wit'))
        return rows,states

    def test_partial_counters_need_later_complete_total_to_support_gain(self):
        rows,states=self.training();event=training_events(rows,states)[0]
        self.assertEqual(event['deltas']['wit'],32)
        self.assertEqual(len(event['partial_result_counter_evidence']['wit']),2)
        self.assertNotIn('wit',training_events(rows[:-1],states)[0]['deltas'])
        rows[-1]['facts']['result_values']['wit']=577;rows[-1]['facts']['result_value_candidates']['wit']=577
        self.assertNotIn('wit',training_events(rows,states)[0]['deltas'])

    def test_conflicting_partial_values_do_not_supply_repetition(self):
        rows,states=self.training()
        for r in rows[1:3]:r['facts']['result_numerator_candidates']['wit']=[576,578]
        self.assertNotIn('wit',training_events(rows,states)[0]['deltas'])

    def test_complete_total_before_partial_observations_does_not_validate_them(self):
        rows,states=self.training();rows[-1]['source_timestamp_ms']=1025
        self.assertNotIn('wit',training_events(rows,states)[0]['deltas'])
