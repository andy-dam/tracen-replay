from copy import deepcopy
import unittest
from tracen_replay.hint_card_events import apply


SOURCE = 'a'*64


def fixture():
    rows=[]
    observations=[]
    for time in (1000,1250):
        evidence=f'gameplay/{time}.png'
        card=dict(text='Example Skill',box=[400,678,565,703],confidence=99,suffix='single_circle')
        receipt=dict(text='Gained 2 hint level(s) for Exmple Skill.',box=[316,853,731,881],amount=2)
        rows.append(dict(source_timestamp_ms=time,evidence=evidence,screen='event_outcome',
                         context_title='A Present',effects=[],
                         ocr={'neural':[deepcopy(card),dict(deepcopy(receipt),confidence=0,overlay_occluded=True)]}))
        observations.append(dict(timestamp_ms=time,evidence=evidence,card=card,receipt=receipt,
                                 prefix_amount_proof={'amount':2}))
    candidate=dict(kind='skill_hint_change',name='Example Skill',amount=2,source_sha256=SOURCE,
                   source_timestamps_ms=[1000,1250],raw_receipt_name_candidates=['Exmple Skill'],
                   identity_proof={'same_context':'A Present','suffix':'single_circle'},observations=observations)
    event=dict(id='outcome-1',kind='outcome',first_seen_ms=900,last_seen_ms=1500,
               context_title='A Present',effects=[],field_evidence={},conflicting_readings=[],deltas={})
    return [event],rows,[candidate]


def wrapped_fixture():
    """Build a source-row-shaped wrapped receipt with an unknown rank."""
    rows = []
    observations = []
    for time in (1000, 1250):
        evidence = f'gameplay/{time}.png'
        card = dict(
            text='Wrapped Skill', confidence=99,
            box=[400, 678, 565, 703],
            header={'text': 'Hint', 'confidence': 99, 'box': [400, 650, 500, 675]},
            suffix=None, suffix_state='undetermined',
        )
        prefix = dict(
            text='Gained 2 hint level(s) for Wrapped', confidence=99,
            box=[316, 830, 590, 854], source='neural',
        )
        continuation = dict(
            text='Skill.', confidence=99,
            box=[316, 858, 410, 882], source='neural',
        )
        receipt = dict(
            text=f'{prefix["text"]} {continuation["text"]}',
            raw_name='Wrapped Skill', amount=2, confidence=99,
            box=[316, 830, 731, 882], source='wrapped_receipt',
        )
        rows.append(dict(
            source_timestamp_ms=time, evidence=evidence, screen='event_outcome',
            effects=[], facts={},
            ocr={'neural': [deepcopy(card), deepcopy(prefix), deepcopy(continuation)]},
        ))
        observations.append(dict(
            timestamp_ms=time, evidence=evidence,
            card=deepcopy(card), receipt=deepcopy(receipt),
            receipt_parts={'prefix': deepcopy(prefix), 'continuation': deepcopy(continuation)},
            prefix_amount_proof={
                'amount': 2, 'recognized_text': '2', 'confidence': 94,
                'crop_box': [395, 828, 415, 884],
                'pixel_rgb_sha256': 'e' * 64,
                'basis': 'source_bound_amount_digit_crop_ocr',
                'model_fingerprint': 'd' * 64,
                'delimiter_proof': {
                    'recognized_text': 'hint', 'confidence': 94,
                    'crop_box': [410, 828, 440, 884],
                    'pixel_rgb_sha256': 'f' * 64,
                    'basis': 'amount_digit_followed_by_hint_delimiter_ocr',
                    'model_fingerprint': 'd' * 64,
                },
            },
        ))
    candidate = dict(
        observation_kind='wrapped_hint_receipt', kind='skill_hint_change',
        name='Wrapped Skill', amount=2, source_sha256=SOURCE,
        source_timestamps_ms=[1000, 1250],
        raw_receipt_name_candidates=['Wrapped Skill'],
        identity_proof={
            'basis': 'standalone_hint_card_with_wrapped_receipt_and_per_row_amount_ocr',
            'card_text': 'Wrapped Skill', 'suffix': None,
            'rank_state': 'undetermined', 'distinct_timestamp_count': 2,
            'same_context': None, 'context_observed': False,
            'geometry_stable': True,
        },
        provenance={
            'source_evidence_type': 'decoded_gameplay_png',
            'capture_manifest_sha256': 'b' * 64,
            'source_frame_chain': 'capture.json -> source frame -> neural cache -> gameplay PNG',
            'independent_observations': False,
            'multi_crop_not_counted_as_timestamp': True,
            'prefix_ocr_model_fingerprint': 'd' * 64,
            'prefix_crop_count': 2,
        },
        observations=observations,
    )
    event = dict(
        id='outcome-wrapped', kind='outcome', first_seen_ms=900, last_seen_ms=1500,
        context_title='A Present', effects=[], field_evidence={},
        conflicting_readings=[], deltas={},
    )
    return [event], rows, [candidate]


