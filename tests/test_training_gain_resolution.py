import unittest
from copy import deepcopy

from tracen_replay.training_gain_resolution import (
    resolve_candidate_only_gain,
    resolve_prefix,
    resolve_source_clipped_gain,
)
from tracen_replay.training_gain_source_refinement import (
    SCHEMA as SOURCE_REFINEMENT_SCHEMA,
    refinement_requests,
)


def observations(values):
    return [(dict(source_timestamp_ms=time,evidence=f'{time}.png'),value) for time,value in values]


def source_candidate(family, text, confidence, box, amount, field='wit'):
    return {
        'region': f'{family}.{field}',
        'crop_family': family,
        'raw_text': text,
        'confidence': confidence,
        'box': list(box),
        'source_role': (
            'result_crop_diagnostic_excluded'
            if family == 'result' else 'amount_crop_candidate'
        ),
        'input_eligible': family != 'result',
        'canonical_eligible': family != 'result' and confidence >= 90,
        'amount': amount,
        'normalization': 'signed_amount',
    }


def clipped_source_row(timestamp, evidence, candidates, option='wit'):
    return {
        'screen': 'training_result',
        'training_option': option,
        'stats': {'training_preview': False},
        'source_timestamp_ms': timestamp,
        'evidence': evidence,
        'facts': {
            'training_gains': {'wit': 6},
            'training_gain_crop_provenance': {
                'wit': {'candidates': deepcopy(candidates)},
            },
        },
    }


def g7_wit_rows():
    """Compact transcription of the frozen native T069 source sidecars."""

    tight_box = (498, 950, 610, 1008)
    wide_box = (448, 930, 660, 1027)
    result_box = (518, 952, 644, 994)
    return [
        clipped_source_row(
            1644683,
            'training-inspection/1644250/frame-000014.png',
            [
                source_candidate('gain', '+6', 99.456, tight_box, 6),
                source_candidate('result', '+65', 93.110, result_box, 65),
            ],
        ),
        clipped_source_row(
            1644700,
            'training-inspection/1644483-native-v2/frame-000014.png',
            [
                source_candidate('gain', '+6', 99.314, tight_box, 6),
                source_candidate('result', '+65', 98.977, result_box, 65),
            ],
        ),
        clipped_source_row(
            1644733,
            'training-inspection/1644483-native-v2/frame-000016.png',
            [
                source_candidate('gain', '+6', 99.472, tight_box, 6),
                source_candidate('wide_gain', '+65', 79.291, wide_box, 65),
            ],
        ),
    ]


def _review_candidate(
    field, family, amount, *, confidence=99.0, box=None,
    localized=False, canonical=True, result=False,
):
    if box is None:
        box = {
            'speed': [300, 832, 414, 890],
            'guts': [300, 950, 414, 1008],
            'wit': [498, 950, 610, 1008],
            'skill_points': [696, 950, 812, 1008],
        }.get(field, [300, 832, 414, 890])
    if family in {'wide_gain', 'expanded_gain'}:
        box = [box[0] - 50, box[1] - 20, box[2] + 50, box[3] + 20]
    if localized:
        box = [box[0] + 10, box[1] + 3, box[2] - 10, box[3] - 9]
    source_role = 'result_crop_diagnostic_excluded' if result else 'amount_crop_candidate'
    candidate = {
        'region': f'{family}.{field}',
        'crop_family': family,
        'raw_text': f'+{amount}',
        'confidence': confidence,
        'box': box,
        'source_role': source_role,
        'input_eligible': not result,
        'canonical_eligible': canonical and not result,
        'amount': amount,
        'normalization': 'signed_amount',
    }
    if localized:
        candidate.update(
            source_pixel_verified=True,
            source_observation_basis='source_pixel_localized_training_badge',
            pixel_rgb_sha256='0' * 64,
        )
    return candidate


