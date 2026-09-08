import unittest
from tracen_replay.effect_evaluate import evaluate
from tracen_replay.gameplay import effects_from_lines
from tracen_replay.receipt_names import collapse_song_variants


class EffectEvaluationTests(unittest.TestCase):
    def pair(self):
        effect=dict(kind='energy_change',amount=50)
        ref=dict(source_sha256='s',scope='fixed interval',start_ms=0,end_ms=1000,sample_interval_ms=250,
            reviewed_samples=[dict(source_timestamp_ms=t) for t in range(0,1000,250)],
            groups=[dict(start_ms=250,end_ms=500,effects=[effect])])
        report=dict(source=dict(sha256='s'),gameplay_tracking=dict(auxiliary_log_used=False,
            events=[dict(id='e',first_seen_ms=250,effects=[effect])]))
        return ref,report

    def test_counts_extras_wrong_values_and_conflicts(self):
        ref,report=self.pair();self.assertTrue(evaluate(ref,report)['passed'])
        report['gameplay_tracking']['events'][0]['effects'].append(dict(kind='fan_change',amount=10))
        result=evaluate(ref,report);self.assertFalse(result['passed']);self.assertEqual(result['precision'],.5)
        report['gameplay_tracking']['events'][0]['effects']=[dict(kind='energy_change',amount=5)]
        result=evaluate(ref,report);self.assertEqual(len(result['missing']),1);self.assertEqual(len(result['extra_predictions']),1)
        report['gameplay_tracking']['events'][0]['effects']=[dict(kind='energy_change',amount=50)]
        report['gameplay_tracking']['events'][0]['conflicting_readings']=[dict(field='energy_change||')]
        self.assertFalse(evaluate(ref,report)['passed'])

    def test_duplicate_reference_cannot_reuse_prediction_and_missing_samples_fail(self):
        ref,report=self.pair();ref['groups'][0]['effects']*=2
        self.assertEqual(evaluate(ref,report)['matched'],1)
        ref,report=self.pair();ref['reviewed_samples'].pop()
        self.assertIn('incomplete_review_sample_manifest',evaluate(ref,report)['evidence_errors'])
        self.assertFalse(evaluate(ref,report)['full_recording_effect_recall_measured'])

    def test_unlock_and_announcement_are_not_supporter_counts(self):
        effects=effects_from_lines([dict(text=t,confidence=99) for t in
            ('New supporters joined!','Light Hello will now appear in training.','Oguri Cap joined your cause!')])
        self.assertEqual([e['kind'] for e in effects],['supporters_joined_announcement','training_appearance_unlocked','supporter_joined'])
        self.assertIsNone(effects[0]['amount']);self.assertIsNone(effects[1]['amount'])

    def names(self):
        return dict(effects=[dict(kind='song_learned',name=n) for n in ('Make Debut!','Make ebut!')],
            field_evidence={'song_learned||Make Debut!':['a','b','c'],'song_learned||Make ebut!':['d']})

    def test_interleaved_single_missing_character_preserves_original_candidate(self):
        event=self.names();collapse_song_variants(event,dict(a=0,b=500,c=1000,d=750))
        self.assertEqual(len(event['effects']),1)
        self.assertEqual(event['effects'][0]['observed_name_candidates'],['Make Debut!','Make ebut!'])

    def test_separate_acquisitions_or_repeated_alternatives_remain_separate(self):
        event=self.names();collapse_song_variants(event,dict(a=0,b=500,c=1000,d=1500));self.assertEqual(len(event['effects']),2)
        event=self.names();collapse_song_variants(event,dict(a=0,b=500,c=1000,d=1250));self.assertEqual(len(event['effects']),1)
        event=self.names();event['field_evidence']['song_learned||Make ebut!'].append('e')
        collapse_song_variants(event,dict(a=0,b=500,c=1000,d=750,e=800));self.assertEqual(len(event['effects']),2)
        event=self.names();event['effects'][1]['name']='Make Rebut!'
        collapse_song_variants(event,dict(a=0,b=500,c=1000,d=750));self.assertEqual(len(event['effects']),2)