def single_line_fixture():
    """Build a source-shaped unranked receipt with independent amount proof."""
    rows = []
    observations = []
    for time in (1000, 1250):
        evidence = f'gameplay/{time}.png'
        header = dict(text='Hint', confidence=99, box=[400, 650, 500, 675])
        card = dict(
            text='Example Skill', confidence=99,
            box=[400, 678, 565, 703], header=deepcopy(header),
            suffix=None, suffix_state='undetermined',
        )
        receipt = dict(
            text='Gained 2 hint level(s) for Exmple Skill.',
            raw_name='Exmple Skill', amount=2, confidence=99,
            box=[316, 853, 731, 881], source='neural',
        )
        overlay = [600, 853, 617, 881]
        row_lines = [deepcopy(card), deepcopy(header), deepcopy(receipt)]
        row_lines[-1]['overlay_occluded'] = True
        rows.append(dict(
            source_timestamp_ms=time, evidence=evidence, screen='event_outcome',
            context_title='A Present', context_title_candidate='A Present',
            effects=[], facts={}, ocr={'neural': row_lines},
        ))
        observations.append(dict(
            timestamp_ms=time, evidence=evidence, card=deepcopy(card),
            receipt=deepcopy(receipt), overlay_box=overlay,
            prefix_amount_proof={
                'amount': 2,
                'recognized_text': 'Gained 2 hint level(s) for Exmple Skill',
                'raw_name': 'Exmple Skill', 'confidence': 94,
                'crop_box': [316, 853, 600, 881],
                'pixel_rgb_sha256': 'e' * 64,
                'model_fingerprint': 'd' * 64,
                'basis': 'independent_prefix_crop_ocr_to_verified_overlay_boundary',
            },
        ))
    candidate = dict(
        observation_kind='single_line_hint_receipt', kind='skill_hint_change',
        name='Example Skill', amount=2, source_sha256=SOURCE,
        source_timestamps_ms=[1000, 1250],
        raw_receipt_name_candidates=['Exmple Skill'],
        identity_proof={
            'basis': 'standalone_hint_card_with_single_line_receipt_and_per_row_amount_ocr',
            'card_text': 'Example Skill', 'suffix': None,
            'rank_state': 'undetermined', 'distinct_timestamp_count': 2,
            'same_context': 'A Present', 'geometry_stable': True,
        },
        provenance={
            'source_evidence_type': 'decoded_gameplay_png',
            'capture_manifest_sha256': 'b' * 64,
            'source_frame_chain': 'capture.json -> source frame -> neural cache -> gameplay PNG',
            'independent_observations': False,
            'multi_crop_not_counted_as_timestamp': True,
            'prefix_ocr_model_fingerprint': 'd' * 64,
            'prefix_crop_count': 2,
        },
        observations=observations,
    )
    event = dict(
        id='outcome-single-line', kind='outcome', first_seen_ms=900,
        last_seen_ms=1500, context_title='A Present', effects=[],
        field_evidence={}, conflicting_readings=[], deltas={},
    )
    return [event], rows, [candidate]