def _review_row(timestamp, field, candidates, option='speed'):
    return {
        'screen': 'training_result',
        'training_option': option,
        'stats': {'training_preview': False},
        'source_timestamp_ms': timestamp,
        'evidence': f'review/frame-{timestamp}.png',
        'facts': {
            'training_gain_crop_provenance': {
                field: {'candidates': candidates},
            },
        },
    }


def _source_refined_inner_candidate(field, amount, *, confidence=94.0):
    """Build a source-bound expanded-inner candidate for resolver controls."""

    region, box, metadata = next(
        request for request in refinement_requests((field,))
        if request[0] == f"expanded_gain.inner.{field}"
    )
    return {
        'region': region,
        'crop_family': 'expanded_gain',
        'raw_text': f'+{amount}',
        'confidence': confidence,
        'box': list(box),
        'source_role': 'amount_crop_candidate',
        'input_eligible': True,
        'canonical_eligible': True,
        'amount': amount,
        'normalization': 'signed_amount',
        'source_refinement_schema': SOURCE_REFINEMENT_SCHEMA,
        'source_refinement_role': metadata['source_refinement_role'],
        'source_refinement_geometry': metadata['source_refinement_geometry'],
        'source_refinement_verified': True,
        'source_refinement_pixel_bound': True,
        'source_refinement_validation': SOURCE_REFINEMENT_SCHEMA + '/pixel-binding-v1',
        'source_pixel_verified': True,
        'source_pixel_basis': 'relative_training_gain_source_crop',
        'source_observation_basis': 'source_pixel_refined_training_gain',
        'source_crop_sha256': 'b' * 64,
        'gameplay_sha256': 'a' * 64,
        'source_frame_sha256': 'c' * 64,
    }


def _source_refined_outer_candidate(field, amount, *, confidence=95.0):
    """Build a complete but deliberately noncanonical outer candidate."""

    region, box, metadata = next(
        request for request in refinement_requests((field,))
        if request[0] == f"expanded_gain.outer.{field}"
    )
    return {
        'region': region,
        'crop_family': 'expanded_gain',
        'raw_text': f'+{amount}',
        'confidence': confidence,
        'box': list(box),
        'source_role': 'unparsed_crop_candidate',
        'input_eligible': True,
        'canonical_eligible': False,
        'amount': amount,
        'normalization': 'signed_amount',
        'source_refinement_schema': SOURCE_REFINEMENT_SCHEMA,
        'source_refinement_role': metadata['source_refinement_role'],
        'source_refinement_geometry': metadata['source_refinement_geometry'],
        'source_refinement_verified': True,
        'source_refinement_pixel_bound': True,
        'source_refinement_validation': SOURCE_REFINEMENT_SCHEMA + '/pixel-binding-v1',
        'source_pixel_verified': True,
        'source_pixel_basis': 'relative_training_gain_source_crop',
        'source_observation_basis': 'source_pixel_refined_training_gain',
        'source_crop_sha256': 'd' * 64,
        'gameplay_sha256': 'a' * 64,
        'source_frame_sha256': 'e' * 64,
    }


