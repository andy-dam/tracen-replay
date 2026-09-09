import copy
import unittest
from tracen_replay.effect_evaluate import evaluate


class TrainingEffectEvaluationTests(unittest.TestCase):
    def pair(self):
        effects=[dict(kind='stat_change',field='speed',amount=12),
                 dict(kind='performance_change',field='dance',amount=8)]
        reference=dict(source_sha256='s',scope='training grid and receipts',
            start_ms=0,end_ms=1000,sample_interval_ms=250,
            reviewed_samples=[dict(source_timestamp_ms=t) for t in range(0,1000,250)],
            groups=[dict(start_ms=250,end_ms=499,effects=effects)])
        event=dict(id='train',kind='training',first_seen_ms=300,last_seen_ms=400,
                   deltas={'speed':12},performance_deltas={'dance':8},
                   conflicting_readings={},effects=[])
        report=dict(source={'sha256':'s'},gameplay_tracking=dict(
            auxiliary_log_used=False,events=[event]))
        return reference,report,event

    def test_typed_training_fields_are_explicit_opt_in_and_do_not_mutate_report(self):
        ref,report,event=self.pair()
        before=copy.deepcopy(report)
        self.assertEqual(evaluate(ref,report)['predicted'],0)
        ref['include_training_results']=True
        result=evaluate(ref,report)
        self.assertTrue(result['passed'])
        self.assertEqual(result['typed_training_predictions'],2)
        self.assertEqual(report,before)

    def test_preview_outside_window_and_non_integer_values_cannot_match(self):
        for change in ({'kind':'training_preview'}, {'first_seen_ms':1100},
                       {'deltas':{'speed':True}}, {'deltas':{'speed':12.0}}):
            ref,report,event=self.pair()
            ref['include_training_results']=True
            event.update(change)
            self.assertFalse(evaluate(ref,report)['passed'])

    def test_training_conflicts_and_wrong_values_remain_failures(self):
        ref,report,event=self.pair()
        ref['include_training_results']=True
        event['conflicting_readings']={'speed':[12,21]}
        result=evaluate(ref,report)
        self.assertFalse(result['passed'])
        self.assertEqual(result['matched'],1)
        event['conflicting_readings']={}
        event['deltas']['speed']=21
        self.assertFalse(evaluate(ref,report)['passed'])
        event['deltas']['speed']=12
        event['performance_reading_conflicts']={'dance':[8,18]}
        self.assertFalse(evaluate(ref,report)['passed'])

    def test_existing_effect_is_not_counted_twice(self):
        ref,report,event=self.pair()
        ref['include_training_results']=True
        event['effects']=[copy.deepcopy(ref['groups'][0]['effects'][0])]
        result=evaluate(ref,report)
        self.assertTrue(result['passed'])
        self.assertEqual(result['predicted'],2)
        self.assertEqual(result['typed_training_predictions'],1)

    def test_incompatible_timing_and_non_boolean_scope_are_rejected(self):
        ref,report,_=self.pair()
        for value in ('true',1,None):
            ref['include_training_results']=value
            with self.assertRaises(ValueError):evaluate(ref,report)
        ref['include_training_results']=True
        ref['timing_basis']='first_exact_effect_observation'
        with self.assertRaisesRegex(ValueError,'derived deltas'):
            evaluate(ref,report)
