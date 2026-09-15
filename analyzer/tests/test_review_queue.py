import unittest
from tracen_replay.review_queue import build,reviewed_intervals_for_sweep


def report():
    return dict(source=dict(sha256='one',duration_ms=300000),gameplay_tracking=dict(auxiliary_log_used=False))


class ReviewQueueTests(unittest.TestCase):
    def test_coarse_review_does_not_retire_dense_sweep_work(self):
        coverage=dict(references=[dict(start_ms=0,end_ms=60000,sample_interval_ms=1000),
                                  dict(start_ms=10000,end_ms=20000,sample_interval_ms=250)])
        intervals=reviewed_intervals_for_sweep(coverage)
        self.assertEqual(intervals,[(10000,20000)])
        queue=build(report(),intervals)
        self.assertEqual(queue['source_sweep'][0]['start_ms'],0)
        self.assertEqual(queue['summary']['unreviewed_source_ms'],290000)
        with self.assertRaises(ValueError):reviewed_intervals_for_sweep(coverage,0)

    def test_balanced_ledgers_do_not_hide_obscured_names(self):
        r=report();d=r['gameplay_tracking']
        d['intervals']=[dict(start_ms=0,end_ms=1000,status='balanced',unexplained_change={'speed':0})]
        d['readings']=[dict(source_timestamp_ms=t,evidence='proof.png',facts=dict(occluded_receipt_lines=[dict(text='Friendship with X went up by7',recipient_name_occluded=True)])) for t in [5000,5250]]
        q=build(r);self.assertEqual(len(q['findings']),2);self.assertEqual(len(q['triage_windows']),1)
        self.assertEqual(q['summary']['triage_footage_ms'],3250);self.assertFalse(q['go_ready'])

    def test_sweep_is_independent_of_predictions_and_merges_review_overlap(self):
        r=report();before=build(r,[(0,10000),(5000,20000)])
        r['gameplay_tracking']['unparsed_receipt_candidates']=[dict(first_seen_ms=22000,last_seen_ms=22500,evidence=[],raw_text='fragment')]
        after=build(r,[(0,10000),(5000,20000)])
        self.assertEqual(before['source_sweep'],after['source_sweep'])
        self.assertEqual(after['source_sweep'][0],dict(start_ms=20000,end_ms=140000,reviewed=False))
        self.assertEqual(after['summary']['unreviewed_source_ms'],280000)

    def test_contradictory_balanced_status_missing_actions_and_costs_are_queued(self):
        r=report();d=r['gameplay_tracking'];d['intervals']=[dict(start_ms=10,end_ms=20,status='balanced',unexplained_change={'speed':3})]
        d['lesson_purchases']=[dict(source_timestamp_ms=25,performance_cost=None)]
        d['training_previews']=[dict(source_timestamp_ms=30,training_option='speed')]
        r['verification']=dict(calendar_action_coverage=dict(windows=[dict(start_ms=5,end_ms=50,status='missing_action')]))
        q=build(r);self.assertEqual(set(q['summary']['findings_by_reason']),{'stats_discrepancy','missing_lesson_cost','calendar_action_coverage'})
        self.assertFalse(q['fully_verified'])

    def test_foreign_scope_and_invalid_dimensions_fail(self):
        r=report();r['gameplay_tracking']['auxiliary_log_used']=True
        with self.assertRaises(ValueError):build(r)
        for intervals in [[(-1,5)],[(0,400000)],[(5,5)]]:
            with self.assertRaises(ValueError):build(report(),intervals)
        with self.assertRaises(ValueError):build(report(),sweep_ms=0)

    def test_inventory_gaps_are_localized_without_claiming_missing_values(self):
        r=report();d=r['gameplay_tracking']
        d['owned_skill_inventory']=dict(complete=False,summary_frames=[dict(timestamp_ms=200000,evidence='summary.png')],
            observed_owned_cards=[dict(name_text='Example',level=None,variant=None,level_conflicts=[4,5],
                observations=[dict(timestamp_ms=200000,evidence='summary.png')])])
        d['readings']=[dict(source_timestamp_ms=200000,evidence='summary.png',facts=dict(
            owned_skill_panel_conflicts=[dict(slot=[0,0],names=['A','B'])]))]
        q=build(r)
        self.assertEqual(set(q['summary']['findings_by_reason']),{
            'incomplete_owned_inventory','owned_skill_name_conflict','owned_skill_detail_conflict'})
        self.assertEqual(len(q['triage_windows']),1)
        finding=q['findings'][0]
        self.assertEqual(finding['evidence'],['summary.png'])
        self.assertTrue(finding['details']['missing_detail_is_not_confirmed_absence'])
        self.assertFalse(q['go_ready'])

    def test_optional_bonus_observation_is_unadjudicated_when_not_observed(self):
        r=report();r['gameplay_tracking']['concerts']=[dict(
            source_timestamp_ms=1000,evidence=['concert-update.png'],
            all_bonus_totals_verified=False,
            later_active_bonus_snapshot=dict(
                values={},observations={
                    'friendship_training_effectiveness':[],
                    'specialty_priority':[],
                    'support_chain_event_frequency':[],
                },unresolved_fields={
                    'friendship_training_effectiveness':'not_observed',
                    'specialty_priority':'not_observed',
                    'support_chain_event_frequency':'not_observed',
                },complete=False))]
        q=build(r)
        findings=[f for f in q['findings'] if f['reason']=='unverified_active_bonuses']
        self.assertEqual(len(findings),1)
        self.assertEqual(findings[0]['evidence'],['concert-update.png'])
        details=findings[0]['details']
        self.assertEqual(details['status'],'no_supported_current_bonus_observation')
        self.assertEqual(details['source_availability'],'unadjudicated')
        self.assertFalse(details['screen_absence_proven'])
        self.assertFalse(details['recognition_failure_proven'])
        self.assertEqual(set(details['unresolved_fields'].values()),{'not_observed'})

    def test_optional_bonus_conflict_and_temporal_support_are_distinct(self):
        r=report();r['gameplay_tracking']['concerts']=[
            dict(source_timestamp_ms=1000,evidence=['conflict.png'],all_bonus_totals_verified=False,
                 later_active_bonus_snapshot=dict(
                     values={},observations={'specialty_priority':[dict(timestamp_ms=1000,value=10)]},
                     unresolved_fields={'specialty_priority':'conflicting_observations'},complete=False)),
            dict(source_timestamp_ms=2000,evidence=['single.png'],all_bonus_totals_verified=False,
                 later_active_bonus_snapshot=dict(
                     values={},observations={'specialty_priority':[dict(timestamp_ms=2000,value=10)]},
                     unresolved_fields={'specialty_priority':'insufficient_distinct_timestamps'},complete=False)),
        ]
        q=build(r)
        findings=[f for f in q['findings'] if f['reason']=='unverified_active_bonuses']
        self.assertEqual([f['details']['status'] for f in findings],
                         ['conflicting_observations','insufficient_temporal_support'])
        self.assertTrue(all(f['details']['source_availability']=='unadjudicated' for f in findings))
        self.assertTrue(all(not f['details']['screen_absence_proven'] for f in findings))

    def test_complete_bonus_snapshot_can_still_have_partial_mechanic_coverage(self):
        fields=('friendship_training_effectiveness','specialty_priority','support_chain_event_frequency')
        values=dict(zip(fields,(10,20,3)))
        observations={field: [dict(timestamp_ms=1000,value=value),dict(timestamp_ms=1250,value=value)]
                      for field,value in values.items()}
        r=report();r['gameplay_tracking']['concerts']=[dict(
            source_timestamp_ms=1000,evidence=['complete-panel.png'],all_bonus_totals_verified=False,
            later_active_bonus_snapshot=dict(values=values,observations=observations,
                                             unresolved_fields={},complete=True))]
        q=build(r)
        finding=next(f for f in q['findings'] if f['reason']=='unverified_active_bonuses')
        details=finding['details']
        self.assertEqual(details['status'],
                         'partial_mechanic_coverage_despite_complete_three_field_snapshot')
        self.assertTrue(details['complete_three_field_snapshot'])
        self.assertEqual(details['supported_current_fields'],list(fields))
        self.assertFalse(details['final_totals_verified'])
        self.assertFalse(details['screen_absence_proven'])

    def test_partial_inventory_details_do_not_require_scroll_or_claim_absence(self):
        r=report();r['gameplay_tracking']['owned_skill_inventory']=dict(
            complete=False,summary_frames=[dict(timestamp_ms=200000,evidence='summary.png')],
            observed_owned_cards=[dict(name_text='Example',level=None,variant=None)])
        q=build(r)
        finding=next(f for f in q['findings'] if f['reason']=='incomplete_owned_inventory')
        details=finding['details']
        self.assertEqual(details['observation_status'],'partial_visible_inventory')
        self.assertEqual(details['source_availability'],'unadjudicated')
        self.assertEqual(details['unobserved_pages'],'unknown')
        self.assertFalse(details['screen_absence_proven'])
        self.assertFalse(details['recognition_failure_proven'])
        self.assertTrue(details['partial_output_requires_no_additional_interaction'])