class SourceReviewedResidualRecoveryTests(unittest.TestCase):
    def test_five_source_reviewed_crop_conflicts_resolve_without_balance_inputs(self):
        cases = [
            (
                'wit', 'wit', 17,
                [
                    (0, [('wide_gain', 1)]),
                    (33, [('wide_gain', 1), ('gain', 17)]),
                    (66, [('wide_gain', 17), ('gain', 17), ('localized_gain', 1)]),
                    (99, [('wide_gain', 17), ('gain', 17), ('localized_gain', 1)]),
                ],
            ),
            (
                'speed', 'speed', 44,
                [
                    (0, [('wide_gain', 44), ('localized_gain', 44)]),
                    (33, [('gain', 4)]),
                    (66, [('wide_gain', 44), ('gain', 44), ('localized_gain', 4)]),
                    (99, [('wide_gain', 44), ('gain', 44), ('localized_gain', 44)]),
                    (132, [('wide_gain', 44), ('localized_gain', 4)]),
                ],
            ),
            (
                'skill_points', 'skill_points', 13,
                [
                    (0, [('gain', 13), ('localized_gain', 13), ('result', 13)]),
                    (33, [('gain', 1)]),
                    (66, [('wide_gain', 13), ('gain', 13)]),
                    (99, [('wide_gain', 13), ('gain', 13)]),
                    (132, [('wide_gain', 13)]),
                ],
            ),
            (
                'skill_points_tight', 'skill_points', 11,
                [
                    (0, [('wide_gain', 11)]),
                    (33, [('wide_gain', 11), ('gain', 11)]),
                    (66, [('gain', 11)]),
                    (99, [('wide_gain', 10)]),
                ],
            ),
            (
                'guts', 'guts', 13,
                [
                    (0, [('wide_gain', 13)]),
                    (33, [('wide_gain', 18), ('gain', 13), ('localized_gain', 13)]),
                    (66, [('wide_gain', 18), ('gain', 13)]),
                    (99, [('wide_gain', 18), ('gain', 13)]),
                ],
            ),
        ]
        for name, field, expected, frame_specs in cases:
            with self.subTest(case=name):
                rows = [
                    _review_row(
                        timestamp,
                        field,
                        [
                            _review_candidate(
                                field,
                                family,
                                amount,
                                result=family == 'result',
                                localized=family == 'localized_gain',
                            )
                            for family, amount in candidates
                        ],
                        option='wit' if field == 'wit' else field,
                    )
                    for timestamp, candidates in frame_specs
                ]
                result = resolve_source_clipped_gain(
                    rows,
                    field,
                    phase_key=f'review:{name}',
                )
                self.assertEqual(result['status'], 'accepted')
                self.assertEqual(result['accepted_amount'], expected)
                self.assertFalse(result['policy']['uses_balance_arithmetic'])
                self.assertFalse(result['policy']['uses_expected_amount'])

    def test_source_reviewed_alternatives_remain_unresolved_when_phase_geometry_is_ambiguous(self):
        rows = [
            _review_row(
                0,
                'speed',
                [_review_candidate('speed', 'gain', 44)],
            ),
            _review_row(
                33,
                'speed',
                [_review_candidate('speed', 'gain', 44)],
            ),
            _review_row(
                66,
                'speed',
                [
                    _review_candidate('speed', 'gain', 44),
                    _review_candidate('speed', 'gain', 45, box=[302, 832, 414, 890]),
                ],
            ),
        ]
        result = resolve_source_clipped_gain(rows, 'speed', phase_key='ambiguous')
        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])


