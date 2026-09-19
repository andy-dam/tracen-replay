import copy
import unittest

from tracen_replay.boundary_state_recovery import (
    CHANNEL_FIELDS,
    boundary_identity,
    candidate_windows,
    endpoint_candidates,
    apply_opening_endpoint_projections,
    promote,
    promote_existing_endpoints,
    turn_boundary_identity,
)


STATS = dict(speed=319, stamina=251, power=361, guts=225, wit=444, skill_points=554)
PERFORMANCE = dict(dance=9, passion=10, vocal=4, visual=20, composure=105)


def row(time, *, calendar='Classic Year Early Mar', stats=None, performance=None,
        screen='unknown', source_sha256=None):
    payload = dict(
        source_timestamp_ms=time,
        evidence=f'gameplay/frame-{time}.png',
        screen=screen,
        stats={'values': copy.deepcopy(stats)} if stats is not None else {},
        facts={'performance_points': copy.deepcopy(performance)} if performance is not None else {},
    )
    if calendar is not None:
        payload['stats']['calendar_text'] = calendar
    if calendar == 'Finale Underway':
        payload['stats']['turns_remaining_to_goal'] = 1
    if source_sha256 is not None:
        payload['source_sha256'] = source_sha256
    return payload


def report_for(turn, *, action_time=250, source_sha256='source'):
    return {
        'source': {'duration_ms': 1000, 'sha256': source_sha256},
        'turn_ledger': {
            'turns': [turn],
            'timeline': [{'kind': 'committed_action', 'turn_id': turn['id'],
                          'first_seen_ms': action_time}],
        },
    }


def attach_state_proof(report, readings, index, channel, *, values=None,
                       source_observations=None, **observation_updates):
    """Attach the producer-shaped same-frame acceptance proof used by promotion."""
    reading = readings[index]
    if values is None:
        values = (reading.get('stats', {}).get('values') if channel == 'stats'
                  else reading.get('facts', {}).get('performance_points'))
    value_source = 'stats.values' if channel == 'stats' else 'facts.performance_points'
    source_ref = f'/gameplay_tracking/readings/{index}'
    if source_observations is None:
        source_observations = [dict(source_ref=source_ref,
                                    source_timestamp_ms=reading['source_timestamp_ms'],
                                    evidence=reading['evidence'],
                                    values=copy.deepcopy(values),
                                    value_source=value_source)]
    payload = dict(kind='state', channel=channel, values=copy.deepcopy(values))
    calendar = reading.get('stats', {}).get('calendar_text')
    if calendar:
        payload['calendar_text'] = calendar
    proof = dict(
        id=f'/gameplay_tracking/state_observations/{index}-{channel}',
        source_ref=f'/gameplay_tracking/state_observations/{index}-{channel}',
        category='state', phase='observed', payload=payload,
        start_ms=reading['source_timestamp_ms'], end_ms=reading['source_timestamp_ms'],
        evidence=[reading['evidence']], source_observations=source_observations,
        uncertain=False, cross_frame_values_merged=False,
        reconciliation_endpoint_inferred=False, observation_basis=value_source,
    )
    proof.update(observation_updates)
    report.setdefault('gameplay_tracking', {})['state_observations'] = [proof]
    return report


def missing_states():
    return {
        channel: {'opening': None, 'opening_status': 'not_observed_before_action'}
        for channel in CHANNEL_FIELDS
    }


