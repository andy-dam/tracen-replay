import copy
import unittest

from tracen_replay.transactions import training_events
from tracen_replay.training_gain_recovery import plan, promote_with_metadata
from tracen_replay.full_recording import (
    PipelineError,
    _coalesce_replay_training_promotion_windows,
    _validate_training_replay_windows,
)


def _result_row(
    timestamp,
    evidence,
    *,
    option='speed',
    gains=None,
    awards=None,
    result_values=None,
    outcome='success',
    preview=False,
    preview_overlay=False,
):
    return {
        'source_timestamp_ms': timestamp,
        'evidence': evidence,
        'screen': 'training_result',
        'training_option': option,
        'stats': {'training_preview': preview},
        'facts': {
            'training_outcome': outcome,
            'preview_overlay_proven': preview_overlay,
            'training_gains': dict(gains or {}),
            'awarded_performance_gains': dict(awards or {}),
            'result_values': dict(result_values or {}),
        },
        'effects': [],
        'ocr': {'neural': []},
    }


class TrainingGainG08ResultProjectionTests(unittest.TestCase):
    def test_replay_validator_preserves_and_validates_result_projection_scope(self):
        value = _validate_training_replay_windows([{
            'start_ms': 500,
            'end_ms': 1500,
            'owner_id': 'training-g08',
            'fields': ['speed'],
            'performance_fields': ['visual', 'dance'],
            'training_option': 'speed',
            'source_result_projection': True,
        }], 'candidate plan')
        self.assertEqual(value[0]['performance_fields'], ['dance', 'visual'])
        self.assertEqual(value[0]['training_option'], 'speed')
        self.assertTrue(value[0]['source_result_projection'])

        invalid = []
        item = {
                'start_ms': 500,
                'end_ms': 1500,
                'owner_id': 'training-g08',
                'fields': ['speed'],
                'performance_fields': ['visual'],
                'training_option': 'speed',
                'source_result_projection': True,
            }
        missing_performance = dict(item)
        missing_performance.pop('performance_fields')
        invalid.append(('missing performance fields', missing_performance))
        missing_option = dict(item)
        missing_option.pop('training_option')
        invalid.append(('missing training option', missing_option))
        invalid.append(('unknown performance field', dict(item, performance_fields=['balance'])))
        invalid.append(('unknown training option', dict(item, training_option='Skill Points')))
        invalid.append(('nonboolean projection', dict(item, source_result_projection='true')))
        for name, candidate in invalid:
            with self.subTest(name=name):
                with self.assertRaises(PipelineError):
                    _validate_training_replay_windows([candidate], 'candidate plan')

    def test_committed_result_without_candidates_gets_bounded_request(self):
        base = _result_row(
            1000,
            'gameplay/result.png',
            result_values={'speed': 334, 'skill_points': 299},
        )
        event = {
            'id': 'training-g08',
            'kind': 'training',
            'training_option': 'speed',
            'first_seen_ms': 1000,
            'last_seen_ms': 1000,
        }

        self.assertEqual(
            plan([base], [event]),
            [{
                'start_ms': 500,
                'end_ms': 1500,
                'owner_id': 'training-g08',
                'fields': ['guts', 'power', 'skill_points', 'speed', 'stamina', 'wit'],
                'performance_fields': ['dance', 'passion', 'vocal', 'visual', 'composure'],
                'training_option': 'speed',
                'source_result_projection': True,
                'reason': 'committed_result_missing_signed_gain_observation',
            }],
        )

    def test_known_candidate_window_precedes_generic_result_fallback(self):
        candidate = _result_row(
            2000,
            'candidate.png',
            option='wit',
            result_values={'wit': 320},
        )
        candidate['facts']['training_gain_crop_provenance'] = {
            'wit': {'candidate_amounts': [13]},
        }
        rows = [
            _result_row(1000, 'fallback.png', result_values={'speed': 334}),
            candidate,
        ]
        events = [
            {
                'id': 'fallback', 'kind': 'training', 'training_option': 'speed',
                'first_seen_ms': 1000, 'last_seen_ms': 1000,
            },
            {
                'id': 'candidate', 'kind': 'training', 'training_option': 'wit',
                'first_seen_ms': 1900, 'last_seen_ms': 2100,
            },
        ]

        requests = plan(rows, events)

        self.assertEqual(requests[0]['owner_id'], 'candidate')
        self.assertEqual(requests[0]['reason'], 'candidate_only_source_gain_evidence')
        self.assertTrue(requests[1]['source_result_projection'])

    def test_partial_signed_result_does_not_treat_absent_fields_as_missing_effects(self):
        # Speed training visibly reports these three signed gains.  The
        # absence of stamina/guts/wit badges is normal for this result and is
        # not a request for another source pass.
        row = _result_row(
            1000,
            'partial-result.png',
            gains={'speed': 20, 'power': 7, 'skill_points': 11},
            awards={'dance': 13},
            result_values={'speed': 334, 'skill_points': 299},
        )
        event = {
            'id': 'training-partial',
            'kind': 'training',
            'training_option': 'speed',
            'first_seen_ms': 1000,
            'last_seen_ms': 1000,
        }
        self.assertEqual(plan([row], [event]), [])

    def test_two_and_three_field_signed_results_do_not_get_generic_fallback(self):
        event = {
            'id': 'training-partial',
            'kind': 'training',
            'training_option': 'speed',
            'first_seen_ms': 1000,
            'last_seen_ms': 1000,
        }
        for gains in (
            {'speed': 20, 'skill_points': 11},
            {'speed': 20, 'power': 7, 'skill_points': 11},
        ):
            with self.subTest(fields=tuple(gains)):
                row = _result_row(
                    1000,
                    f"partial-{len(gains)}-fields.png",
                    gains=gains,
                    result_values={'speed': 334},
                )
                self.assertEqual(plan([row], [event]), [])

    def test_noncanonical_signed_fact_does_not_suppress_generic_fallback(self):
        row = _result_row(
            1000,
            'unknown-field.png',
            gains={'unrelated': 13},
            result_values={'speed': 334},
        )
        event = {
            'id': 'training-unknown-field',
            'kind': 'training',
            'training_option': 'speed',
            'first_seen_ms': 1000,
            'last_seen_ms': 1000,
        }
        requests = plan([row], [event])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['fields'], [
            'guts', 'power', 'skill_points', 'speed', 'stamina', 'wit',
        ])

    def test_replay_coalesce_preserves_result_projection_scope(self):
        generic = {
            'start_ms': 324000,
            'end_ms': 325000,
            'owner_id': 'training-0012',
            'fields': ['speed'],
            'performance_fields': ['visual'],
            'training_option': 'speed',
            'source_result_projection': True,
        }
        narrow = {
            'start_ms': 324150,
            'end_ms': 324550,
            'owner_id': 'training-0012',
            'fields': ['power', 'skill_points'],
            'reason': 'candidate_only_source_gain_evidence',
        }
        result = _coalesce_replay_training_promotion_windows([generic, narrow])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['fields'], ['power', 'skill_points', 'speed'])
        self.assertEqual(result[0]['training_option'], 'speed')
        self.assertEqual(result[0]['performance_fields'], ['visual'])
        self.assertTrue(result[0]['source_result_projection'])

    def test_replay_coalesce_does_not_merge_conflicting_options(self):
        windows = [
            {
                'start_ms': 100,
                'end_ms': 300,
                'owner_id': 'same-id',
                'fields': ['speed'],
                'training_option': 'speed',
            },
            {
                'start_ms': 200,
                'end_ms': 400,
                'owner_id': 'same-id',
                'fields': ['power'],
                'training_option': 'power',
            },
        ]
        result = _coalesce_replay_training_promotion_windows(windows)
        self.assertEqual(len(result), 2)

    def test_partial_signed_result_only_reopens_explicit_candidate_field(self):
        row = _result_row(
            1000,
            'partial-candidate.png',
            gains={'speed': 20, 'power': 7, 'skill_points': 11},
            result_values={'speed': 334},
        )
        row['facts']['training_gain_crop_provenance'] = {
            'speed': {'canonical_amount': 20},
            'wit': {'candidate_amounts': [13]},
        }
        event = {
            'id': 'training-partial-candidate',
            'kind': 'training',
            'training_option': 'speed',
            'first_seen_ms': 900,
            'last_seen_ms': 1100,
        }
        requests = plan([row], [event])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['fields'], ['wit'])
        self.assertEqual(requests[0]['reason'], 'candidate_only_source_gain_evidence')

    def test_candidate_recovery_window_carries_selected_training_option(self):
        row = _result_row(
            1000,
            'candidate-speed.png',
            option='speed',
            result_values={'speed': 334},
        )
        row['facts']['training_gain_crop_provenance'] = {
            'power': {'candidate_amounts': [7]},
        }
        event = {
            'id': 'training-speed-owner',
            'kind': 'training',
            'training_option': 'speed',
            'first_seen_ms': 900,
            'last_seen_ms': 1100,
        }
        request = plan([row], [event])[0]
        self.assertEqual(request['training_option'], 'speed')

    def test_training_gain_and_performance_resolution_rejects_preview_or_failure_rows(self):
        cases = [
            _result_row(
                1000,
                'preview-gain.png',
                gains={'speed': 20},
                awards={'dance': 13},
                preview=True,
            ),
            _result_row(
                1000,
                'preview-overlay-gain.png',
                gains={'speed': 20},
                awards={'dance': 13},
                preview_overlay=True,
            ),
            _result_row(
                1000,
                'failure-gain.png',
                gains={'speed': 10},
                awards={'dance': 10},
                outcome='failure',
            ),
        ]
        modifier_preview = _result_row(
            1000,
            'preview-modifier-gain.png',
            gains={'speed': 20},
            awards={'dance': 13},
        )
        modifier_preview['facts']['preview_modifier_proven'] = True
        cases.append(modifier_preview)
        failure_banner = _result_row(
            1000,
            'failure-banner-gain.png',
            gains={'speed': 10},
            awards={'dance': 10},
        )
        failure_banner['facts']['failure_banner'] = True
        cases.append(failure_banner)
        for row in cases:
            with self.subTest(evidence=row['evidence']):
                event = training_events([row])[0]
                self.assertEqual(event['deltas'], {})
                self.assertEqual(event['performance_deltas'], {})
        failed = training_events([cases[2]])[0]
        self.assertEqual(failed['failure_evidence'], ['failure-gain.png'])

    def test_candidate_promotion_rejects_foreign_training_option(self):
        # The source proof is otherwise a valid candidate pair, but the
        # speed-owned window must not consume a power result from a neighboring
        # action.
        from tests.test_training_gain_candidate_recovery import _row

        fresh = [
            _row(950, 'foreign-1.png', option='power', confidence=96.0),
            _row(983, 'foreign-2.png', option='power', confidence=95.0),
        ]
        window = {
            'start_ms': 900,
            'end_ms': 1050,
            'owner_id': 'training-speed-owner',
            'fields': ['speed'],
            'training_option': 'speed',
            'reason': 'candidate_only_source_gain_evidence',
        }
        promoted, recoveries = promote_with_metadata([], fresh, [window])
        self.assertEqual(promoted, [])
        self.assertEqual(recoveries, [])

    def test_generic_candidate_window_cannot_promote_foreign_raw_gain(self):
        foreign = _result_row(
            950,
            'foreign-raw-gain.png',
            option='power',
            gains={'speed': 20},
        )
        window = {
            'start_ms': 900,
            'end_ms': 1050,
            'owner_id': 'training-speed-owner',
            'fields': ['speed'],
            'training_option': 'speed',
            'reason': 'candidate_only_source_gain_evidence',
        }
        promoted, recoveries = promote_with_metadata([], [foreign], [window])
        self.assertEqual(promoted, [])
        self.assertEqual(recoveries, [])

    def test_optionless_gain_uses_unique_committed_owner_context(self):
        owner = _result_row(
            1000,
            'owner-wit.png',
            option='wit',
            gains={'wit': 21},
        )
        reread = _result_row(
            1033,
            'missing-option.png',
            option=None,
            gains={'skill_points': 10},
        )
        window = {
            'start_ms': 1000,
            'end_ms': 1100,
            'owner_id': 'training-wit-owner',
            'fields': ['skill_points'],
            'training_option': 'wit',
            'reason': 'candidate_only_source_gain_evidence',
        }
        promoted, recoveries = promote_with_metadata([owner], [reread], [window])
        self.assertEqual(
            next(row for row in promoted if row['source_timestamp_ms'] == 1033)
            ['facts']['training_gains']['skill_points'],
            10,
        )
        self.assertEqual(recoveries, [])

    def test_optionless_gain_without_source_owner_context_is_rejected(self):
        reread = _result_row(
            1033,
            'missing-option-no-owner.png',
            option=None,
            gains={'skill_points': 10},
        )
        window = {
            'start_ms': 1000,
            'end_ms': 1100,
            'owner_id': 'training-wit-owner',
            'fields': ['skill_points'],
            'training_option': 'wit',
            'reason': 'candidate_only_source_gain_evidence',
        }
        promoted, recoveries = promote_with_metadata([], [reread], [window])
        self.assertEqual(promoted, [])
        self.assertEqual(recoveries, [])

    def test_optionless_gain_does_not_cross_explicit_foreign_owner(self):
        owner = _result_row(1000, 'owner-wit.png', option='wit')
        optionless = _result_row(
            1033,
            'missing-option-with-foreign.png',
            option=None,
            gains={'skill_points': 10},
        )
        foreign = _result_row(
            1066,
            'foreign-power.png',
            option='power',
            gains={'skill_points': 10},
        )
        window = {
            'start_ms': 1000,
            'end_ms': 1100,
            'owner_id': 'training-wit-owner',
            'fields': ['skill_points'],
            'training_option': 'wit',
            'reason': 'candidate_only_source_gain_evidence',
        }
        promoted, recoveries = promote_with_metadata(
            [owner], [optionless, foreign], [window],
        )
        self.assertEqual([row['source_timestamp_ms'] for row in promoted], [1000])
        self.assertEqual(recoveries, [])

    def test_preview_failure_and_totals_only_never_schedule_or_promote(self):
        cases = [
            _result_row(1000, 'preview.png', result_values={'speed': 334}, preview=True),
            _result_row(1000, 'failure.png', result_values={'speed': 334}, outcome='failure'),
            _result_row(1000, 'totals-only.png', result_values={'speed': 334}),
        ]
        # The first two are not committed result owners.  The last one is a
        # valid owner for scheduling, but a reread containing only totals must
        # not turn a balance into an inferred gain.
        events = [{
            'id': 'training', 'kind': 'training', 'training_option': 'speed',
            'first_seen_ms': 1000, 'last_seen_ms': 1000,
        }]
        self.assertEqual(plan(cases[:1], events), [])
        self.assertEqual(plan(cases[1:2], events), [])
        request = plan(cases[2:], events)
        self.assertEqual(len(request), 1)
        fresh = copy.deepcopy(cases[2:])
        promoted, recoveries = promote_with_metadata(cases[2:], fresh, request)
        self.assertEqual(promoted, cases[2:])
        self.assertEqual(recoveries, [])

    def test_projects_only_direct_result_fields_with_option_and_provenance(self):
        base = [_result_row(
            1000,
            'base-result.png',
            result_values={'speed': 334, 'skill_points': 299},
        )]
        fresh = [
            _result_row(
                900,
                'reread-1.png',
                gains={'speed': 20, 'power': 7, 'skill_points': 11},
                awards={'dance': 13, 'visual': 13},
                result_values={'speed': 334},
            ),
            _result_row(
                933,
                'reread-2.png',
                gains={'speed': 20, 'power': 7, 'skill_points': 11},
                awards={'dance': 13, 'visual': 13},
                result_values={'speed': 334},
            ),
        ]
        window = {
            'start_ms': 500,
            'end_ms': 1500,
            'owner_id': 'training-g08',
            'fields': ['guts', 'power', 'skill_points', 'speed', 'stamina', 'wit'],
            'performance_fields': ['dance', 'passion', 'vocal', 'visual', 'composure'],
            'training_option': 'speed',
            'source_result_projection': True,
            'reason': 'committed_result_missing_signed_gain_observation',
        }

        merged, recoveries = promote_with_metadata(base, fresh, [window])

        additions = [row for row in merged if row['source_timestamp_ms'] in (900, 933)]
        self.assertEqual(len(additions), 2)
        self.assertEqual(additions[0]['training_option'], 'speed')
        self.assertEqual(
            additions[0]['facts']['training_gains'],
            {'speed': 20, 'power': 7, 'skill_points': 11},
        )
        self.assertEqual(
            additions[0]['facts']['awarded_performance_gains'],
            {'dance': 13, 'visual': 13},
        )
        recovery = additions[0]['facts']['training_gain_recovery']
        self.assertEqual(recovery['owner_id'], 'training-g08')
        self.assertEqual(recovery['training_option'], 'speed')
        self.assertEqual(recovery['projection_mode'], 'committed_result_direct_fields')
        self.assertNotIn('result_values', additions[0]['facts'])
        self.assertEqual(recoveries, [])

        event = training_events(merged)[0]
        self.assertEqual(
            event['deltas'], {'speed': 20, 'power': 7, 'skill_points': 11},
        )
        self.assertEqual(
            event['performance_deltas'], {'dance': 13, 'visual': 13},
        )

    def test_one_source_result_frame_is_accepted_only_with_projection_proof(self):
        base = [_result_row(
            1000,
            'base-result.png',
            result_values={'speed': 334},
        )]
        source = _result_row(
            900,
            'reread-result.png',
            gains={'speed': 20},
            result_values={'speed': 334},
        )
        source.update(
            source_sha256='a' * 64,
            source_frame_sha256='b' * 64,
            source_frame_id='frame-900',
            engine_fingerprint='reader-v1',
            model_sha256='c' * 64,
        )
        window = {
            'start_ms': 500,
            'end_ms': 1500,
            'owner_id': 'training-g08',
            'fields': ['speed'],
            'performance_fields': [],
            'training_option': 'speed',
            'source_result_projection': True,
        }

        merged, _recoveries = promote_with_metadata(base, [source], [window])
        event = training_events(merged)[0]
        self.assertEqual(event['deltas'], {'speed': 20})
        self.assertEqual(
            event['direct_gain_provenance']['speed']['basis'],
            'training_gain_source_phase',
        )
        self.assertEqual(
            event['direct_gain_provenance']['speed']['source_result_projection']['owner_id'],
            'training-g08',
        )
        self.assertEqual(
            event['direct_gain_provenance']['speed']['source_result_projection']['source_frame_id'],
            'frame-900',
        )
        self.assertEqual(
            event['direct_gain_provenance']['speed']['source_result_projection']['owner_interval_ms'],
            [500, 1500],
        )

        tampered = copy.deepcopy(merged)
        tampered[0]['facts']['training_gain_recovery']['source_frame_sha256'] = 'd' * 64
        self.assertEqual(training_events(tampered)[0]['deltas'], {})

        outside_interval = copy.deepcopy(merged)
        outside_interval[0]['facts']['training_gain_recovery']['owner_interval_ms'] = [
            900, 950,
        ]
        self.assertEqual(training_events(outside_interval)[0]['deltas'], {})

        missing_interval = copy.deepcopy(merged)
        missing_interval[0]['facts']['training_gain_recovery'].pop(
            'owner_interval_ms', None,
        )
        self.assertEqual(training_events(missing_interval)[0]['deltas'], {})

        # The same single reading without the source-result owner tuple stays
        # outside the ordinary one-frame direct fallback.
        unbound = copy.deepcopy(source)
        unbound['facts']['training_gain_recovery'] = None
        self.assertEqual(training_events([unbound])[0]['deltas'], {})

    def test_wrong_option_preview_and_non_success_rereads_are_rejected(self):
        base = [_result_row(1000, 'base.png', result_values={'speed': 334})]
        window = {
            'start_ms': 500,
            'end_ms': 1500,
            'owner_id': 'training-g08',
            'fields': ['speed'],
            'performance_fields': ['dance'],
            'training_option': 'speed',
            'source_result_projection': True,
        }
        for row in (
            _result_row(900, 'wrong-option.png', option='power', gains={'speed': 20}),
            _result_row(900, 'preview.png', gains={'speed': 20}, preview=True),
            _result_row(900, 'preview-overlay.png', gains={'speed': 20}, preview_overlay=True),
            _result_row(900, 'failure.png', gains={'speed': 20}, outcome='failure'),
        ):
            with self.subTest(evidence=row['evidence']):
                merged, recoveries = promote_with_metadata(base, [row], [window])
                self.assertEqual(merged, base)
                self.assertEqual(recoveries, [])

    def test_conflicting_same_timestamp_projection_is_quarantined(self):
        base = [_result_row(1000, 'base.png', result_values={'speed': 334})]
        fresh = [
            _result_row(900, 'reread-a.png', gains={'speed': 20}),
            _result_row(900, 'reread-b.png', gains={'speed': 21}),
            _result_row(900, 'reread-c.png', gains={'speed': 20}),
        ]
        window = {
            'start_ms': 500,
            'end_ms': 1500,
            'owner_id': 'training-g08',
            'fields': ['speed'],
            'performance_fields': [],
            'training_option': 'speed',
            'source_result_projection': True,
        }

        merged, recoveries = promote_with_metadata(base, fresh, [window])

        self.assertEqual(merged, base)
        self.assertEqual(recoveries, [])

    def test_conflicting_same_timestamp_option_is_quarantined(self):
        base = [_result_row(1000, 'base.png', result_values={'speed': 334})]
        fresh = [
            _result_row(900, 'reread-a.png', gains={'speed': 20}),
            _result_row(900, 'reread-b.png', option='power', gains={'speed': 20}),
        ]
        window = {
            'start_ms': 500,
            'end_ms': 1500,
            'owner_id': 'training-g08',
            'fields': ['speed'],
            'performance_fields': [],
            'training_option': 'speed',
            'source_result_projection': True,
        }

        merged, recoveries = promote_with_metadata(base, fresh, [window])

        self.assertEqual(merged, base)
        self.assertEqual(recoveries, [])


if __name__ == '__main__':
    unittest.main()