class SourceRefinedInnerPhaseTests(unittest.TestCase):
    def test_source_bound_inner_crop_can_establish_single_frame_phase(self):
        rows = [
            _review_row(
                845717,
                'speed',
                [
                    _source_refined_inner_candidate('speed', 36),
                    _review_candidate('speed', 'result', 3, result=True),
                ],
            ),
        ]

        result = resolve_source_clipped_gain(
            rows, 'speed', phase_key='speed:845717:845717',
        )

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['accepted_amount'], 36)
        self.assertEqual(result['basis'], 'source_pixel_refined_training_gain_phase')
        self.assertEqual(result['resolution_mode'], 'source_refinement_inner_single_frame')
        self.assertEqual(
            [item['amount'] for item in result['accepted_observations']], [36]
        )
        self.assertFalse(result['policy']['uses_balance_arithmetic'])
        self.assertFalse(result['policy']['uses_expected_amount'])

    def test_unbound_inner_candidate_does_not_establish_phase(self):
        candidate = _source_refined_inner_candidate('speed', 36)
        candidate.pop('source_refinement_validation')
        rows = [_review_row(845717, 'speed', [candidate])]

        result = resolve_source_clipped_gain(
            rows, 'speed', phase_key='speed:845717:845717',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])
        self.assertNotEqual(result['reason'], 'source_pixel_refined_training_gain_phase')

    def test_conflicting_bound_inner_amounts_remain_unresolved(self):
        first = _source_refined_inner_candidate('speed', 36)
        second = _source_refined_inner_candidate('speed', 32)
        first['source_frame_sha256'] = 'c' * 64
        second['source_frame_sha256'] = 'd' * 64
        rows = [
            _review_row(845717, 'speed', [first]),
            _review_row(845750, 'speed', [second]),
        ]

        result = resolve_source_clipped_gain(
            rows, 'speed', phase_key='speed:845717:845750',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])
        self.assertEqual(result['reason'], 'conflicting_source_refinement_inner_amounts')

    def test_canonical_competing_crop_is_not_ranked_around(self):
        competing = _review_candidate('speed', 'gain', 37, confidence=99.0)
        rows = [
            _review_row(
                845717,
                'speed',
                [_source_refined_inner_candidate('speed', 36), competing],
            ),
        ]

        result = resolve_source_clipped_gain(
            rows, 'speed', phase_key='speed:845717:845717',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])
        self.assertEqual(result['reason'], 'source_refinement_canonical_crop_conflict')

    def test_preview_inner_candidate_cannot_establish_committed_phase(self):
        row = _review_row(
            845717, 'speed', [_source_refined_inner_candidate('speed', 36)]
        )
        row['stats']['training_preview'] = True

        result = resolve_source_clipped_gain(
            [row], 'speed', phase_key='speed:845717:845717',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])
        self.assertEqual(result['reason'], 'no_committed_result_phase_rows')

    def test_complete_noncanonical_outer_crop_conflict_cannot_be_ranked_around(self):
        rows = [
            _review_row(
                845717,
                'speed',
                [
                    _source_refined_inner_candidate('speed', 36),
                    _source_refined_outer_candidate('speed', 37),
                ],
            ),
        ]

        result = resolve_source_clipped_gain(
            rows, 'speed', phase_key='speed:845717:845717',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])
        self.assertEqual(result['reason'], 'source_refinement_outer_crop_conflict')
        outer = next(
            item for item in result['observations']
            if item['region'] == 'expanded_gain.outer.speed'
        )
        self.assertEqual(outer['source_observation_role'], 'unparsed_crop_candidate')

    def test_low_confidence_noncanonical_outer_crop_stays_diagnostic(self):
        rows = [
            _review_row(
                845717,
                'speed',
                [
                    _source_refined_inner_candidate('speed', 36),
                    _source_refined_outer_candidate('speed', 37, confidence=69.0),
                ],
            ),
        ]

        result = resolve_source_clipped_gain(
            rows, 'speed', phase_key='speed:845717:845717',
        )

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['accepted_amount'], 36)
        self.assertEqual(
            next(
                item for item in result['observations']
                if item['region'] == 'expanded_gain.outer.speed'
            )['amount'],
            37,
        )

    def test_source_frame_hash_aliases_cannot_claim_two_refined_frames(self):
        first = _source_refined_inner_candidate('speed', 36)
        second = deepcopy(first)
        first['source_frame_sha256'] = second['source_frame_sha256'] = 'f' * 64
        rows = [
            _review_row(845717, 'speed', [first]),
            _review_row(845750, 'speed', [second]),
        ]

        result = resolve_source_clipped_gain(
            rows, 'speed', phase_key='speed:845717:845750',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])
        self.assertEqual(result['reason'], 'duplicate_source_frame_identity')
        self.assertTrue(result['observations'])
        self.assertEqual(result['observations'][0]['source_frame_sha256'], 'f' * 64)


