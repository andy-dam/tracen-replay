import json
import unittest
from unittest.mock import patch

from tests.test_gameplay import workspace_temp
from tracen_replay.transactions import training_events
from tracen_replay.training_gain_resolution import resolve_candidate_only_gain
from tracen_replay.training_gain_recovery import plan, promote, promote_with_metadata, recover
from tracen_replay.vision import parse


def _candidate(
    field,
    amount=None,
    *,
    family='gain',
    text=None,
    confidence=96.0,
    box=None,
    role='amount_crop_candidate',
    input_eligible=True,
):
    if text is None:
        text='+' + str(amount)
    if box is None:
        box={
            'speed': [300, 832, 414, 890],
            'stamina': [498, 832, 610, 890],
            'power': [696, 832, 812, 890],
            'guts': [300, 950, 414, 1008],
            'wit': [498, 950, 610, 1008],
            'skill_points': [696, 950, 812, 1008],
        }.get(field, [300, 832, 414, 890])
    return dict(
        region=f'{family}.{field}', crop_family=family, raw_text=text,
        amount=amount, confidence=confidence, box=box, source_role=role,
        input_eligible=input_eligible, canonical_eligible=False,
    )


def _row(
    timestamp,
    evidence,
    field='speed',
    amount=3,
    *,
    option='wit',
    confidence=96.0,
    wide=None,
    wide_confidence=85.0,
    wide_box=None,
    screen='training_result',
    training_preview=False,
    region_field=None,
    result_only=False,
):
    candidates=[]
    candidate_field=region_field or field
    if result_only:
        candidates.append(_candidate(
            candidate_field, amount, family='result',
            box=[322, 834, 448, 876], role='result_crop_diagnostic_excluded',
            input_eligible=False,
        ))
    else:
        candidates.append(_candidate(candidate_field, amount, confidence=confidence))
        if wide is not None:
            default_wide_box={
                'speed': [250, 812, 462, 909],
                'stamina': [448, 812, 660, 909],
                'power': [646, 812, 858, 909],
                'guts': [250, 930, 462, 1027],
                'wit': [448, 930, 660, 1027],
                'skill_points': [646, 930, 858, 1027],
            }.get(candidate_field, [250, 812, 462, 909])
            candidates.append(_candidate(
                candidate_field, None, family='wide_gain', text=wide,
                confidence=wide_confidence,
                box=wide_box or default_wide_box,
                role='unparsed_crop_candidate',
            ))
    return dict(
        source_timestamp_ms=timestamp,
        evidence=evidence,
        screen=screen,
        training_option=option,
        stats={'training_preview': training_preview},
        facts={
            'training_gains': {},
            'training_gain_crop_provenance': {
                field: {
                    'candidate_amounts': [amount] if amount is not None else [],
                    'canonical_amount': None,
                    'candidates': candidates,
                },
            },
        },
    )



