import copy
import json
from pathlib import Path
import unittest

from tests import localdata
from tracen_replay.evaluation_adapters import evidence_ids, report_document, source_document
from tracen_replay.observation_evaluate import evaluate
from tests.test_causal_accounting import fixture
from tests.test_skill_batch_metadata import _batch_rows, _batch_states
from tests.test_training_gain_resolution import g7_wit_rows
from tracen_replay.transactions import skill_transactions
from tracen_replay.transactions import training_events


def source():
    return dict(source_sha256='example',start_ms=0,end_ms=300,labels=[dict(id='label',category='effect',
        first_seen_ms=150,last_seen_ms=150,evidence=['C:/recording/receipt.png'],
        expected=dict(kind='stat_change',field='speed',amount=5))])


def training_phase_report():
    report = fixture()
    data = report['gameplay_tracking']
    data['readings'] = [
        {'source_timestamp_ms': 100, 'evidence': 'gain.png',
         'screen': 'training_result', 'training_option': 'speed'},
        {'source_timestamp_ms': 200, 'evidence': 'result.png',
         'screen': 'training_result', 'training_option': None},
    ]
    direct = {
        'value': 7,
        'basis': 'repeated_training_gain_badge',
        'observations': [{'source_timestamp_ms': 100, 'evidence': 'gain.png',
                          'value': 7}],
        'source_timestamps_ms': [100],
        'evidence': ['gain.png'],
        'observation_count': 1,
    }
    data['events'] = [{
        'id': 'training-0001', 'kind': 'training', 'training_option': 'speed',
        'first_seen_ms': 100, 'last_seen_ms': 200,
        'deltas': {'speed': 7}, 'field_evidence': {'speed': ['gain.png']},
        'direct_gain_provenance': {'speed': direct},
        'result_group': {
            'interval_ms': [100, 200], 'training_option': 'speed',
            'observations': [
                {'source_timestamp_ms': 100, 'evidence': 'gain.png',
                 'training_option': 'speed', 'screen': 'training_result'},
                {'source_timestamp_ms': 200, 'evidence': 'result.png',
                 'training_option': None, 'screen': 'training_result'},
            ],
        },
    }]
    data['turn_action_receipts'] = []
    data['checkpoints'] = []
    data['performance_accounting'] = {'checkpoints': []}
    return report


def skill_batch_report():
    report = fixture()
    data = report['gameplay_tracking']
    readings = _batch_rows()
    batch = skill_transactions(readings, _batch_states())[0]
    data['readings'] = readings
    data['events'] = []
    data['turn_action_receipts'] = []
    data['checkpoints'] = []
    data['performance_accounting'] = {'checkpoints': []}
    data['skill_purchases'] = [batch]
    return report


def source_clipped_training_report():
    rows = g7_wit_rows()
    rows.append({
        'source_timestamp_ms': 1644800,
        'evidence': 'training-inspection/1644250/frame-000017.png',
        'screen': 'training_result',
        'training_option': None,
        'facts': {},
    })
    report = fixture()
    data = report['gameplay_tracking']
    data['readings'] = copy.deepcopy(rows)
    data['events'] = training_events(copy.deepcopy(rows), [])
    data['turn_action_receipts'] = []
    data['checkpoints'] = []
    data['performance_accounting'] = {'checkpoints': []}
    return report