class HintCardEventTests(unittest.TestCase):
    def test_report_reconstruction_reuses_observations_without_running_ocr(self):
        from unittest.mock import patch
        from tests.test_report_contract import valid_report
        from tracen_replay.full_recording import assemble
        from tracen_replay.report_contract import validate
        _,rows,candidates=fixture()
        for row in rows:
            row.update(stats={},facts={},training_option=None,context_title_candidate='A Present')
            row['effects']=[dict(kind='energy_change',amount=5,raw_text='Energy went up by 5.',confidence=99)]
        report=valid_report()
        with patch('tracen_replay.hint_card_identity.recover',side_effect=AssertionError('OCR in reconstruction')):
            assemble(report,rows,hint_card_observations=candidates)
            validate(report,require_gameplay=True)
            self.assertEqual(len(report['gameplay_tracking']['hint_card_recovery']['accepted']),1)
            snapshot=deepcopy(report)
            assemble(report,rows)
            self.assertEqual(report,snapshot)
        self.assertFalse(any(e['kind']=='skill_hint_change' for row in rows for e in row['effects']))

    def test_attaches_recovered_identity_and_preserves_raw_inputs(self):
        events,rows,candidates=fixture()
        originals=deepcopy((rows,candidates))
        result=apply(events,rows,candidates,source_sha256=SOURCE)
        self.assertEqual(result['rejected'],[])
        effect=events[0]['effects'][0]
        self.assertEqual((effect['name'],effect['amount']),('Example Skill ○',2))
        self.assertIn('Exmple',effect['raw_text'])
        self.assertTrue(effect['circle_variant_verified'])
        self.assertEqual((rows,candidates),originals)
        before=deepcopy(events)
        apply(events,rows,candidates,source_sha256=SOURCE)
        self.assertEqual(events,before)

    def test_rejects_foreign_source_or_changed_text_and_amount(self):
        for change in ('source','card','receipt','amount','prefix'):
            with self.subTest(change=change):
                events,rows,candidates=fixture()
                if change=='source':candidates[0]['source_sha256']='b'*64
                if change=='card':rows[0]['ocr']['neural'][0]['text']='Different Skill'
                if change=='receipt':rows[0]['ocr']['neural'][1]['box'][1]+=1
                if change=='amount':candidates[0]['amount']=3
                if change=='prefix':candidates[0]['observations'][0]['prefix_amount_proof']['amount']=True
                before=deepcopy(events)
                result=apply(events,rows,candidates,source_sha256=SOURCE)
                self.assertEqual(result['accepted'],[])
                self.assertEqual(events,before)

    def test_duplicate_timestamp_and_intervening_screen_break_support(self):
        for duplicate in (True,False):
            events,rows,candidates=fixture()
            if duplicate:
                candidates[0]['observations'][1]['timestamp_ms']=1000
            else:
                middle=deepcopy(rows[0]);middle.update(source_timestamp_ms=1125,evidence='1125.png',screen='career_hub')
                rows.insert(1,middle)
            self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
            self.assertEqual(events[0]['effects'],[])

    def test_support_cannot_cross_or_create_outcome_boundaries(self):
        events,rows,candidates=fixture()
        second=deepcopy(events[0]);second.update(id='outcome-2',first_seen_ms=1200)
        events[0]['last_seen_ms']=1100;events.append(second)
        self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
        self.assertEqual([e['effects'] for e in events],[[],[]])

    def test_replaces_only_fully_supported_same_receipt_alternative(self):
        for outside in (False,True):
            events,rows,candidates=fixture()
            old=dict(kind='skill_hint_change',name='Exmple Skill',amount=2,
                     raw_text=candidates[0]['observations'][0]['receipt']['text'])
            events[0]['effects']=[old]
            events[0]['field_evidence']['skill_hint_change||Exmple Skill']=[rows[0]['evidence']]
            if outside:events[0]['field_evidence']['skill_hint_change||Exmple Skill'].append('earlier.png')
            apply(events,rows,candidates,source_sha256=SOURCE)
            self.assertEqual(old in events[0]['effects'],outside)
            self.assertEqual(len(events[0]['effects']),2 if outside else 1)
            if not outside:
                self.assertNotIn('skill_hint_change||Exmple Skill',events[0]['field_evidence'])
                archived=events[0]['effects'][0]['replaced_receipt_readings'][0]
                self.assertEqual(archived['field_evidence'],[rows[0]['evidence']])
                self.assertEqual(archived['raw_text'],old['raw_text'])

    def test_existing_amount_conflict_prevents_promotion(self):
        for name in ('Example Skill ○','Example Skill','Example Skill O'):
            with self.subTest(name=name):
                events,rows,candidates=fixture()
                events[0]['effects']=[dict(kind='skill_hint_change',name=name,amount=3)]
                before=deepcopy(events)
                self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
                self.assertEqual(events,before)

    def test_disjoint_conflicting_candidates_abstain_in_either_order(self):
        for conflict in ('amount','rank'):
            for reverse in (False,True):
                with self.subTest(conflict=conflict,reverse=reverse):
                    events,rows,candidates=fixture()
                    _,more_rows,more_candidates=fixture()
                    second=more_candidates[0]
                    for row,observation in zip(more_rows,second['observations']):
                        time=row['source_timestamp_ms']+500
                        row.update(source_timestamp_ms=time,evidence=f'gameplay/{time}.png')
                        observation.update(timestamp_ms=time,evidence=row['evidence'])
                        if conflict=='amount':
                            observation['receipt']['amount']=3
                            observation['receipt']['text']='Gained 3 hint level(s) for Exmple Skill.'
                            row['ocr']['neural'][1]['text']=observation['receipt']['text']
                            observation['prefix_amount_proof']['amount']=3
                        else:
                            observation['card']['suffix']='double_circle'
                    second['source_timestamps_ms']=[1500,1750]
                    if conflict=='amount':second['amount']=3
                    else:second['identity_proof']['suffix']='double_circle'
                    rows.extend(more_rows);candidates.extend(more_candidates)
                    events[0]['last_seen_ms']=1800
                    if reverse:candidates.reverse()
                    before=deepcopy(events)
                    result=apply(events,rows,candidates,source_sha256=SOURCE)
                    self.assertEqual(result['accepted'],[])
                    self.assertEqual([r['reason'] for r in result['rejected']],['conflicting_hint_candidates']*2)
                    self.assertEqual(events,before)

    def test_overlapping_outcome_targets_and_existing_rank_conflict_abstain(self):
        for conflict in ('target','rank'):
            events,rows,candidates=fixture()
            if conflict=='target':
                extra=deepcopy(events[0]);extra['id']='outcome-2';events.append(extra)
            else:
                events[0]['effects']=[dict(kind='skill_hint_change',name='Example Skill ◎',amount=2)]
            before=deepcopy(events)
            self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
            self.assertEqual(events,before)

    def test_explicit_circle_is_not_duplicated_and_conflicting_rank_rejected(self):
        for glyph,accepted in (('○',True),('◎',False)):
            events,rows,candidates=fixture()
            name='Example Skill '+glyph
            candidates[0]['name']=name
            for row,observation in zip(rows,candidates[0]['observations']):
                row['ocr']['neural'][0]['text']=name;observation['card']['text']=name
            result=apply(events,rows,candidates,source_sha256=SOURCE)
            self.assertEqual(bool(result['accepted']),accepted)
            if accepted:self.assertEqual(events[0]['effects'][0]['name'],name)

    def test_attaches_wrapped_identity_with_unknown_rank_and_raw_parts(self):
        events, rows, candidates = wrapped_fixture()
        originals = deepcopy((rows, candidates))
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['rejected'], [])
        effect = events[0]['effects'][0]
        self.assertEqual((effect['name'], effect['amount']), ('Wrapped Skill', 2))
        self.assertEqual(effect['rank_state'], 'undetermined')
        self.assertFalse(effect['circle_variant_verified'])
        self.assertEqual(
            effect['identity_basis'],
            'validated_repeated_hint_card_and_wrapped_receipt',
        )
        saved = effect['hint_card_evidence'][0]
        self.assertEqual(
            saved['observations'][0]['receipt_parts']['prefix']['text'],
            'Gained 2 hint level(s) for Wrapped',
        )
        self.assertEqual(saved['observations'][0]['receipt_parts']['continuation']['text'], 'Skill.')
        self.assertEqual(rows, originals[0])
        self.assertEqual(candidates, originals[1])

    def test_attaches_single_line_identity_with_unknown_rank(self):
        events, rows, candidates = single_line_fixture()
        originals = deepcopy((rows, candidates))
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['rejected'], [])
        effect = events[0]['effects'][0]
        self.assertEqual((effect['name'], effect['amount']), ('Example Skill', 2))
        self.assertEqual(effect['rank_state'], 'undetermined')
        self.assertFalse(effect['circle_variant_verified'])
        self.assertEqual(
            effect['identity_basis'],
            'validated_repeated_hint_card_and_single_line_receipt',
        )
        self.assertEqual((rows, candidates), originals)

    def test_single_line_mode_rejects_single_frame_or_contradictory_proof(self):
        for change in (
            'single_frame', 'suffix', 'name', 'amount', 'prefix_text',
            'prefix_name', 'prefix_model',
        ):
            with self.subTest(change=change):
                events, rows, candidates = single_line_fixture()
                if change == 'single_frame':
                    candidates[0]['observations'].pop()
                    candidates[0]['source_timestamps_ms'] = [1000]
                elif change == 'suffix':
                    candidates[0]['observations'][0]['card']['suffix'] = 'single_circle'
                elif change == 'name':
                    candidates[0]['observations'][1]['receipt']['raw_name'] = 'Other Skill'
                elif change == 'amount':
                    candidates[0]['observations'][0]['prefix_amount_proof']['amount'] = 3
                elif change == 'prefix_text':
                    candidates[0]['observations'][0]['prefix_amount_proof']['recognized_text'] = (
                        'Gained 2 hint level(s) for Other Skill'
                    )
                    candidates[0]['observations'][0]['prefix_amount_proof']['raw_name'] = 'Other Skill'
                elif change == 'prefix_name':
                    candidates[0]['observations'][0]['prefix_amount_proof']['raw_name'] = 'Other Skill'
                else:
                    candidates[0]['observations'][0]['prefix_amount_proof']['model_fingerprint'] = 'f' * 64
                before = deepcopy(events)
                result = apply(events, rows, candidates, source_sha256=SOURCE)
                self.assertEqual(result['accepted'], [])
                self.assertEqual(events, before)

    def test_wrapped_candidate_rejects_missing_receipt_part(self):
        for missing in ('prefix', 'continuation'):
            with self.subTest(missing=missing):
                events, rows, candidates = wrapped_fixture()
                candidates[0]['observations'][0]['receipt_parts'][missing]['text'] = 'Corrupt.'
                before = deepcopy(events)
                result = apply(events, rows, candidates, source_sha256=SOURCE)
                self.assertEqual(result['accepted'], [])
                self.assertEqual(events, before)

    def test_wrapped_candidate_rejects_malformed_receipt_parts(self):
        events, rows, candidates = wrapped_fixture()
        candidates[0]['observations'][0]['receipt_parts']['prefix'] = None
        before = deepcopy(events)
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['accepted'], [])
        self.assertEqual(events, before)

    def test_wrapped_unknown_context_requires_unique_temporal_outcome(self):
        events, rows, candidates = wrapped_fixture()
        second = deepcopy(events[0])
        second['id'] = 'outcome-wrapped-2'
        events.append(second)
        before = deepcopy(events)
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['accepted'], [])
        self.assertEqual(events, before)

    def test_wrapped_candidate_rejects_invalid_amount_proof_or_rank_state(self):
        for change in ('proof', 'rank'):
            with self.subTest(change=change):
                events, rows, candidates = wrapped_fixture()
                if change == 'proof':
                    candidates[0]['observations'][1]['prefix_amount_proof']['confidence'] = 101
                else:
                    candidates[0]['identity_proof']['rank_state'] = 'absent'
                before = deepcopy(events)
                result = apply(events, rows, candidates, source_sha256=SOURCE)
                self.assertEqual(result['accepted'], [])
                self.assertEqual(events, before)

    def test_wrapped_candidate_rejects_relocated_delimiter_crop(self):
        events, rows, candidates = wrapped_fixture()
        candidates[0]['observations'][0]['prefix_amount_proof']['delimiter_proof']['crop_box'] = [
            500, 500, 530, 560
        ]
        before = deepcopy(events)
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['accepted'], [])
        self.assertEqual(events, before)

    def test_wrapped_unknown_rank_does_not_duplicate_ranked_existing_effect(self):
        events, rows, candidates = wrapped_fixture()
        existing = dict(
            kind='skill_hint_change', name='Wrapped Skill ○', amount=2,
            circle_variant_verified=True,
        )
        events[0]['effects'] = [existing]
        before = deepcopy(existing)
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['rejected'], [])
        self.assertEqual(len(events[0]['effects']), 1)
        self.assertEqual(events[0]['effects'][0]['name'], before['name'])
        self.assertEqual(events[0]['effects'][0]['amount'], before['amount'])
        self.assertTrue(events[0]['effects'][0]['circle_variant_verified'])
        self.assertEqual(events[0]['effects'][0]['hint_card_rank_state'], 'undetermined')

    def test_single_line_unknown_rank_preserves_verified_existing_variant(self):
        events, rows, candidates = single_line_fixture()
        events[0]['effects'] = [dict(
            kind='skill_hint_change', name='Example Skill ○', amount=2,
            circle_variant_verified=True,
        )]
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['rejected'], [])
        effect = events[0]['effects'][0]
        self.assertEqual(effect['name'], 'Example Skill ○')
        self.assertTrue(effect['circle_variant_verified'])
        self.assertEqual(effect['hint_card_rank_state'], 'undetermined')

    def test_wrapped_source_lines_select_high_confidence_retained_duplicate(self):
        from tracen_replay.hint_card_events import _wrapped_source_lines

        events, rows, candidates = wrapped_fixture()
        row = rows[1]
        prefix = deepcopy(row['ocr']['neural'][1])
        prefix['confidence'] = 0
        prefix['overlay_occluded'] = True
        row['ocr']['neural'][1] = prefix
        retained = deepcopy(prefix)
        retained['confidence'] = 92.407
        retained.pop('overlay_occluded', None)
        retained['overlay_boxes'] = [[522, 862, 535, 877]]
        row['facts'] = {'occluded_receipt_lines': [retained]}

        selected = [
            line for line in _wrapped_source_lines(row)
            if line.get('text') == prefix['text']
        ]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['confidence'], 92.407)
        self.assertEqual(selected[0]['source'], 'occluded_receipt_line')
        self.assertTrue(selected[0]['overlay_occluded'])
        self.assertEqual(selected[0]['overlay_boxes'], [[522, 862, 535, 877]])

    def test_attaches_wrapped_candidate_with_source_clear_fragment_proof(self):
        events, rows, candidates = wrapped_fixture()
        row = rows[1]
        source_prefix = row['ocr']['neural'][1]
        source_prefix['overlay_occluded'] = True
        source_prefix['overlay_boxes'] = [[522, 850, 535, 865]]
        observation = candidates[0]['observations'][1]
        observation['receipt_parts']['prefix']['overlay_occluded'] = True
        observation['receipt_parts']['prefix']['overlay_boxes'] = [[522, 850, 535, 865]]
        observation['overlay_box'] = [522, 850, 535, 865]
        observation['identity_fragment_proof'] = {
            'basis': 'source_bound_identity_fragment_tail_ocr',
            'recognized_text': 'or Wrapped',
            'fragment': 'Wrapped',
            'confidence': 93.0,
            'crop_box': [538, 828, 731, 884],
            'overlay_box': [522, 850, 535, 865],
            'pixel_rgb_sha256': 'a' * 64,
            'model_fingerprint': 'd' * 64,
        }
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['rejected'], [])
        self.assertEqual(result['accepted'][0]['name'], 'Wrapped Skill')

    def test_particle_only_overlay_keeps_cursor_validated_wrapped_candidate_supported(self):
        """The normal integration pass shares the cache's cursor-only view."""
        events, rows, candidates = wrapped_fixture()
        particle = [[460, 848, 505, 893]]
        for row, observation in zip(rows, candidates[0]['observations']):
            prefix = row['ocr']['neural'][1]
            prefix['source'] = 'occluded_receipt_line'
            row['facts'] = {
                'occluded_receipt_lines': [{
                    **deepcopy(prefix),
                    'confidence': 100.0,
                    'overlay_boxes': deepcopy(particle),
                    'animated_overlay_boxes': deepcopy(particle),
                    'animated_overlay_occluded': True,
                }],
            }
            # The prepared candidate was validated against the cursor-only
            # copy, so its identity proof has no particle obstruction.
            observation['receipt_parts']['prefix']['overlay_boxes'] = []
            observation['receipt_parts']['prefix']['overlay_occluded'] = False
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['rejected'], [])
        self.assertEqual(result['accepted'][0]['amount'], 2)

    def test_stable_overlay_remains_a_wrapped_identity_negative_control(self):
        events, rows, candidates = wrapped_fixture()
        stable = [[522, 850, 535, 865]]
        particle = [[460, 848, 505, 893]]
        for row, observation in zip(rows, candidates[0]['observations']):
            prefix = row['ocr']['neural'][1]
            prefix['source'] = 'occluded_receipt_line'
            row['facts'] = {
                'occluded_receipt_lines': [{
                    **deepcopy(prefix),
                    'confidence': 100.0,
                    'overlay_boxes': [*deepcopy(stable), *deepcopy(particle)],
                    'animated_overlay_boxes': deepcopy(particle),
                    'animated_overlay_occluded': True,
                }],
            }
            observation['receipt_parts']['prefix'].update(
                overlay_boxes=deepcopy(stable), overlay_occluded=True,
            )
            observation['overlay_box'] = deepcopy(stable[0])
        result = apply(events, rows, candidates, source_sha256=SOURCE)
        self.assertEqual(result['accepted'], [])
        self.assertEqual(result['rejected'][0]['reason'], 'candidate_does_not_match_source_rows')