class CandidateOnlyGainRecoveryTests(unittest.TestCase):
    def test_repeated_tight_badges_accept_same_phase_without_state(self):
        rows=[
            _row(262533, 'frame-17.png', confidence=90.782),
            _row(262567, 'frame-18.png', confidence=96.018),
            _row(262600, 'frame-19.png', confidence=94.911),
        ]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='training-0020')

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['accepted_amount'], 3)
        self.assertEqual(result['basis'], 'repeated_source_gain_badge_same_phase')
        self.assertEqual(
            [item['source_timestamp_ms'] for item in result['accepted_observations']],
            [262533, 262567, 262600],
        )
        self.assertFalse(result['policy']['uses_balance_arithmetic'])
        self.assertFalse(result['policy']['uses_expected_amount'])

    def test_nested_broad_crop_agrees_with_tight_badge(self):
        row=_row(
            830717, 'frame-15.png', field='wit', amount=13,
            confidence=97.591, wide='+13:', wide_confidence=84.65,
            option='wit',
            wide_box=[448, 930, 660, 1027],
        )

        result=resolve_candidate_only_gain([row], 'wit', phase_key='training-0055')

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['accepted_amount'], 13)
        self.assertEqual(result['basis'], 'same_frame_nested_source_gain_crop_agreement')
        self.assertEqual(
            {item['crop_family'] for item in result['accepted_observations']},
            {'gain', 'wide_gain'},
        )

    def test_single_below_floor_badge_is_not_enough(self):
        result=resolve_candidate_only_gain(
            [_row(10, 'frame.png', amount=13, confidence=97.591)],
            'speed', phase_key='event',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'insufficient_same_phase_corroboration')

    def test_candidate_recovery_requires_source_phase_owner(self):
        result=resolve_candidate_only_gain(
            [_row(10, 'frame.png'), _row(43, 'frame-2.png')], 'speed',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'missing_source_phase_owner')

    def test_result_crop_alone_is_never_award_evidence(self):
        result=resolve_candidate_only_gain(
            [_row(10, 'frame.png', amount=13, result_only=True)],
            'speed', phase_key='event',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'no_source_signed_gain_candidate')

    def test_preview_row_is_rejected_even_with_signed_text(self):
        result=resolve_candidate_only_gain(
            [_row(10, 'preview.png', training_preview=True)],
            'speed', phase_key='event',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'no_committed_result_phase_rows')

    def test_option_switch_is_a_phase_boundary(self):
        rows=[
            _row(10, 'wit.png', option='wit'),
            _row(43, 'speed.png', option='speed'),
        ]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'insufficient_same_phase_corroboration')

    def test_conflicting_broad_crop_blocks_repeated_tight_badges(self):
        rows=[
            _row(10, 'one.png', amount=13, wide='+12:', wide_confidence=85.0),
            _row(43, 'two.png', amount=13, wide='+12:', wide_confidence=85.0),
        ]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'insufficient_same_phase_corroboration')

    def test_dual_match_rejects_another_confident_tight_amount(self):
        row=_row(
            10, 'frame.png', amount=13, wide='+13:', wide_confidence=85.0,
        )
        row['facts']['training_gain_crop_provenance']['speed']['candidates'].append(
            _candidate('speed', 12, confidence=95.0, box=[320, 832, 414, 890]),
        )

        result=resolve_candidate_only_gain([row], 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'candidate_conflict_or_phase_change')

    def test_dual_match_rejects_another_confident_broad_amount(self):
        row=_row(
            10, 'frame.png', amount=13, wide='+13:', wide_confidence=85.0,
        )
        row['facts']['training_gain_crop_provenance']['speed']['candidates'].append(
            _candidate(
                'speed', None, family='wide_gain', text='+12:', confidence=85.0,
                box=[250, 812, 462, 909], role='unparsed_crop_candidate',
            ),
        )

        result=resolve_candidate_only_gain([row], 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'candidate_conflict_or_phase_change')

    def test_dual_match_rejects_another_eligible_phase(self):
        rows=[
            _row(
                10, 'frame.png', amount=13, wide='+13:', wide_confidence=85.0,
            ),
            _row(43, 'other-phase.png', amount=13, option='speed'),
        ]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'candidate_conflict_or_phase_change')

    def test_repeated_match_rejects_another_eligible_phase(self):
        rows=[
            _row(10, 'one.png', amount=13),
            _row(43, 'two.png', amount=13),
            _row(76, 'other-phase.png', amount=13, option='speed'),
        ]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'candidate_conflict_or_phase_change')

    def test_rejected_repeated_group_cannot_fall_through_to_dual_match(self):
        first=_row(
            10, 'one.png', amount=13, wide='+13:', wide_confidence=85.0,
        )
        first['facts']['training_gain_crop_provenance']['speed']['candidates'].append(
            _candidate(
                'speed', None, family='wide_gain', text='+12:', confidence=85.0,
                box=[250, 812, 462, 909], role='unparsed_crop_candidate',
            ),
        )
        rows=[first, _row(43, 'two.png', amount=13)]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'candidate_conflict_or_phase_change')

    def test_repeated_timestamp_samples_of_one_frame_are_deduplicated(self):
        rows=[
            _row(10, 'same-frame.png', confidence=96.0),
            _row(43, 'same-frame.png', confidence=96.0),
        ]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'insufficient_same_phase_corroboration')

    def test_wrong_field_region_is_not_rebound_to_requested_field(self):
        rows=[
            _row(10, 'one.png', field='speed', region_field='wit'),
            _row(43, 'two.png', field='speed', region_field='wit'),
        ]

        result=resolve_candidate_only_gain(rows, 'speed', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'no_source_signed_gain_candidate')

    def test_mismatched_nested_geometry_does_not_correlate_crops(self):
        row=_row(
            10, 'frame.png', field='wit', amount=13, wide='+13:',
            wide_confidence=85.0, wide_box=[0, 0, 100, 100],
        )

        result=resolve_candidate_only_gain([row], 'wit', phase_key='event')

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'insufficient_same_phase_corroboration')


    def test_plan_requests_candidate_only_result_window_without_balance(self):
        rows=[
            _row(150, 'one.png', confidence=96.0),
            _row(183, 'two.png', confidence=95.0),
        ]
        event=dict(id='training-event', kind='training', first_seen_ms=90, last_seen_ms=250)

        requests=plan(rows, [event])

        self.assertEqual(requests, [dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )])

    def test_promote_with_metadata_adds_only_source_corroborated_candidate(self):
        fresh=[
            _row(150, 'one.png', confidence=96.0),
            _row(183, 'two.png', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )

        result, recoveries=promote_with_metadata([], fresh, [window])

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['facts']['training_gains'], {'speed': 3})
        self.assertEqual(result[0]['facts']['observed_training_gain_fields'], ['speed'])
        proof=result[0]['facts']['training_gain_candidate_recovery']['speed']
        self.assertEqual(proof['basis'], 'repeated_source_gain_badge_same_phase')
        self.assertEqual(proof['accepted_amount'], 3)
        self.assertEqual([item['status'] for item in recoveries], ['accepted'])
        self.assertEqual(result[0]['stats'], {})
        self.assertEqual(result[0]['effects'], [])
        self.assertEqual(result[0]['training_option'], 'wit')

    def test_training_events_propagates_accepted_candidate_proof(self):
        fresh=[
            _row(150, 'one.png', option='speed', confidence=96.0),
            _row(183, 'two.png', option='speed', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )

        readings, recoveries=promote_with_metadata([], fresh, [window])
        event=training_events(readings)[0]

        self.assertEqual([item['status'] for item in recoveries], ['accepted'])
        self.assertEqual(event['deltas']['speed'], 3)
        proof=event['direct_gain_provenance']['speed']
        self.assertEqual(proof['basis'], 'candidate_only_training_gain')
        self.assertEqual(proof['candidate_recovery_basis'],
                         'repeated_source_gain_badge_same_phase')
        self.assertEqual(proof['observation_count'], 2)
        self.assertTrue(proof['independent_effect_verification'])

    def test_nested_candidate_proof_propagates_as_one_physical_frame(self):
        fresh=[_row(
            150, 'one.png', field='wit', amount=13, option='wit',
            confidence=97.591, wide='+13:', wide_confidence=84.65,
            wide_box=[448, 930, 660, 1027],
        )]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-0001', fields=['wit'],
            reason='candidate_only_source_gain_evidence',
        )

        readings, _recoveries=promote_with_metadata([], fresh, [window])
        event=training_events(readings)[0]

        self.assertEqual(event['deltas']['wit'], 13)
        proof=event['direct_gain_provenance']['wit']
        self.assertEqual(proof['basis'], 'candidate_only_training_gain')
        self.assertEqual(proof['observation_count'], 2)
        self.assertEqual(proof['evidence'], ['one.png'])

    def test_training_events_rejects_forged_candidate_observation(self):
        fresh=[
            _row(150, 'one.png', option='speed', confidence=96.0),
            _row(183, 'two.png', option='speed', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )
        readings, _recoveries=promote_with_metadata([], fresh, [window])
        proof=readings[0]['facts']['training_gain_candidate_recovery']['speed']
        proof['accepted_observations'][1]['amount']=99

        event=training_events(readings)[0]

        self.assertNotIn('speed', event['deltas'])
        self.assertNotIn('speed', event['direct_gain_provenance'])

    def test_training_events_rejects_conflicting_candidate_observation(self):
        fresh=[
            _row(150, 'one.png', option='speed', confidence=96.0),
            _row(183, 'two.png', option='speed', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )
        readings, _recoveries=promote_with_metadata([], fresh, [window])
        proof=readings[0]['facts']['training_gain_candidate_recovery']['speed']
        conflict=dict(proof['accepted_observations'][0])
        conflict.update(
            amount=99, raw_text='+99', confidence=99.0,
            source_timestamp_ms=216, evidence='conflict.png',
        )
        proof['observations'].append(conflict)

        event=training_events(readings)[0]

        self.assertNotIn('speed', event['deltas'])
        self.assertNotIn('speed', event['direct_gain_provenance'])

    def test_training_events_rejects_nonfinite_candidate_policy(self):
        fresh=[
            _row(150, 'one.png', option='speed', confidence=96.0),
            _row(183, 'two.png', option='speed', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )
        readings, _recoveries=promote_with_metadata([], fresh, [window])
        proof=readings[0]['facts']['training_gain_candidate_recovery']['speed']
        proof['policy']['maximum_span_ms']=float('nan')

        event=training_events(readings)[0]

        self.assertNotIn('speed', event['deltas'])
        self.assertNotIn('speed', event['direct_gain_provenance'])

    def test_training_events_requires_candidate_policy(self):
        fresh=[
            _row(150, 'one.png', option='speed', confidence=96.0),
            _row(183, 'two.png', option='speed', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )
        readings, _recoveries=promote_with_metadata([], fresh, [window])
        proof=readings[0]['facts']['training_gain_candidate_recovery']['speed']
        proof.pop('policy')

        event=training_events(readings)[0]

        self.assertNotIn('speed', event['deltas'])
        self.assertNotIn('speed', event['direct_gain_provenance'])

    def test_training_events_rejects_weakened_candidate_policy(self):
        mutations=[
            ('minimum_tight_confidence', 0),
            ('minimum_broad_confidence', 0),
            ('minimum_repeated_frames', 1),
            ('maximum_span_ms', 999),
            ('maximum_gap_ms', 999),
        ]
        for key, value in mutations:
            with self.subTest(policy_key=key):
                fresh=[
                    _row(150, 'one.png', option='speed', confidence=96.0),
                    _row(183, 'two.png', option='speed', confidence=95.0),
                ]
                window=dict(
                    start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
                    reason='candidate_only_source_gain_evidence',
                )
                readings, _recoveries=promote_with_metadata([], fresh, [window])
                proof=readings[0]['facts']['training_gain_candidate_recovery']['speed']
                proof['policy'][key]=value

                event=training_events(readings)[0]

                self.assertNotIn('speed', event['deltas'])
                self.assertNotIn('speed', event['direct_gain_provenance'])

    def test_training_events_rejects_nonfinite_or_boolean_candidate_box(self):
        for value in (float('nan'), float('inf'), True):
            with self.subTest(box_coordinate=value):
                fresh=[
                    _row(150, 'one.png', option='speed', confidence=96.0),
                    _row(183, 'two.png', option='speed', confidence=95.0),
                ]
                window=dict(
                    start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
                    reason='candidate_only_source_gain_evidence',
                )
                readings, _recoveries=promote_with_metadata([], fresh, [window])
                proof=readings[0]['facts']['training_gain_candidate_recovery']['speed']
                proof['accepted_observations'][0]['box'][0]=value

                event=training_events(readings)[0]

                self.assertNotIn('speed', event['deltas'])
                self.assertNotIn('speed', event['direct_gain_provenance'])

    def test_training_events_rejects_preview_candidate_proof(self):
        fresh=[
            _row(150, 'one.png', option='speed', confidence=96.0),
            _row(183, 'two.png', option='speed', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-0001', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )
        readings, _recoveries=promote_with_metadata([], fresh, [window])
        readings[0]['stats']={'training_preview':True}

        event=training_events(readings)[0]

        self.assertNotIn('speed', event['deltas'])
        self.assertNotIn('speed', event['direct_gain_provenance'])

    def test_promote_matches_exact_evidence_when_timestamp_has_multiple_rows(self):
        readings=[
            _row(150, 'reread-frame.png'),
            _row(150, 'other-frame.png'),
        ]
        fresh=[
            _row(150, 'reread-frame.png', confidence=96.0),
            _row(183, 'second-frame.png', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )

        result, recoveries=promote_with_metadata(readings, fresh, [window])

        enriched=next(row for row in result if row['evidence']=='reread-frame.png')
        untouched=next(row for row in result if row['evidence']=='other-frame.png')
        self.assertEqual(enriched['facts']['training_gains'], {'speed': 3})
        self.assertEqual(untouched['facts']['training_gains'], {})
        self.assertEqual(recoveries[0]['status'], 'accepted')
        self.assertEqual(recoveries[0]['source_identity_match'], 'exact_evidence')

    def test_promote_rejects_ambiguous_existing_timestamp_identity(self):
        readings=[
            _row(150, 'original-a.png'),
            _row(150, 'original-b.png'),
        ]
        fresh=[
            _row(150, 'reread-a.png', confidence=96.0),
            _row(183, 'reread-b.png', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )

        result, recoveries=promote_with_metadata(readings, fresh, [window])

        self.assertEqual(result, readings)
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0]['status'], 'unresolved_ambiguous_source_identity')
        self.assertEqual(recoveries[0]['source_identity'], 'ambiguous_timestamp')
        self.assertEqual(recoveries[0]['source_timestamp_ms'], 150)

    def test_promote_rejects_duplicate_windows(self):
        fresh=[
            _row(150, 'one.png', confidence=96.0),
            _row(183, 'two.png', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )

        self.assertEqual(promote([], fresh, [window, window]), [])

    def test_promote_does_not_use_result_total_as_candidate(self):
        fresh=[_row(150, 'result.png', amount=13, result_only=True)]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )

        self.assertEqual(promote([], fresh, [window]), [])

    def test_promote_does_not_cross_option_phase_boundary(self):
        fresh=[
            _row(150, 'wit.png', option='wit', confidence=96.0),
            _row(183, 'speed.png', option='speed', confidence=95.0),
        ]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence',
        )

        result, recoveries=promote_with_metadata([], fresh, [window])

        self.assertEqual(result, [])
        self.assertEqual(recoveries, [])

    def test_recover_persists_candidate_proof_and_policy(self):
        readings=[
            _row(150, 'one.png', confidence=96.0),
            _row(183, 'two.png', confidence=95.0),
        ]
        events=[dict(id='training-event', kind='training', first_seen_ms=90, last_seen_ms=250)]
        window=dict(
            start_ms=90, end_ms=250, owner_id='training-event', fields=['speed'],
            reason='candidate_only_source_gain_evidence', fps=60,
        )
        fresh=[
            _row(150, 'one.png', confidence=96.0),
            _row(183, 'two.png', confidence=95.0),
        ]

        original=__import__('copy').deepcopy(readings)
        with workspace_temp() as root:
            directory=root/'training-gain-recovery'
            directory.mkdir()
            (directory/'receipt-inspection.json').write_text(
                json.dumps({'readings': [], 'windows': [window]}), encoding='utf-8',
            )
            with patch('tracen_replay.inspect_receipts.inspect') as inspect, \
                 patch('tracen_replay.inspect_training.reparse_inspection', return_value=fresh):
                result, metadata=recover(
                    root/'source.mp4', root, {'sha256': 'source'}, readings, events,
                )

        recovered=next(row for row in result if row['source_timestamp_ms']==150)
        self.assertEqual(recovered['facts']['training_gains'], {'speed': 3})
        self.assertEqual(readings, original)
        self.assertEqual(metadata['candidate_only_recovery_count'], 1)
        self.assertEqual(metadata['candidate_only_recoveries'][0]['basis'],
                         'repeated_source_gain_badge_same_phase')
        self.assertEqual(metadata['candidate_only_recoveries'][0]['source_identity_match'],
                         'unique_timestamp')
        self.assertEqual(metadata['candidate_only_recoveries'][0]['row_update'],
                         'enriched_existing_source_row')
        self.assertEqual(metadata['candidate_only_recovery_policy']['uses_balance_arithmetic'], False)
        inspect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