class TrainingGainResolutionTests(unittest.TestCase):
    def test_brief_prefix_does_not_discard_two_complete_badges(self):
        result=resolve_prefix(observations([(0,2),(33,21),(66,21)]))
        self.assertEqual(result['accepted_amount'],21)
        self.assertEqual([r['evidence'] for r in result['complete_observations']],['33.png','66.png'])
        self.assertEqual(result['prefix_observations'][0]['value'],2)

    def test_bracketed_glare_can_hide_a_trailing_digit(self):
        self.assertEqual(resolve_prefix(observations([(0,52),(33,5),(66,5),(100,52)]))['accepted_amount'],52)

    def test_mixed_component_shapes_cannot_fall_back_to_prefix_resolution(self):
        rows=[]
        for time,value,fields in [(0,52,('speed','wit')),(33,52,('speed','wit')),
                                  (66,5,('speed',)),(99,5,('speed','wit'))]:
            rows.append((dict(source_timestamp_ms=time,evidence=f'{time}.png',
                              facts={'observed_training_gain_fields':list(fields)}),value))
        self.assertIsNone(resolve_prefix(rows))
        self.assertIsNone(resolve_prefix(list(reversed(rows))))

    def test_reject_singleton_sustained_nonprefix_and_duplicate_views(self):
        for values in ([(0,2),(33,21)],[(0,2),(33,21),(33,21)],
                       [(0,2),(33,21),(66,21),(100,2),(133,2)],
                       [(0,7),(33,21),(66,21)],[(0,2),(300,21),(333,21)],
                       [(0,2),(0,21),(33,21),(66,21)],[(0,72),(33,7),(66,7),(100,7)]):
            self.assertIsNone(resolve_prefix(observations(values)),values)

    def test_g7_native_wit_crop_recovers_complete_badge_from_source_views(self):
        result = resolve_source_clipped_gain(
            g7_wit_rows(), 'wit', phase_key='training-0069',
        )

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['accepted_amount'], 65)
        self.assertEqual(
            result['basis'],
            'cross_frame_source_clipped_gain_overlay_and_broad_agreement',
        )
        self.assertEqual(result['observed_amounts'], [6, 65])
        self.assertEqual(
            {item['crop_family'] for item in result['full_observations']},
            {'result', 'wide_gain'},
        )
        self.assertEqual(
            [item['amount'] for item in result['short_observations']],
            [6, 6, 6],
        )
        self.assertNotIn('result_values', result)
        self.assertNotIn('training_gains', result)

    def test_signed_result_overlay_without_broad_agreement_stays_unresolved(self):
        rows = g7_wit_rows()[:2]

        result = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])
        self.assertEqual(result['reason'], 'insufficient_source_clipping_corroboration')

    def test_conflicting_complete_crop_values_stay_unresolved(self):
        rows = g7_wit_rows()
        conflicting = rows[1]['facts']['training_gain_crop_provenance']['wit']['candidates']
        result_candidate = next(item for item in conflicting if item['crop_family'] == 'result')
        result_candidate.update(raw_text='+66', amount=66)

        result = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])

    def test_unrelated_neighbor_geometry_cannot_supply_complete_badge(self):
        rows = g7_wit_rows()
        wide = next(
            item for item in rows[2]['facts']['training_gain_crop_provenance']['wit']['candidates']
            if item['crop_family'] == 'wide_gain'
        )
        wide['box'] = [20, 20, 232, 117]

        result = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])

    def test_nonprefix_neighbor_amount_cannot_be_ignored(self):
        rows = g7_wit_rows()
        rows[2]['facts']['training_gain_crop_provenance']['wit']['candidates'].append(
            source_candidate('wide_gain', '+71', 79.0, (448, 930, 660, 1027), 71),
        )

        result = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['accepted_amount'])

    def test_changed_training_option_cannot_cross_bind_the_source_window(self):
        rows = g7_wit_rows()
        rows[2]['training_option'] = 'power'

        result = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['reason'], 'conflicting_training_options')

    def test_declared_family_must_match_region_for_clipped_recovery(self):
        rows = g7_wit_rows()
        candidates = rows[1]['facts']['training_gain_crop_provenance']['wit']['candidates']
        result = next(item for item in candidates if item['crop_family'] == 'result')
        result['region'] = 'fake.wit'
        wide = rows[2]['facts']['training_gain_crop_provenance']['wit']['candidates']
        wide_candidate = next(item for item in wide if item['crop_family'] == 'wide_gain')
        wide_candidate['region'] = 'gain.wit'

        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertIsNone(resolution['accepted_amount'])

    def test_region_must_bind_exactly_to_declared_family_and_field(self):
        rows = g7_wit_rows()
        for row in rows:
            for candidate in row['facts']['training_gain_crop_provenance']['wit']['candidates']:
                family = candidate['crop_family']
                candidate['region'] = f'{family}.other.wit'

        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertIsNone(resolution['accepted_amount'])

    def test_result_role_metadata_is_required_for_clipped_recovery(self):
        rows = g7_wit_rows()
        for row in rows:
            for candidate in row['facts']['training_gain_crop_provenance']['wit']['candidates']:
                if candidate['crop_family'] == 'result':
                    candidate['source_role'] = 'signed_overlay_diagnostic'

        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertIsNone(resolution['accepted_amount'])

    def test_signed_prefix_metadata_cannot_promote_nonexpanded_family(self):
        rows = g7_wit_rows()
        for row in rows:
            for candidate in row['facts']['training_gain_crop_provenance']['wit']['candidates']:
                if candidate['crop_family'] in {'gain', 'wide_gain', 'result'}:
                    candidate['normalization'] = 'signed_amount_prefix'
                    candidate['raw_text'] = '+65x'
                    candidate['amount'] = 65

        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertIsNone(resolution['accepted_amount'])

    def test_explicit_preview_markers_cannot_supply_committed_clipping_proof(self):
        for marker_kind in ('facts_preview', 'phase', 'training_phase'):
            rows = g7_wit_rows()
            for row in rows:
                if marker_kind == 'facts_preview':
                    row['facts']['preview'] = True
                else:
                    row[marker_kind] = 'preview'

            resolution = resolve_source_clipped_gain(
                rows, 'wit', phase_key='training-0069',
            )

            self.assertEqual(resolution['status'], 'unresolved', marker_kind)
            self.assertEqual(resolution['reason'], 'no_committed_result_phase_rows', marker_kind)

    def test_minimum_tight_frames_is_enforced(self):
        resolution = resolve_source_clipped_gain(
            g7_wit_rows(), 'wit', phase_key='training-0069',
            minimum_tight_frames=4,
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertEqual(
            resolution['reason'], 'insufficient_source_clipping_corroboration',
        )

    def test_minimum_full_frames_is_enforced(self):
        resolution = resolve_source_clipped_gain(
            g7_wit_rows(), 'wit', phase_key='training-0069',
            minimum_full_frames=4,
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertEqual(
            resolution['reason'], 'insufficient_source_clipping_corroboration',
        )

    def test_broad_crop_must_be_strictly_broader_than_tight_crop(self):
        for broad_box in (
            (498, 950, 610, 1008),
            (499, 951, 609, 1007),
        ):
            rows = g7_wit_rows()
            wide = rows[2]['facts']['training_gain_crop_provenance']['wit']['candidates']
            wide_candidate = next(item for item in wide if item['crop_family'] == 'wide_gain')
            wide_candidate['box'] = list(broad_box)

            resolution = resolve_source_clipped_gain(
                rows, 'wit', phase_key='training-0069',
            )

            self.assertEqual(resolution['status'], 'unresolved', broad_box)
            self.assertIsNone(resolution['accepted_amount'], broad_box)

    def test_evidence_path_aliases_do_not_create_extra_physical_frames(self):
        rows = g7_wit_rows()[:2]
        duplicate = deepcopy(rows[1])
        duplicate['evidence'] = './training-inspection//1644483-native-v2//frame-000014.png'
        rows.append(duplicate)

        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
            minimum_tight_frames=3,
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertEqual(
            resolution['reason'], 'insufficient_source_clipping_corroboration',
        )

    def test_same_evidence_alias_at_two_timestamps_is_duplicate_source_frame(self):
        rows = g7_wit_rows()
        rows[1]['evidence'] = './training-inspection//1644250//frame-000014.png'

        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )

        self.assertEqual(resolution['status'], 'unresolved')
        self.assertEqual(resolution['reason'], 'duplicate_source_frame_identity')

    def test_invalid_bounds_and_phase_owner_are_unresolved(self):
        invalid_bounds = (
            {'minimum_tight_confidence': None},
            {'minimum_overlay_confidence': 'bad'},
            {'minimum_broad_confidence': float('nan')},
            {'minimum_tight_frames': 'bad'},
            {'minimum_full_frames': None},
            {'maximum_span_ms': 'bad'},
            {'maximum_gap_ms': None},
        )
        for override in invalid_bounds:
            resolution = resolve_source_clipped_gain(
                g7_wit_rows(), 'wit', phase_key='training-0069', **override,
            )
            self.assertEqual(resolution['status'], 'unresolved', override)
            self.assertEqual(resolution['reason'], 'invalid_source_clipping_policy', override)

        for phase_key in ([], {}, True, '', '   ', 1.5):
            resolution = resolve_source_clipped_gain(
                g7_wit_rows(), 'wit', phase_key=phase_key,
            )
            self.assertEqual(resolution['status'], 'unresolved', phase_key)
            self.assertEqual(resolution['reason'], 'invalid_source_phase_owner', phase_key)

        self.assertEqual(
            resolve_source_clipped_gain(
                g7_wit_rows(), 'wit', phase_key=None,
            )['reason'],
            'missing_source_phase_owner',
        )

    def test_source_clipping_rejects_malformed_candidate_confidence_and_timestamp(self):
        rows = g7_wit_rows()
        candidate = next(
            item for item in rows[0]['facts']['training_gain_crop_provenance']['wit']['candidates']
            if item['crop_family'] == 'gain'
        )
        candidate['confidence'] = 101

        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )
        self.assertEqual(resolution['status'], 'unresolved')
        self.assertEqual(resolution['reason'], 'invalid_source_confidence')

        rows = g7_wit_rows()
        rows[0]['source_timestamp_ms'] = 100.9
        resolution = resolve_source_clipped_gain(
            rows, 'wit', phase_key='training-0069',
        )
        self.assertEqual(resolution['status'], 'unresolved')
        self.assertEqual(resolution['reason'], 'invalid_source_identity')

    def test_candidate_only_resolver_rejects_malformed_confidence_and_timestamp(self):
        for mutation, value, expected_reason in (
            ('confidence', float('inf'), 'invalid_source_confidence'),
            ('confidence', '100', 'invalid_source_confidence'),
            ('source_timestamp_ms', 100.9, 'invalid_source_identity'),
            ('source_timestamp_ms', '100', 'invalid_source_identity'),
        ):
            rows = g7_wit_rows()
            if mutation == 'confidence':
                candidate = next(
                    item for item in rows[0]['facts']['training_gain_crop_provenance']['wit']['candidates']
                    if item['crop_family'] == 'gain'
                )
                candidate[mutation] = value
            else:
                rows[0][mutation] = value

            resolution = resolve_candidate_only_gain(
                rows, 'wit', phase_key='training-0069',
            )
            self.assertEqual(resolution['status'], 'unresolved', (mutation, value))
            self.assertEqual(resolution['reason'], expected_reason, (mutation, value))

    def test_prefix_resolver_requires_integer_timestamp_and_unique_source_path(self):
        for timestamp, evidence in ((100.9, 'a.png'), ('100', 'a.png'), (100, None)):
            rows = [
                (dict(source_timestamp_ms=timestamp, evidence=evidence), 2),
                (dict(source_timestamp_ms=133, evidence='b.png'), 21),
                (dict(source_timestamp_ms=166, evidence='c.png'), 21),
            ]
            self.assertIsNone(resolve_prefix(rows), (timestamp, evidence))

        rows = [
            (dict(source_timestamp_ms=100, evidence='a/../frame.png'), 2),
            (dict(source_timestamp_ms=100, evidence='frame.png'), 2),
            (dict(source_timestamp_ms=133, evidence='other.png'), 21),
            (dict(source_timestamp_ms=166, evidence='last.png'), 21),
        ]
        self.assertIsNone(resolve_prefix(rows))
