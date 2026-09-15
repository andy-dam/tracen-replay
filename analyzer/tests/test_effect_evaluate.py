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

    def test_partial_labels_cannot_certify_a_perfect_prediction_subset(self):
        ref,report=self.pair()
        ref['reference_complete']=False
        result=evaluate(ref,report)
        self.assertEqual(result['matched'],1)
        self.assertEqual(result['recall'],1)
        self.assertFalse(result['reference_complete'])
        self.assertFalse(result['passed'])
        # An empty partial annotation is likewise not a verified negative interval.
        ref['groups']=[]
        report['gameplay_tracking']['events']=[]
        self.assertFalse(evaluate(ref,report)['passed'])
        ref['reference_complete']=True
        self.assertTrue(evaluate(ref,report)['passed'])

    def test_reference_completeness_rejects_truthy_strings_and_numbers(self):
        for value in ('false',0,1,None):
            with self.subTest(value=value):
                ref,report=self.pair()
                ref['reference_complete']=value
                with self.assertRaisesRegex(ValueError,'completeness'):
                    evaluate(ref,report)

    def test_source_onset_bracket_accepts_detection_between_reviewed_samples(self):
        ref,report=self.pair()
        report['gameplay_tracking']['events'][0]['first_seen_ms']=200
        self.assertFalse(evaluate(ref,report)['passed'])
        ref['groups'][0]['onset_window']=dict(last_absent_ms=0,first_present_ms=250)
        result=evaluate(ref,report)
        self.assertTrue(result['passed'])
        self.assertEqual(result['source_onset_windows'],1)
        report['gameplay_tracking']['events'][0]['first_seen_ms']=0
        self.assertFalse(evaluate(ref,report)['passed'])

    def test_onset_window_cannot_invent_samples_or_skip_reviewed_absence_bounds(self):
        for absent,present in ((1,250),(0,500),(250,0),(-250,250)):
            with self.subTest(absent=absent,present=present):
                ref,report=self.pair()
                ref['groups'][0]['onset_window']=dict(last_absent_ms=absent,first_present_ms=present)
                with self.assertRaises(ValueError):evaluate(ref,report)

    def test_explicit_observation_timing_owns_effect_at_right_boundary(self):
        ref,report=self.pair()
        event=report['gameplay_tracking']['events'][0]
        effect=event['effects'][0]
        event.update(last_seen_ms=1250,field_evidence={'energy_change||':['receipt']})
        report['gameplay_tracking']['readings']=[dict(evidence='receipt',source_timestamp_ms=1000,effects=[effect])]
        # The preserved default still uses the legacy event-start convention.
        self.assertTrue(evaluate(ref,report)['passed'])
        ref.update(timing_basis='first_exact_effect_observation',groups=[])
        result=evaluate(ref,report)
        self.assertTrue(result['passed'])
        self.assertEqual(result['predicted'],0)
        ref.update(start_ms=1000,end_ms=2000,
                   reviewed_samples=[dict(source_timestamp_ms=t) for t in range(1000,2000,250)],
                   groups=[dict(start_ms=1000,end_ms=1250,effects=[effect])])
        result=evaluate(ref,report)
        self.assertTrue(result['passed'])
        self.assertEqual(result['matched'],1)

    def test_missing_exact_support_fails_instead_of_silently_dropping(self):
        for case in ('missing_row','wrong_value','unlinked_row'):
            with self.subTest(case=case):
                ref,report=self.pair()
                ref['timing_basis']='first_exact_effect_observation'
                event=report['gameplay_tracking']['events'][0]
                event['field_evidence']={'energy_change||':['receipt']}
                row=dict(evidence='receipt',source_timestamp_ms=250,effects=[dict(kind='energy_change',amount=50)])
                if case=='wrong_value':row['effects'][0]['amount']=5
                if case=='unlinked_row':row['evidence']='unrelated'
                report['gameplay_tracking']['readings']=[] if case=='missing_row' else [row]
                result=evaluate(ref,report)
                self.assertFalse(result['passed'])
                self.assertEqual(len(result['timing_errors']),1)
                self.assertEqual(len(result['missing']),1)

    def test_repeated_observations_do_not_create_multiple_awards(self):
        ref,report=self.pair();ref['timing_basis']='first_exact_effect_observation'
        event=report['gameplay_tracking']['events'][0]
        event['field_evidence']={'energy_change||':['a','b']}
        report['gameplay_tracking']['readings']=[dict(evidence=p,source_timestamp_ms=t,effects=event['effects'])
                                               for p,t in [('a',250),('b',500)]]
        self.assertEqual(evaluate(ref,report)['matched'],1)
        self.assertEqual(evaluate(ref,report)['predicted'],1)
        ref['timing_basis']='unsupported'
        with self.assertRaises(ValueError):evaluate(ref,report)

    def test_duplicate_reference_cannot_reuse_prediction_and_missing_samples_fail(self):
        ref,report=self.pair();ref['groups'][0]['effects']*=2
        self.assertEqual(evaluate(ref,report)['matched'],1)
        ref,report=self.pair();ref['reviewed_samples'].pop()
        self.assertIn('incomplete_review_sample_manifest',evaluate(ref,report)['evidence_errors'])
        self.assertFalse(evaluate(ref,report)['full_recording_effect_recall_measured'])

    def test_observability_annotation_preserves_denominator_and_missing_effect(self):
        ref,report=self.pair()
        ref['observability_exceptions']=[dict(group_index=0,effect_index=0,fields=['amount'],
            reason='Cursor covers the final digit.',source_timestamps_ms=[250,500])]
        report['gameplay_tracking']['events']=[]
        result=evaluate(ref,report)
        self.assertEqual(result['expected'],1)
        self.assertEqual(result['matched'],0)
        self.assertEqual(len(result['missing']),1)
        self.assertFalse(result['passed'])
        self.assertEqual(result['reference_observability'],'known_exceptions')
        self.assertEqual(result['observability_exceptions'],ref['observability_exceptions'])
        ref.pop('observability_exceptions')
        self.assertEqual(evaluate(ref,report)['reference_observability'],'not_adjudicated')

    def test_observability_annotation_cannot_point_to_other_receipts_or_fields(self):
        for changes in (dict(group_index=True),dict(effect_index=1),dict(fields=['name']),
                        dict(fields=[]),dict(reason=''),dict(source_timestamps_ms=[750]),
                        dict(source_timestamps_ms=[251]),dict(source_timestamps_ms=[])):
            with self.subTest(changes=changes):
                ref,report=self.pair()
                ref['observability_exceptions']=[dict(dict(group_index=0,effect_index=0,
                    fields=['amount'],reason='Obscured.',source_timestamps_ms=[250]),**changes)]
                with self.assertRaises(ValueError):evaluate(ref,report)

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