class AdapterTests(unittest.TestCase):
    def test_source_and_report_join_without_amount_based_identity(self):
        ref=source_document(source(),evidence_root='C:/recording')
        report=fixture(); report['gameplay_tracking']['events'][0]['effects'][0]['amount']=8
        pred=report_document(report)
        score=evaluate(ref,pred)
        self.assertEqual(score['results'][0]['status'],'incorrect')
        amount=next(f for f in score['results'][0]['fields'] if f['field']=='/amount')
        self.assertEqual((amount['expected'],amount['actual']),(5,8))

    def test_summary_is_not_an_extra_effect(self):
        pred=report_document(fixture())
        self.assertEqual(len([r for r in pred['observations'] if r['category']=='effect']),1)

    def test_source_clipped_gain_projects_validated_amount_and_result_phase(self):
        pred = report_document(source_clipped_training_report())
        effect = next(row for row in pred['observations']
                      if row['payload'].get('field') == 'wit')
        self.assertEqual(effect['payload']['amount'], 65)
        self.assertEqual(effect['amount_evidence'], [
            'training-inspection/1644250/frame-000014.png',
            'training-inspection/1644483-native-v2/frame-000014.png',
            'training-inspection/1644483-native-v2/frame-000016.png',
        ])
        self.assertEqual(effect['phase_evidence'], [
            'training-inspection/1644250/frame-000017.png',
        ])
        self.assertEqual(effect['observation_basis'],
                         'accepted_training_result_group_after_direct_gain')

    def test_source_clipped_gain_invalid_nested_proof_cannot_project_phase(self):
        report = source_clipped_training_report()
        event = report['gameplay_tracking']['events'][0]
        event['direct_gain_provenance']['wit']['observations'][0]['evidence'] = (
            'tampered/frame.png'
        )
        pred = report_document(report)
        effect = next(row for row in pred['observations']
                      if row['payload'].get('field') == 'wit')
        self.assertNotIn('amount_evidence', effect)
        self.assertNotIn('phase_evidence', effect)
        self.assertNotIn('observation_basis', effect)


    def test_field_timestamp_not_parent_event_span_drives_receipt_matching(self):
        report=fixture(); report['gameplay_tracking']['events'][0].update(first_seen_ms=110,last_seen_ms=190)
        pred=report_document(report)
        effect=next(r for r in pred['observations'] if r['category']=='effect')
        self.assertEqual((effect['start_ms'],effect['end_ms']),(150,150))

    def test_explicit_root_conversion_cannot_match_same_basename_elsewhere(self):
        self.assertEqual(evidence_ids(['C:\\recording\\receipt.png'],'C:/recording'),['receipt.png'])
        self.assertEqual(evidence_ids('other/receipt.png'),['other/receipt.png'])
        with self.assertRaises(ValueError): evidence_ids('C:/elsewhere/receipt.png','C:/recording')
        with self.assertRaises(ValueError): evidence_ids('../receipt.png')

    def test_amendment_preserves_original_and_checks_previous_value(self):
        doc=source(); original=copy.deepcopy(doc)
        amendment=dict(label_id='label',field='expected',before=doc['labels'][0]['expected'],
                       after=dict(kind='stat_change',field='speed',amount=6),reason='Source screenshot rechecked',evidence=['receipt.png'])
        ref=source_document(doc,evidence_root='C:/recording',amendments=[amendment])
        self.assertEqual(ref['observations'][0]['payload']['amount'],6)
        self.assertEqual(ref['original_labels'],original['labels'])
        self.assertEqual(doc,original)
        amendment['before']={'amount':999}
        with self.assertRaises(ValueError): source_document(doc,amendments=[amendment])

    def test_unknown_amount_is_explicit_unobservable_field(self):
        doc=source(); doc['labels'][0]['expected']['amount']=None
        ref=source_document(doc,evidence_root='C:/recording')
        score=evaluate(ref,report_document(fixture()))
        self.assertEqual(next(f for f in score['results'][0]['fields'] if f['field']=='/amount')['status'],'unobservable')

    def test_source_adapter_scores_canonical_race_and_skill_batch_fields(self):
        doc = dict(source_sha256='example', start_ms=0, end_ms=300, labels=[
            dict(id='race-items', category='effect', first_seen_ms=150,
                 last_seen_ms=150, evidence=['race.png'], expected={
                     'kind': 'race_reward', 'field': 'item_quantities',
                     'values': [1, 400], 'item_names_visible': False,
                     'list_complete': False}),
            dict(id='skill-batch', category='purchase', first_seen_ms=200,
                 last_seen_ms=200, evidence=['skill.png'], expected={
                     'kind': 'skill', 'visible_confirmation_names': ['Keen Eye'],
                     'skill_points_after': 25}),
            dict(id='race-course', category='action', first_seen_ms=250,
                 last_seen_ms=250, evidence=['race-action.png'], expected={
                     'kind': 'race', 'course': {
                         'venue': 'Nakayama', 'surface': 'turf', 'distance_m': 2000,
                     }}),
        ])
        ref = source_document(doc)
        self.assertEqual(ref['observations'][0]['payload']['values'], [1, 400])
        self.assertEqual(ref['observations'][0]['unscored_expected_fields'], {})
        self.assertEqual(ref['observations'][1]['payload']['skill_points_after'], 25)
        self.assertEqual(ref['observations'][1]['payload']['visible_confirmation_names'], ['Keen Eye'])
        self.assertEqual(ref['observations'][2]['payload']['course'], {
            'venue': 'Nakayama', 'surface': 'turf', 'distance_m': 2000,
        })
        self.assertEqual(ref['observations'][2]['unscored_expected_fields'], {})

    def test_unadapted_source_details_are_retained_and_do_not_certify_coverage(self):
        doc=source(); doc['labels'][0]['expected']['future_metadata']='keep this'
        ref=source_document(doc,evidence_root='C:/recording')
        self.assertEqual(ref['observations'][0]['unscored_expected_fields']['future_metadata'],'keep this')
        self.assertFalse(evaluate(ref,report_document(fixture()))['passed'])

    def test_partial_action_identity_does_not_require_unlabeled_name(self):
        doc=dict(source_sha256='example',start_ms=0,end_ms=300,actions=[dict(kind='race',start_ms=100,end_ms=200)])
        ref=source_document(doc)
        pred=dict(source_sha256='example',auxiliary_log_used=False,observations=[dict(id='race',category='action',
            phase='committed',start_ms=150,end_ms=150,payload={'kind':'race','name':'Example Race'})])
        self.assertEqual(evaluate(ref,pred)['results'][0]['status'],'correct')
        self.assertEqual(ref['original_document'],doc)

    def test_composite_success_label_keeps_ungraded_friendship_detail(self):
        doc=dict(source_sha256='example',start_ms=0,end_ms=300,actions=[dict(kind='training',
            training_option='speed',result='Friendship training success',start_ms=100,end_ms=200)])
        row=source_document(doc)['observations'][0]
        self.assertEqual(row['payload']['result'],'success')
        self.assertEqual(row['unscored_expected_fields']['result_detail'],'Friendship training success')

    def test_skill_purchase_projects_confirmed_names_and_unambiguous_after_balance(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [
            {'source_timestamp_ms': 105, 'evidence': ['skill-confirm.png']},
            {'source_timestamp_ms': 120, 'evidence': ['skill-after.png']},
        ]
        data['skill_purchases'] = [{
            'id': 'skills-001', 'first_seen_ms': 105, 'last_seen_ms': 110,
            'evidence': ['skill-confirm.png'],
            'spent_skill_points': 123,
            'visible_confirmation_names': ['Keen Eye', 'Watchful Eye'],
            'purchased_list_complete': False,
            'balance_evidence': [{
                'role': 'after', 'skill_points': 25,
                'evidence': ['skill-after.png'],
            }],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'purchase')
        self.assertEqual(row['payload']['visible_confirmation_names'],
                         ['Keen Eye', 'Watchful Eye'])
        self.assertEqual(row['payload']['skill_points_after'], 25)
        self.assertFalse(row['payload']['confirmation_names_complete'])
        self.assertIn('skill-after.png', row['evidence'])

    def test_skill_purchase_omits_ambiguous_after_balance_instead_of_zero(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [{'source_timestamp_ms': 105, 'evidence': ['skill.png']}]
        data['skill_purchases'] = [{
            'id': 'skills-001', 'first_seen_ms': 105, 'last_seen_ms': 110,
            'evidence': ['skill.png'],
            'balance_evidence': [
                {'role': 'after', 'skill_points': 25},
                {'role': 'after', 'skill_points': 0},
            ],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'purchase')
        self.assertNotIn('skill_points_after', row['payload'])

    def test_race_reward_projects_all_sections_without_dropping_quantities(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [{'source_timestamp_ms': 150, 'evidence': ['race-items.png']}]
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001',
            'source_timestamp_ms': 140, 'evidence': ['race-action.png'],
        }]
        data['readings'].append({'source_timestamp_ms': 140, 'evidence': ['race-action.png']})
        data['races'] = [{
            'id': 'race-001', 'race_name': 'Example Cup', 'placing': 1,
            'course': {'venue': 'Nakayama', 'surface': 'turf', 'distance_m': 2000},
            'first_seen_ms': 140, 'last_seen_ms': 160,
            'visible_item_reward_snapshots': [{
                'first_seen_ms': 150, 'last_seen_ms': 150,
                'evidence': ['race-items.png'], 'list_complete': False,
                'items': [
                    {'quantity': 1, 'name': None, 'section': 'items'},
                    {'quantity': 400, 'name': None, 'section': 'items'},
                    {'quantity': 20, 'name': None, 'section': 'bonus'},
                ],
            }],
        }]
        adapted = report_document(report)
        action = next(row for row in adapted['observations'] if row['category'] == 'action')
        self.assertEqual(action['payload'], {
            'kind': 'race', 'name': 'Example Cup', 'placing': 1,
            'course': {'venue': 'Nakayama', 'surface': 'turf', 'distance_m': 2000},
        })
        rows = [row for row in adapted['observations']
                if row['payload'].get('kind') == 'race_reward']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['payload'], {
            'kind': 'race_reward', 'field': 'item_quantities',
            'values': [1, 400, 20], 'item_names_visible': False,
            'sections': ['items', 'items', 'bonus'],
            'list_complete': False,
        })

    def test_friendship_maxed_effect_is_preserved_as_status(self):
        report = fixture()
        event = report['gameplay_tracking']['events'][0]
        event['effects'] = [{
            'kind': 'friendship_status', 'name': 'Yaeno Muteki',
            'value': 'maximum',
        }]
        event['field_evidence'] = {
            'friendship_status|Yaeno Muteki|': ['receipt.png'],
        }
        rows = [row for row in report_document(report)['observations']
                if row['category'] == 'effect']
        self.assertEqual(rows[0]['payload'], {
            'kind': 'friendship_status', 'name': 'Yaeno Muteki',
            'value': 'maximum',
        })

    def test_outing_projects_linked_event_name_and_companion(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['events'] = [{
            'id': 'outcome-001', 'kind': 'outcome',
            'context_title': 'At Rainbow Cove',
            'first_seen_ms': 150, 'last_seen_ms': 170,
            'effects': [],
        }]
        data['turn_action_receipts'] = [{
            'kind': 'outing', 'event_id': 'outcome-001',
            'source_timestamp_ms': 150, 'evidence': ['outing.png'],
            'companion': 'Light Hello',
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['payload'], {
            'kind': 'outing', 'name': 'At Rainbow Cove',
            'companion': 'Light Hello',
        })

    def test_race_projects_direct_fans_gained_effect(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [{'source_timestamp_ms': 160, 'evidence': ['race.png']}]
        data['races'] = [{
            'id': 'race-001', 'race_name': 'NHK Mile Cup',
            'fans_gained': 17457, 'first_seen_ms': 150, 'last_seen_ms': 170,
            'evidence': ['race.png'], 'visible_item_reward_snapshots': [],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['payload'].get('kind') == 'fan_change')
        self.assertEqual(row['payload'], {
            'kind': 'fan_change', 'name': 'NHK Mile Cup', 'amount': 17457,
        })
        self.assertEqual((row['start_ms'], row['end_ms']), (160, 160))

    def test_state_projects_caps_from_same_supporting_frame(self):
        report = fixture()
        data = report['gameplay_tracking']
        checkpoint = data['checkpoints'][0]
        checkpoint['supporting_frames'] = ['receipt.png']
        data['readings'][0].update(
            stats={'values': copy.deepcopy(checkpoint['values'])},
            facts={'stat_caps': {
                'speed': 1600, 'stamina': 1341, 'power': 1348,
                'guts': 1500, 'wit': 1300,
            }})
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'state' and row['payload']['channel'] == 'stats')
        self.assertEqual(row['payload']['caps'], {
            'speed': 1600, 'stamina': 1341, 'power': 1348,
            'guts': 1500, 'wit': 1300,
        })
        self.assertIn('receipt.png', row['evidence'])

    def test_state_does_not_borrow_caps_from_another_frame(self):
        report = fixture()
        data = report['gameplay_tracking']
        checkpoint = data['checkpoints'][0]
        checkpoint['supporting_frames'] = ['owned.png']
        data['readings'][0].update(
            evidence='other.png',
            stats={'values': copy.deepcopy(checkpoint['values'])},
            facts={'stat_caps': {'speed': 1600}})
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'state' and row['payload']['channel'] == 'stats')
        self.assertNotIn('caps', row['payload'])

    def test_persisted_state_snapshot_replaces_only_an_identical_checkpoint(self):
        report = fixture()
        data = report['gameplay_tracking']
        checkpoint = data['checkpoints'][0]
        data['state_observations'] = [{
            'id': 'state-snapshot-0',
            'source_ref': '/gameplay_tracking/state_observations/0',
            'category': 'state',
            'phase': 'observed',
            'start_ms': checkpoint['first_seen_ms'],
            'end_ms': checkpoint['last_seen_ms'],
            'evidence': ['receipt.png'],
            'payload': {
                'kind': 'state', 'channel': 'stats',
                'values': copy.deepcopy(checkpoint['values']),
            },
            'source_observations': [{
                'source_ref': '/gameplay_tracking/readings/0',
                'evidence': 'receipt.png',
                'timestamp_ms': 150,
            }],
            'status': 'observed',
        }]

        rows = report_document(report)['observations']
        state_rows = [row for row in rows
                      if row['category'] == 'state'
                      and row['payload'].get('channel') == 'stats']
        self.assertEqual([row['id'] for row in state_rows], [
            '/gameplay_tracking/state_observations/0',
            '/gameplay_tracking/checkpoints/1',
        ])
        self.assertEqual(state_rows[0]['payload']['values'], checkpoint['values'])

    def test_persisted_status_keeps_typed_mood_value(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['status_observations'] = [{
            'id': 'mood-0',
            'source_ref': '/gameplay_tracking/status_observations/0',
            'category': 'effect',
            'phase': 'observed',
            'start_ms': 150,
            'end_ms': 150,
            'evidence': ['receipt.png'],
            'payload': {'kind': 'mood_status', 'value': 'great'},
            'status': 'observed',
        }]

        row = next(row for row in report_document(report)['observations']
                   if row['id'] == '/gameplay_tracking/status_observations/0')
        self.assertEqual(row['payload'], {'kind': 'mood_status', 'value': 'great'})

    def test_infirmary_projects_explicit_recovery_success_and_result_interval(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [
            {'source_timestamp_ms': 100, 'evidence': 'infirmary-confirm.png'},
            {'source_timestamp_ms': 180, 'evidence': 'infirmary-result.png'},
        ]
        data['turn_action_receipts'] = [{
            'kind': 'infirmary', 'source_timestamp_ms': 180,
            'confirmation_timestamp_ms': 100,
            'result_first_seen_ms': 180, 'result_last_seen_ms': 200,
            'evidence': ['infirmary-confirm.png'],
            'result_evidence': ['infirmary-result.png'],
            'recovery_effects': [{
                'kind': 'condition_removed', 'name': 'Practice Poor',
                'raw_text': 'Practice Poor was removed.',
            }],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['payload'], {'kind': 'infirmary', 'result': 'success'})
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 200))
        self.assertIn('infirmary-result.png', row['evidence'])
        recovery = next(row for row in report_document(report)['observations']
                        if row['payload'].get('kind') == 'condition_removed')
        self.assertEqual(recovery['payload'], {
            'kind': 'condition_removed', 'name': 'Practice Poor',
        })
        self.assertEqual((recovery['start_ms'], recovery['end_ms']), (180, 180))

    def test_dialogue_choice_projects_same_frame_context_as_preview_offer(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [{
            'source_timestamp_ms': 100, 'evidence': 'choice.png',
            'context_title': 'Everyone\'s Oguri Cap',
        }]
        data['dialogue_choices'] = [{
            'kind': 'dialogue_choice',
            'options': ['Make her dreams come true!', 'Do my best.'],
            'first_seen_ms': 100, 'selection_observed_ms': 120,
            'evidence': ['choice.png'],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['payload'].get('kind') == 'event_choice')
        self.assertEqual(row['phase'], 'preview')
        self.assertEqual(row['payload'], {
            'kind': 'event_choice',
            'name': 'Everyone\'s Oguri Cap',
            'choices': ['Make her dreams come true!', 'Do my best.'],
        })

    def test_persisted_preview_observation_is_consumed_without_reparsing_readings(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['preview_observations'] = [{
            'id': 'preview-1', 'category': 'effect', 'phase': 'preview',
            'payload': {'kind': 'stat_change', 'field': 'speed', 'amount': 5},
            'start_ms': 100, 'end_ms': 120, 'evidence': ['preview.png'],
            'status': 'observed', 'occurrence_key': 'preview:one',
            'observation_basis': 'accepted_typed_preview_fact',
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/preview_observations/0'))
        self.assertEqual(row['phase'], 'preview')
        self.assertEqual(row['payload']['amount'], 5)
        self.assertEqual(row['occurrence_key'], 'preview:one')

        data['preview_observations'][0]['status'] = 'ambiguous'
        self.assertFalse(any(row['id'].endswith('/preview_observations/0')
                             for row in report_document(report)['observations']))

    def test_persisted_preview_uncertainty_scope_survives_report_adapter(self):
        report = fixture()
        report['gameplay_tracking']['preview_observations'] = [{
            'id': 'preview-1', 'category': 'purchase', 'phase': 'preview',
            'payload': {'kind': 'skill', 'selected_draft_names': ['Pace Chaser Savvy']},
            'start_ms': 100, 'end_ms': 120, 'evidence': ['preview.png'],
            'status': 'observed', 'uncertain': True,
            'uncertainty_fields': ['/visible_card_prices/Other Skill'],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/preview_observations/0'))
        self.assertTrue(row['uncertain'])
        self.assertEqual(row['uncertainty_fields'],
                         ['/visible_card_prices/Other Skill'])

        report['gameplay_tracking']['preview_observations'] = []
        report['gameplay_tracking']['skill_menu_observations'] = [{
            'id': 'menu-1', 'category': 'purchase', 'phase': 'preview',
            'payload': {'kind': 'skill', 'selected_draft_names': ['Pace Chaser Savvy']},
            'start_ms': 100, 'end_ms': 120, 'evidence': ['preview.png'],
            'status': 'observed', 'uncertain': True,
            'uncertainty_fields': ['/visible_card_prices/Other Skill'],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_menu_observations/0'))
        self.assertTrue(row['uncertain'])
        self.assertEqual(row['uncertainty_fields'],
                         ['/visible_card_prices/Other Skill'])

    def test_explicit_race_phase_and_result_interval_are_preserved(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001', 'source_timestamp_ms': 180,
            'phase': 'observed', 'result_first_seen_ms': 150,
            'result_last_seen_ms': 200, 'evidence': ['race-result.png'],
        }]
        data['races'] = [{
            'id': 'race-001', 'race_name': 'NHK Mile Cup', 'placing': 1,
            'first_seen_ms': 150, 'last_seen_ms': 200,
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['phase'], 'observed')
        self.assertEqual((row['start_ms'], row['end_ms']), (150, 200))

    def test_race_action_fallback_projects_source_bound_fields_without_result_map(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['races'] = []
        data['readings'] = [{'source_timestamp_ms': 150,
                            'evidence': ['race-result.png']}]
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001', 'source_timestamp_ms': 150,
            'evidence': ['race-result.png'], 'race_name': 'Example Cup',
            'placing': 2,
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['payload']['name'], 'Example Cup')
        self.assertEqual(row['payload']['placing'], 2)
        self.assertIn('race-result.png', row['evidence'])
        self.assertEqual(row['observation_basis'],
                         'source_bound_race_action_metadata')

    def test_race_action_fallback_rejects_preview_fields(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['races'] = []
        data['readings'] = [{'source_timestamp_ms': 150,
                            'evidence': ['race-preview.png']}]
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001', 'source_timestamp_ms': 150,
            'evidence': ['race-preview.png'], 'race_name': 'Example Cup',
            'placing': 2, 'phase': 'preview',
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['payload'], {'kind': 'race'})
        self.assertNotIn('observation_basis', row)

    def test_race_action_fallback_keeps_placing_when_name_is_ambiguous(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['races'] = []
        data['readings'] = [{'source_timestamp_ms': 150,
                            'evidence': ['race-result.png']}]
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001', 'source_timestamp_ms': 150,
            'evidence': ['race-result.png'], 'race_name': 'Example Cup',
            'name': 'Different Cup', 'placing': 2,
            'conflicting_readings': {'race_name': ['Example Cup', 'Different Cup']},
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertNotIn('name', row['payload'])
        self.assertEqual(row['payload']['placing'], 2)
        self.assertEqual(row['observation_basis'],
                         'source_bound_race_action_metadata')

    def test_race_action_fallback_rejects_unscoped_conflict(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['races'] = []
        data['readings'] = [{'source_timestamp_ms': 150,
                            'evidence': ['race-result.png']}]
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001', 'source_timestamp_ms': 150,
            'evidence': ['race-result.png'], 'race_name': 'Example Cup',
            'placing': 2, 'conflicting_readings': {'parser_disagreement': ['x']},
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['payload'], {'kind': 'race'})
        self.assertNotIn('observation_basis', row)

    def test_race_result_grade_rejects_malformed_or_unscoped_conflict_container(self):
        for conflicts in (['race_grade', 'G2'], 'race_grade',
                          {'parser_disagreement': ['G2', 'G3']}):
            with self.subTest(conflicts=conflicts):
                report = fixture()
                data = report['gameplay_tracking']
                data['readings'] = [{'source_timestamp_ms': 150,
                                     'evidence': ['race-result.png']}]
                data['races'] = [{
                    'id': 'race-001', 'race_name': 'Example Cup',
                    'race_grade': 'G2', 'placing': 1,
                    'first_seen_ms': 150, 'last_seen_ms': 200,
                    'conflicting_readings': conflicts,
                }]
                data['turn_action_receipts'] = [{
                    'kind': 'race', 'race_id': 'race-001',
                    'source_timestamp_ms': 150,
                    'evidence': ['race-result.png'],
                }]
                row = next(row for row in report_document(report)['observations']
                           if row['category'] == 'action')
                self.assertEqual(row['payload'].get('name'), 'Example Cup')
                self.assertNotIn('grade', row['payload'])

    def test_race_result_grade_survives_field_scoped_name_conflict(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [{'source_timestamp_ms': 150,
                             'evidence': ['race-result.png']}]
        data['races'] = [{
            'id': 'race-001', 'race_name': 'Example Cup',
            'race_grade': 'G2', 'placing': 1,
            'first_seen_ms': 150, 'last_seen_ms': 200,
            'conflicting_readings': {
                'race_name': ['Example Cup', 'Different Cup'],
            },
        }]
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001',
            'source_timestamp_ms': 150,
            'evidence': ['race-result.png'],
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['payload']['grade'], 'G2')

    def test_race_action_fallback_honors_binding_field_conflict(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['races'] = []
        data['readings'] = [{'source_timestamp_ms': 150,
                            'evidence': ['race-result.png']}]
        data['turn_action_receipts'] = [{
            'kind': 'race', 'race_id': 'race-001', 'source_timestamp_ms': 150,
            'evidence': ['race-result.png'], 'race_name': 'Example Cup',
            'placing': 2,
            'race_action_binding': {
                'status': 'bound',
                'metadata_status': {
                    'race_name': 'unknown_conflicting_readings',
                    'placing': 'observed',
                },
            },
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertNotIn('name', row['payload'])
        self.assertEqual(row['payload']['placing'], 2)

    def test_training_name_is_projected_only_from_canonical_action_field(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [{'source_timestamp_ms': 150, 'evidence': 'training.png'}]
        data['events'][0].update(kind='training', training_option='wit',
                                 first_seen_ms=150, last_seen_ms=150,
                                 training_name='Video Research')
        data['turn_action_receipts'] = [{
            'kind': 'training', 'event_id': 'event', 'training_option': 'wit',
            'training_name': 'Video Research',
            'training_name_evidence': ['training.png'],
            'action_identity_evidence': ['training.png'],
            'source_timestamp_ms': 150, 'evidence': ['training.png'],
            'training_outcome': 'success',
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['category'] == 'action')
        self.assertEqual(row['payload']['training_name'], 'Video Research')

        data['turn_action_receipts'][0].pop('training_name')
        adapted = report_document(report)
        row = next(row for row in adapted['observations'] if row['category'] == 'action')
        self.assertNotIn('training_name', row['payload'])

    def test_training_result_phase_uses_result_group_after_direct_amount(self):
        report = training_phase_report()
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (200, 200))
        self.assertEqual(row['amount_evidence'], ['gain.png'])
        self.assertEqual(row['phase_evidence'], ['result.png'])
        self.assertEqual(row['evidence'], ['gain.png', 'result.png'])
        self.assertEqual(row['observation_basis'],
                         'accepted_training_result_group_after_direct_gain')
        reference = dict(source_sha256='example', start_ms=0, end_ms=300,
                         labels=[dict(id='result', category='effect',
                                      first_seen_ms=200, last_seen_ms=200,
                                      evidence=['result.png'], expected={
                                          'kind': 'stat_change', 'field': 'speed',
                                          'amount': 7})])
        self.assertEqual(evaluate(source_document(reference), adapted)
                         ['results'][0]['status'], 'correct')

    def test_wrong_result_group_path_cannot_supply_applied_phase(self):
        report = training_phase_report()
        group = report['gameplay_tracking']['events'][0]['result_group']
        group['observations'][1]['evidence'] = 'foreign-result.png'
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
        self.assertNotIn('phase_evidence', row)
        self.assertEqual(evaluate(source_document(dict(
            source_sha256='example', start_ms=0, end_ms=300, labels=[
                dict(id='result', category='effect', first_seen_ms=200,
                     last_seen_ms=200, evidence=['result.png'], expected={
                         'kind': 'stat_change', 'field': 'speed', 'amount': 7})])),
            adapted)['results'][0]['status'], 'missed')

    def test_preview_or_wrong_option_result_group_cannot_supply_phase(self):
        for mode in ('preview', 'wrong-option'):
            with self.subTest(mode=mode):
                report = training_phase_report()
                group = report['gameplay_tracking']['events'][0]['result_group']
                reading = report['gameplay_tracking']['readings'][1]
                if mode == 'preview':
                    group['observations'][1]['screen'] = 'training_preview'
                    reading['screen'] = 'training_preview'
                else:
                    group['observations'][1]['training_option'] = 'power'
                    reading['training_option'] = 'power'
                adapted = report_document(report)
                row = next(row for row in adapted['observations']
                           if row['id'].endswith('/events/0/deltas/speed'))
                self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
                self.assertNotIn('phase_evidence', row)

    def test_result_group_without_canonical_amount_stays_unpromoted(self):
        report = training_phase_report()
        event = report['gameplay_tracking']['events'][0]
        event['deltas'] = {}
        adapted = report_document(report)
        self.assertFalse(any(row['id'].endswith('/events/0/deltas/speed')
                             for row in adapted['observations']))

        report = training_phase_report()
        report['gameplay_tracking']['events'][0].pop('direct_gain_provenance')
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
        self.assertNotIn('phase_evidence', row)

    def test_indirect_basis_cannot_be_used_as_direct_amount_proof(self):
        report = training_phase_report()
        event = report['gameplay_tracking']['events'][0]
        event['direct_gain_provenance']['speed']['basis'] = 'indirect_balance_match'
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
        self.assertNotIn('phase_evidence', row)

    def test_conflicted_amount_cannot_be_promoted_by_a_result_group(self):
        report = training_phase_report()
        report['gameplay_tracking']['events'][0]['conflicting_readings'] = {
            'speed': [7, 9],
        }
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
        self.assertNotIn('phase_evidence', row)

    def test_duplicate_source_identity_cannot_supply_result_phase(self):
        report = training_phase_report()
        report['gameplay_tracking']['readings'].append(
            copy.deepcopy(report['gameplay_tracking']['readings'][1]))
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
        self.assertNotIn('phase_evidence', row)

    def test_unscoped_conflict_dict_cannot_supply_result_phase(self):
        report = training_phase_report()
        report['gameplay_tracking']['events'][0]['conflicting_readings'] = {
            'parser_disagreement': ['speed'],
        }
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
        self.assertNotIn('phase_evidence', row)

    def test_source_gain_fact_mismatch_cannot_supply_result_phase(self):
        report = training_phase_report()
        report['gameplay_tracking']['readings'][0]['facts'] = {
            'training_gains': {'speed': 9},
        }
        adapted = report_document(report)
        row = next(row for row in adapted['observations']
                   if row['id'].endswith('/events/0/deltas/speed'))
        self.assertEqual((row['start_ms'], row['end_ms']), (100, 100))
        self.assertNotIn('phase_evidence', row)

    def test_effect_conflict_is_scoped_to_the_named_field(self):
        report = fixture()
        event = report['gameplay_tracking']['events'][0]
        event['effects'] = [
            {'kind': 'stat_change', 'field': 'speed', 'amount': 3},
            {'kind': 'friendship_status', 'name': 'Air Groove', 'value': 'maximum'},
        ]
        event['field_evidence'] = {
            'stat_change|speed|': ['receipt.png'],
            'friendship_status||Air Groove': ['receipt.png'],
        }
        event['conflicting_readings'] = [
            {'field': 'friendship_status||Air Groove', 'values': ['maximum', None]},
        ]
        rows = report_document(report)['observations']
        speed = next(row for row in rows if row['payload'].get('field') == 'speed')
        friendship = next(row for row in rows
                          if row['payload'].get('kind') == 'friendship_status')
        self.assertFalse(speed['uncertain'])
        self.assertTrue(friendship['uncertain'])

    def test_skill_batch_projects_source_bound_canonical_metadata(self):
        report = skill_batch_report()
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertEqual(row['payload']['identity_status'], 'ambiguous')
        self.assertEqual(row['payload']['post_learn_obtained_names'], ['Later Skill'])
        self.assertNotIn('price_status', row['payload'])
        self.assertEqual(row['payload']['precommit_price_visibility'], {
            'phase': 'preview',
            'status': 'partially_visible',
            'basis': 'visible_skill_card_prices_with_incomplete_item_list',
            'observed_card_count': 1,
            'observed_timestamps': [250, 500],
        })
        self.assertEqual(row['payload']['confirmation_text'],
                         'our trainee learned new skills!')
        self.assertEqual(row['payload']['skill_points_after'], 700)
        self.assertNotIn('selected_item_candidates', row['payload'])
        self.assertIn('2000.png', row['evidence'])
        self.assertIn('250.png', row['evidence'])
        self.assertIn('1500.png', row['evidence'])

    def test_skill_batch_projects_only_committed_explicit_price_status(self):
        report = skill_batch_report()
        purchase = report['gameplay_tracking']['skill_purchases'][0]
        purchase['price_status'] = 'unreadable'
        purchase['price_status_basis'] = 'explicit_source_price_status'
        purchase['price_status_evidence'] = ['750.png', '900.png']
        purchase['price_status_observed_card_count'] = 0
        purchase['price_status_observed_timestamps'] = [750, 900]
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertEqual(row['payload']['price_status'], 'unreadable')
        self.assertEqual(row['payload']['precommit_price_visibility']['status'],
                         'partially_visible')

    def test_skill_batch_keeps_precommit_offer_without_committed_price_claim(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['readings'] = [{
            'source_timestamp_ms': 100, 'evidence': 'offer.png',
            'screen': 'skill_selection',
        }]
        data['events'] = []
        data['turn_action_receipts'] = []
        data['checkpoints'] = []
        data['performance_accounting'] = {'checkpoints': []}
        data['skill_purchases'] = [{
            'id': 'skills-001', 'first_seen_ms': 200, 'last_seen_ms': 250,
            'evidence': ['offer.png'],
            'confirmation_first_seen_ms': 200,
            'precommit_price_visibility': {
                'phase': 'preview', 'status': 'partially_visible',
                'basis': 'visible_skill_card_prices_with_incomplete_item_list',
                'observed_card_count': 1, 'observed_timestamps': [100],
                'evidence': ['offer.png'],
            },
        }]
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertNotIn('price_status', row['payload'])
        self.assertEqual(row['payload']['precommit_price_visibility']['status'],
                         'partially_visible')

    def test_legacy_selection_derived_price_status_is_not_projected_as_committed(self):
        report = skill_batch_report()
        purchase = report['gameplay_tracking']['skill_purchases'][0]
        purchase['price_status'] = 'partially_visible'
        purchase['price_status_basis'] = 'visible_skill_card_prices_with_incomplete_item_list'
        purchase['price_status_evidence'] = ['250.png', '500.png']
        purchase['price_status_observed_card_count'] = 1
        purchase['price_status_observed_timestamps'] = [250, 500]
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertNotIn('price_status', row['payload'])

    def test_skill_batch_metadata_without_identity_proof_stays_unprojected(self):
        report = skill_batch_report()
        report['gameplay_tracking']['skill_purchases'][0].pop(
            'identity_status_evidence')
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertNotIn('identity_status', row['payload'])
        self.assertEqual(row['payload']['visible_confirmation_names'], ['Known Skill'])

    def test_skill_batch_metadata_with_missing_post_name_proof_stays_unknown(self):
        report = skill_batch_report()
        purchase = report['gameplay_tracking']['skill_purchases'][0]
        purchase['post_learn_obtained_name_evidence']['Later Skill'] = ['missing.png']
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertNotIn('post_learn_obtained_names', row['payload'])
        self.assertNotIn('missing.png', row['evidence'])

    def test_skill_batch_metadata_with_ambiguous_post_name_source_stays_unknown(self):
        report = skill_batch_report()
        readings = report['gameplay_tracking']['readings']
        source_row = next(row for row in readings
                          if row.get('evidence') == '2000.png')
        conflicting = copy.deepcopy(source_row)
        conflicting['facts']['skill_cards'][0]['name'] = 'Different Skill'
        readings.append(conflicting)
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertNotIn('post_learn_obtained_names', row['payload'])

    def test_post_receipt_name_is_kept_when_also_visible_in_confirmation(self):
        report = skill_batch_report()
        purchase = report['gameplay_tracking']['skill_purchases'][0]
        purchase['post_learn_obtained_names'] = ['Known Skill']
        purchase['post_learn_obtained_name_evidence'] = {
            'Known Skill': ['2000.png', '2250.png'],
        }
        purchase['post_learn_obtained_name_provenance'][0]['name'] = 'Known Skill'
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertEqual(row['payload']['post_learn_obtained_names'], ['Known Skill'])

    def test_unknown_skill_prices_do_not_become_item_costs_or_batch_debit(self):
        report = skill_batch_report()
        purchase = report['gameplay_tracking']['skill_purchases'][0]
        purchase['spent_skill_points'] = None
        purchase['selected_item_candidates'] = [{
            'name': 'Known Skill', 'cost': 123, 'evidence': ['250.png'],
        }]
        purchase['price_status'] = 'unknown'
        purchase.pop('price_status_evidence', None)
        row = next(row for row in report_document(report)['observations']
                   if row['id'].endswith('/skill_purchases/0'))
        self.assertNotIn('cost', row['payload'])
        self.assertNotIn('price_status', row['payload'])
        self.assertNotIn('selected_item_candidates', row['payload'])


if __name__=='__main__':unittest.main()