class BoundaryStateRecoveryTests(unittest.TestCase):
    def test_candidate_requires_same_explicit_calendar_boundary(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        readings = [
            # This complete panel is readable but belongs to another date.
            row(145, calendar='Classic Year Late Mar', stats=STATS),
            # This panel has the right date but is explicitly from another run.
            row(150, stats=STATS, source_sha256='other-source'),
            row(160, stats=STATS),
        ]

        windows = candidate_windows(report, readings)

        self.assertEqual(len(windows), 1)
        channel = windows[0]['channels'][0]
        self.assertEqual(channel['channel'], 'stats')
        self.assertEqual(channel['candidate_source_timestamp_ms'], 160)
        self.assertEqual(channel['candidate_boundary_identity'], ['dated', 28])
        self.assertEqual(windows[0]['boundary_identity'], ['dated', 28])
        self.assertEqual(windows[0]['ownership_basis'],
                         'same_calendar_boundary_before_action')
        projection = endpoint_candidates(report, readings)
        self.assertEqual(projection[0]['source_ref'],
                         '/gameplay_tracking/readings/2/stats/values')
        self.assertEqual(projection[0]['values'], STATS)
        self.assertFalse(projection[0]['merged_from_multiple_frames'])

    def test_rejected_action_context_without_boundary_proof_is_not_an_owner(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        rejected = row(160, calendar=None, stats=STATS,
                       screen='event_outcome')
        rejected['facts']['rejected_counts'] = {'missing_training_header_or_option': 1}

        self.assertEqual(candidate_windows(report, [rejected]), [])
        self.assertIsNone(boundary_identity(rejected))

    def test_unnumbered_phase_does_not_establish_a_unique_turn_owner(self):
        turn = dict(id='pre-debut', phase='Junior Year Pre-Debut',
                    calendar_value=None, start_ms=100, end_ms=400,
                    states=missing_states())
        reading = row(160, calendar='Junior Year Pre-Debut', stats=STATS)

        self.assertIsNone(turn_boundary_identity(turn))
        self.assertEqual(candidate_windows(report_for(turn), [reading]), [])

    def test_promote_keeps_partial_and_complete_source_samples_separate(self):
        source_sha256 = 'source'
        expected = ['Finale Underway', 1]
        window = {
            'id': 'boundary-turn-073-100',
            'owner_turn_id': 'turn-073',
            'owner_start_ms': 100,
            'owner_action_time_ms': 300,
            'start_ms': 100,
            'end_ms': 300,
            'source_sha256': source_sha256,
            'channels': [{
                'channel': 'performance',
                'requested_fields': list(CHANNEL_FIELDS['performance']),
                'candidate_source_timestamp_ms': 100,
                'candidate_evidence': ['gameplay/frame-100.png'],
                'candidate_boundary_identity': expected,
                'candidate_readability': 5,
                'ownership_basis': 'same_calendar_boundary_before_action',
            }],
        }
        complete = row(120, calendar='Finale Underway', performance=PERFORMANCE,
                       source_sha256=source_sha256)
        partial_values = dict(PERFORMANCE)
        partial_values.pop('dance')
        partial = row(140, calendar='Finale Underway', performance=partial_values,
                      source_sha256=source_sha256)
        unrelated = row(160, calendar='Finale Underway',
                         performance=dict(PERFORMANCE, dance=77),
                         source_sha256='another-source')
        missing_boundary = row(180, calendar=None, performance=PERFORMANCE,
                               source_sha256=source_sha256)

        promoted = promote([], [complete, partial, unrelated, missing_boundary], [window])

        self.assertEqual([r['source_timestamp_ms'] for r in promoted], [120, 140])
        self.assertEqual(promoted[0]['facts']['performance_points'], PERFORMANCE)
        self.assertEqual(promoted[1]['facts']['performance_points'], partial_values)
        self.assertNotIn('dance', promoted[1]['facts']['performance_points'])
        for sample in promoted:
            proof = sample['facts']['boundary_state_recovery'][0]
            self.assertEqual(proof['candidate_boundary_identity'], expected)
            self.assertEqual(proof['source_sha256'], source_sha256)

    def test_real_change_between_same_boundary_samples_is_never_merged(self):
        expected = ['Finale Underway', 1]
        window = {
            'id': 'boundary-turn-073-100',
            'owner_turn_id': 'turn-073',
            'owner_start_ms': 100,
            'owner_action_time_ms': 300,
            'start_ms': 100,
            'end_ms': 300,
            'source_sha256': 'source',
            'channels': [{
                'channel': 'performance',
                'requested_fields': list(CHANNEL_FIELDS['performance']),
                'candidate_source_timestamp_ms': 100,
                'candidate_evidence': ['gameplay/frame-100.png'],
                'candidate_boundary_identity': expected,
                'candidate_readability': 5,
            }],
        }
        first = row(120, calendar='Finale Underway', performance=PERFORMANCE,
                    source_sha256='source')
        changed = row(140, calendar='Finale Underway',
                      performance=dict(PERFORMANCE, dance=10), source_sha256='source')

        promoted = promote([], [first, changed], [window])

        self.assertEqual([r['facts']['performance_points']['dance'] for r in promoted],
                         [9, 10])
        self.assertNotEqual(promoted[0]['facts']['performance_points'],
                            promoted[1]['facts']['performance_points'])

    def test_terminal_closing_remains_unknown_when_only_opening_is_recovered(self):
        from tracen_replay.causal_accounting import CHANNELS, build

        gameplay = {
            'auxiliary_log_used': False,
            'readings': [row(100, calendar='Finale Underway',
                             stats=STATS, performance=PERFORMANCE)],
            'events': [], 'checkpoints': [], 'intervals': [],
            'performance_accounting': {'checkpoints': [], 'intervals': []},
            'lesson_purchases': [], 'song_acquisitions': [],
            'turn_action_receipts': [],
        }
        states = {}
        for channel, values in CHANNELS.items():
            values = copy.deepcopy(STATS if channel == 'stats' else PERFORMANCE)
            source = '/gameplay_tracking/readings/0/stats/values' \
                if channel == 'stats' else '/gameplay_tracking/readings/0/facts/performance_points'
            states[channel] = {
                'opening': {'values_ref': source, 'values': values.copy(),
                            'observed_at_ms': 100},
                'closing': None,
            }
        report = {
            'source': {'sha256': 'source'},
            'gameplay_tracking': gameplay,
            'turn_ledger': {'turns': [dict(id='turn-073', start_ms=100, states=states)]},
        }

        accounting = build(report)

        # The one turn is the last: its end is the career's end, not a gap.
        self.assertTrue(all(field['status'] == 'career_end'
                            for transition in accounting['turn_transitions']
                            for field in transition['fields']))
        self.assertTrue(all(transition['after_state_ref'] is None
                            for transition in accounting['turn_transitions']))
        self.assertTrue(all(transition['terminal_observation'] is None
                            for transition in accounting['turn_transitions']))

    def test_single_source_bound_complete_reading_projects_opening_without_repetition(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        reading = row(160, stats=STATS)
        attach_state_proof(report, [reading], 0, 'stats')

        projections = promote_existing_endpoints(report, [reading])
        self.assertEqual(len(projections), 1)
        self.assertTrue(projections[0]['complete'])
        self.assertFalse(projections[0]['merged_from_multiple_frames'])
        ledger, accepted, rejected = apply_opening_endpoint_projections(
            report['turn_ledger'], projections, source_sha256='source')

        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        opening = ledger['turns'][0]['states']['stats']['opening']
        self.assertEqual(opening['values'], STATS)
        self.assertEqual(opening['values_ref'],
                         '/gameplay_tracking/readings/0/stats/values')
        self.assertEqual(opening['basis'], 'source_bound_single_frame_before_action')

    def test_direct_finale_projection_keeps_terminal_closing_unknown(self):
        turn = dict(id='turn-073', phase='Finale Underway', calendar_value=1,
                    start_ms=100, end_ms=400, states=missing_states())
        turn['states']['performance']['opening'] = {
            'values': {field: PERFORMANCE[field] for field in PERFORMANCE if field != 'dance'},
            'opening_status': 'partially_observed',
        }
        report = report_for(turn)
        reading = row(160, calendar='Finale Underway', performance=PERFORMANCE)
        attach_state_proof(report, [reading], 0, 'performance')

        projections = promote_existing_endpoints(report, [reading])
        performance = [item for item in projections if item['channel'] == 'performance']
        self.assertEqual(len(performance), 1)
        ledger, accepted, rejected = apply_opening_endpoint_projections(
            report['turn_ledger'], performance, source_sha256='source')

        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        state = ledger['turns'][0]['states']['performance']
        self.assertEqual(state['opening']['values'], PERFORMANCE)
        self.assertEqual(state['opening_status'], 'observed')
        self.assertIsNone(state.get('closing'))

    def test_partial_opening_with_conflicting_overlap_is_rejected(self):
        turn = dict(id='turn-073', phase='Finale Underway', calendar_value=1,
                    start_ms=100, end_ms=400, states=missing_states())
        turn['states']['performance']['opening'] = {
            'values': dict(PERFORMANCE, passion=99),
            'opening_status': 'partially_observed',
        }
        report = report_for(turn)
        reading = row(160, calendar='Finale Underway', performance=PERFORMANCE)
        attach_state_proof(report, [reading], 0, 'performance')
        projections = promote_existing_endpoints(report, [reading])
        performance = [item for item in projections if item['channel'] == 'performance']

        ledger, accepted, rejected = apply_opening_endpoint_projections(
            report['turn_ledger'], performance, source_sha256='source')

        self.assertEqual(accepted, [])
        self.assertEqual(rejected[0]['reason'], 'conflicting_overlapping_opening')
        self.assertEqual(ledger['turns'][0]['states']['performance']['opening']['values']['passion'], 99)

    def test_new_nonterminal_opening_repairs_predecessor_unavailable_closing(self):
        target = dict(id='turn-029', phase='dated', calendar_value=28,
                      start_ms=100, end_ms=400, states=missing_states())
        report = report_for(target)
        reading = row(160, stats=STATS)
        attach_state_proof(report, [reading], 0, 'stats')
        projections = promote_existing_endpoints(report, [reading])
        previous = dict(id='turn-028', phase='dated', calendar_value=27,
                        start_ms=0, end_ms=100,
                        states={channel: {'opening': None, 'closing': None,
                                          'closing_basis': 'unavailable'}
                                for channel in CHANNEL_FIELDS})
        ledger_input = {'turns': [previous, copy.deepcopy(target)]}

        ledger, accepted, rejected = apply_opening_endpoint_projections(
            ledger_input, projections, source_sha256='source')

        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        closing = ledger['turns'][0]['states']['stats']['closing']
        self.assertEqual(closing['values'], STATS)
        self.assertEqual(closing['values_ref'],
                         '/gameplay_tracking/readings/0/stats/values')
        self.assertEqual(ledger['turns'][0]['states']['stats']['closing_basis'],
                         'next_turn_first_observed_state')

    def test_direct_singleton_requires_accepted_same_frame_source_proof(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        reading = row(160, stats=STATS)

        # Calendar text and integer values remain useful discovery evidence,
        # but cannot promote a singleton without the accepted observation.
        self.assertEqual(promote_existing_endpoints(report, [reading]), [])

        attach_state_proof(report, [reading], 0, 'stats')
        projections = promote_existing_endpoints(report, [reading])
        self.assertEqual(len(projections), 1)

    def test_direct_singleton_rejects_conflicting_field_proof(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        reading = row(160, stats=STATS)
        attach_state_proof(report, [reading], 0, 'stats',
                           values=dict(STATS, speed=320))

        self.assertEqual(promote_existing_endpoints(report, [reading]), [])

    def test_direct_singleton_rejects_ambiguous_timestamp(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        readings = [row(160, stats=STATS), row(160, stats=STATS)]
        attach_state_proof(report, readings, 0, 'stats')
        duplicate_proof = copy.deepcopy(report['gameplay_tracking']['state_observations'][0])
        duplicate_proof['id'] = duplicate_proof['source_ref'] = \
            '/gameplay_tracking/state_observations/1-stats'
        duplicate_proof['source_observations'][0]['source_ref'] = \
            '/gameplay_tracking/readings/1'
        report['gameplay_tracking']['state_observations'].append(duplicate_proof)

        self.assertEqual(promote_existing_endpoints(report, readings), [])

    def test_direct_singleton_rejects_temporally_crossing_source_proof(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        reading = row(160, stats=STATS)
        later_member = dict(source_ref='/gameplay_tracking/readings/1',
                             source_timestamp_ms=260, evidence='later.png',
                             values=copy.deepcopy(STATS), value_source='stats.values')
        attach_state_proof(report, [reading], 0, 'stats',
                           source_observations=[
                               dict(source_ref='/gameplay_tracking/readings/0',
                                    source_timestamp_ms=160,
                                    evidence=reading['evidence'],
                                    values=copy.deepcopy(STATS),
                                    value_source='stats.values'),
                               later_member])

        self.assertEqual(promote_existing_endpoints(report, [reading]), [])

    def test_direct_singleton_rejects_fabricated_source_member(self):
        turn = dict(id='turn-029', phase='dated', calendar_value=28,
                    start_ms=100, end_ms=400, states=missing_states())
        report = report_for(turn)
        reading = row(160, stats=STATS)
        fabricated_member = dict(source_ref='/gameplay_tracking/readings/99',
                                 source_timestamp_ms=170, evidence='later.png',
                                 values=copy.deepcopy(STATS), value_source='stats.values')
        attach_state_proof(report, [reading], 0, 'stats',
                           source_observations=[
                               dict(source_ref='/gameplay_tracking/readings/0',
                                    source_timestamp_ms=160,
                                    evidence=reading['evidence'],
                                    values=copy.deepcopy(STATS),
                                    value_source='stats.values'),
                               fabricated_member])

        self.assertEqual(promote_existing_endpoints(report, [reading]), [])

    def test_direct_singleton_rejects_uncertain_or_merged_source_proof(self):
        for field in ('uncertain', 'cross_frame_values_merged',
                      'reconciliation_endpoint_inferred'):
            with self.subTest(field=field):
                turn = dict(id='turn-029', phase='dated', calendar_value=28,
                            start_ms=100, end_ms=400, states=missing_states())
                report = report_for(turn)
                reading = row(160, stats=STATS)
                attach_state_proof(report, [reading], 0, 'stats', **{field: True})
                self.assertEqual(promote_existing_endpoints(report, [reading]), [])


if __name__ == '__main__':
    unittest.main()
