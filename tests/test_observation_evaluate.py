import copy
import unittest

from tracen_replay.observation_evaluate import evaluate


def observation(identity, amount=10, *, start=100, end=200, cause='turn-a', key=None,
                category='effect', phase='applied', **payload):
    return dict(id=identity, category=category, phase=phase, start_ms=start, end_ms=end,
                cause_id=cause, occurrence_key=key, evidence=[],
                payload=dict(kind='stat_change', field='speed', amount=amount, **payload))


def score(expected, predicted, complete=True, **reference_fields):
    return evaluate(dict(source_sha256='recording-a', scope_ms=[0,1000],
                         reference_complete=complete, observations=expected, **reference_fields),
                    dict(source_sha256='recording-a', auxiliary_log_used=False, observations=predicted))


class ObservationEvaluationTests(unittest.TestCase):
    def test_exact_source_frame_precedes_overlapping_view_without_using_amount(self):
        source = observation('s')
        source['evidence'] = ['source/frame.png']
        exact = observation('exact', 11)
        exact['evidence'] = ['source/frame.png']
        nearby = observation('nearby', 10)
        nearby['evidence'] = ['source/later.png']
        result = score([source], [nearby, exact])
        self.assertEqual(result['results'][0]['prediction_id'], 'exact')
        self.assertEqual(result['results'][0]['status'], 'incorrect')
        self.assertEqual(result['results'][0]['matching_basis'], 'source_identity')
        self.assertEqual(result['unmatched_predictions'],
                         [{'prediction_id': 'nearby', 'status': 'extra'}])

    def test_duplicate_exact_source_claims_remain_ambiguous(self):
        rows = [observation(name) for name in ('s', 'a', 'b')]
        for row in rows:
            row['evidence'] = ['same/frame.png']
        result = score(rows[:1], rows[1:])
        self.assertEqual(result['results'][0]['status'], 'ambiguous')
        self.assertIsNone(result['results'][0]['prediction_id'])

    def test_exact_frame_does_not_override_explicit_wrong_owner(self):
        source = observation('s')
        foreign = observation('foreign', cause='turn-b')
        for row in (source, foreign):
            row['evidence'] = ['same/frame.png']
        result = score([source], [foreign])
        self.assertEqual(result['results'][0]['status'], 'misattributed')

    def test_unobservable_source_does_not_turn_corresponding_prediction_into_false_positive(self):
        source=observation('s');source['status']='unobservable'
        result=score([source],[observation('p')])
        self.assertEqual(result['unmatched_predictions'][0]['status'],'ungraded')
        self.assertFalse(result['passed'])

    def test_nested_values_keep_boolean_and_numeric_semantics(self):
        source=observation('s');prediction=observation('p')
        source['payload']['items']=[{'amount':1}]
        prediction['payload']['items']=[{'amount':True}]
        self.assertEqual(score([source],[prediction])['results'][0]['status'],'incorrect')
        prediction['payload']['items']=[{'amount':float('nan')}]
        with self.assertRaises(ValueError):score([source],[prediction])

    def test_missing_and_wrong_amount_are_distinct(self):
        expected=[observation('source')]
        self.assertEqual(score(expected,[])['status_counts'],{'missed':1})
        wrong=score(expected,[observation('prediction',11)])
        self.assertEqual(wrong['status_counts'],{'incorrect':1})
        self.assertEqual(wrong['results'][0]['prediction_id'],'prediction')
        self.assertFalse(wrong['passed'])

    def test_offsetting_errors_do_not_pass_balanced_totals(self):
        expected=[observation('s1',10,key='award-1'),observation('s2',20,key='award-2')]
        predicted=[observation('p1',15,key='award-1'),observation('p2',15,key='award-2')]
        self.assertEqual(sum(x['payload']['amount'] for x in expected),sum(x['payload']['amount'] for x in predicted))
        result=score(expected,predicted)
        self.assertEqual(result['status_counts'],{'incorrect':2})
        self.assertFalse(result['passed'])

    def test_wrong_cause_does_not_pass_correct_amount(self):
        result=score([observation('s',key='award')],[observation('p',key='award',cause='turn-b')])
        self.assertEqual(result['status_counts'],{'misattributed':1})
        fields={x['field']:x['status'] for x in result['results'][0]['fields']}
        self.assertEqual(fields['/amount'],'correct')
        self.assertEqual(fields['/cause_id'],'incorrect')
        self.assertFalse(result['passed'])

    def test_absent_owner_is_partial_not_verified_attribution(self):
        result=score([observation('s')],[observation('p',cause=None)])
        self.assertEqual(result['status_counts'],{'partial':1})
        self.assertFalse(result['passed'])

    def test_duplicate_atomic_claims_are_detected(self):
        result=score([observation('s',key='award')],
                     [observation('p1',key='award'),observation('p2',key='award')])
        self.assertEqual(result['duplicate_occurrence_claims'],[['p1','p2']])
        self.assertEqual(result['status_counts'],{'ambiguous':1})
        self.assertFalse(result['passed'])

    def test_one_prediction_cannot_fill_two_source_occurrences(self):
        result=score([observation('s1'),observation('s2')],[observation('p')])
        self.assertEqual(result['status_counts'],{'ambiguous':2})
        self.assertFalse(result['passed'])

    def test_global_matching_is_order_independent_and_ignores_amount(self):
        # s1 can see either prediction, while s2 can only see p1.
        sources=[observation('s1',20,start=100,end=400),observation('s2',10,start=100,end=150)]
        predictions=[observation('p1',10,start=110,end=120),observation('p2',20,start=300,end=310)]
        result=score(sources,predictions)
        self.assertTrue(result['passed'])
        self.assertEqual({x['source_id']:x['prediction_id'] for x in result['results']},{'s1':'p2','s2':'p1'})
        reversed_result=score(list(reversed(sources)),list(reversed(predictions)))
        self.assertEqual(result['status_counts'],reversed_result['status_counts'])
        predictions[0]['payload']['amount']=20
        predictions[1]['payload']['amount']=10
        self.assertEqual(score(sources,predictions)['status_counts'],{'incorrect':2})

    def test_matching_amount_does_not_resolve_structural_ambiguity(self):
        result=score([observation('s',10)],[observation('p1',10),observation('p2',99)])
        self.assertEqual(result['status_counts'],{'ambiguous':1})
        self.assertEqual(result['results'][0]['candidate_ids'],['p1','p2'])

    def test_unrelated_correct_recipient_is_not_consumed_for_missing_recipient(self):
        agnes=observation('agnes',7,name='Agnes Tachyon')
        director=observation('director',2,name='Director Akikawa')
        actual=observation('p',2,name='Director Akikawa')
        for row in (agnes,director,actual):row['payload']['kind']='friendship_change';row['payload'].pop('field')
        result=score([agnes,director],[actual])
        self.assertEqual({x['source_id']:x['status'] for x in result['results']},{'agnes':'missed','director':'correct'})

    def test_wrong_identity_requires_atomic_occurrence_support(self):
        source=observation('s',key='line-1',name='Director Akikawa')
        actual=observation('p',key='line-1',name='Director Akixawa')
        self.assertEqual(score([source],[actual])['status_counts'],{'incorrect':1})
        source.pop('occurrence_key');actual.pop('occurrence_key')
        self.assertEqual(score([source],[actual])['status_counts'],{'missed':1})

    def test_previews_actions_receipts_and_purchases_remain_distinct(self):
        for category,phase in [('effect','preview'),('action','committed'),('purchase','committed')]:
            with self.subTest(category=category,phase=phase):
                result=score([observation('s')],[observation('p',category=category,phase=phase)])
                self.assertEqual(result['status_counts'],{'missed':1})
                self.assertFalse(result['passed'])

    def test_canceled_purchase_does_not_satisfy_committed_purchase(self):
        result=score([observation('s',category='purchase',phase='committed')],
                     [observation('p',category='purchase',phase='preview')])
        self.assertEqual(result['status_counts'],{'missed':1})

    def test_field_evidence_can_join_cross_boundary_receipt(self):
        source=observation('s',start=500,end=500)
        actual=observation('p',start=250,end=450)
        self.assertFalse(score([source],[actual])['passed'])
        source['evidence']=actual['evidence']=['recording-a/gameplay/frame-500/line-2']
        self.assertTrue(score([source],[actual])['passed'])
        actual['cause_id']='turn-b'
        self.assertEqual(score([source],[actual])['status_counts'],{'misattributed':1})

    def test_evidence_paths_are_not_compared_by_basename(self):
        source=observation('s',start=500,end=500);actual=observation('p',start=100,end=200)
        source['evidence']=['part-a/frame.png'];actual['evidence']=['part-b/frame.png']
        self.assertEqual(score([source],[actual])['status_counts'],{'missed':1})

    def test_signed_decrease_matches_without_changing_inputs(self):
        source=observation('s',13);source['payload'].update(kind='energy_change',direction='down')
        actual=observation('p',-13);actual['payload']['kind']='energy_change'
        originals=copy.deepcopy([source,actual])
        self.assertTrue(score([source],[actual])['passed'])
        self.assertEqual([source,actual],originals)

    def test_zero_unknown_bool_and_unobservable_are_distinct(self):
        self.assertTrue(score([observation('s',0)],[observation('p',0)])['passed'])
        self.assertEqual(score([observation('s',0)],[observation('p',None)])['status_counts'],{'partial':1})
        self.assertEqual(score([observation('s',0)],[observation('p',False)])['status_counts'],{'incorrect':1})
        source=observation('s');source['status']='unobservable'
        self.assertEqual(score([source],[],complete=False)['status_counts'],{'unobservable':1})

    def test_incomplete_reference_does_not_claim_precision_or_pass(self):
        result=score([observation('s')],[observation('p'),observation('outside',start=700,end=750)],complete=False)
        self.assertEqual(result['status_counts'],{'correct':1})
        self.assertEqual(result['unmatched_predictions'],[{'prediction_id':'outside','status':'ungraded'}])
        self.assertFalse(result['passed'])

    def test_negative_scope_requires_explicit_review(self):
        self.assertFalse(score([],[])['passed'])
        self.assertTrue(score([],[],negative_scope_reviewed=True)['passed'])

    def test_duplicate_ids_and_invalid_numeric_observations_fail(self):
        with self.assertRaises(ValueError):score([observation('s')],[observation('p'),observation('p')])
        with self.assertRaises(ValueError):score([observation('s')],[observation('p',float('nan'))])
        with self.assertRaises(ValueError):score([observation('s',-10,direction='up')],[])

    def test_scoped_uncertainty_only_ambiguous_for_requested_payload_fields(self):
        source = observation('s', category='purchase', phase='preview', cause='turn-a')
        source['payload'] = {
            'kind': 'skill',
            'selected_draft_names': ['Pace Chaser Savvy'],
            'skill_points_after': 25,
        }
        actual = copy.deepcopy(source)
        actual['id'] = 'p'
        actual['uncertain'] = True
        actual['uncertainty_fields'] = ['/visible_card_prices/Other Skill']
        result = score([source], [actual])
        self.assertEqual(result['results'][0]['status'], 'correct')
        self.assertTrue(result['passed'])

        actual['uncertainty_fields'] = ['/selected_draft_names']
        result = score([source], [actual])
        self.assertEqual(result['results'][0]['status'], 'ambiguous')
        self.assertFalse(result['passed'])

    def test_scoped_uncertainty_accepts_parent_json_pointer(self):
        source = observation('s', category='purchase', phase='preview', cause='turn-a')
        source['payload'] = {'kind': 'skill', 'visible_card_prices': {'Skill A': 20}}
        actual = copy.deepcopy(source)
        actual['id'] = 'p'
        actual['uncertain'] = True
        actual['uncertainty_fields'] = ['/visible_card_prices']
        result = score([source], [actual])
        self.assertEqual(result['results'][0]['status'], 'ambiguous')

    def test_uncertainty_field_scope_is_nonempty_valid_unique_json_pointers(self):
        source = observation('s')
        for fields in ([], ['selected_draft_names'], ['/bad~2escape'],
                       ['/visible_card_prices', '/visible_card_prices'], ['/'],
                       [[]], [{}]):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                actual = observation('p')
                actual['uncertain'] = True
                actual['uncertainty_fields'] = fields
                score([source], [actual])

    def test_legacy_uncertainty_without_scope_remains_row_ambiguous(self):
        source = observation('s')
        actual = observation('p')
        actual['uncertain'] = True
        result = score([source], [actual])
        self.assertEqual(result['results'][0]['status'], 'ambiguous')

    def test_recording_identity_and_auxiliary_log_are_checked(self):
        ref=dict(source_sha256='a',scope_ms=[0,1000],observations=[])
        with self.assertRaises(ValueError):evaluate(ref,dict(source_sha256='b',auxiliary_log_used=False,observations=[]))
        with self.assertRaises(ValueError):evaluate(ref,dict(source_sha256='a',auxiliary_log_used=True,observations=[]))


if __name__=='__main__':unittest.main()
