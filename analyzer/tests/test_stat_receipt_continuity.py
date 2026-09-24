import copy
import unittest
from tracen_replay.receipt_stat_continuity import collapse_cross_event_stat_duplicates


class StatReceiptContinuityTests(unittest.TestCase):
    def test_repeated_terminal_punctuation_does_not_duplicate_an_award(self):
        events, rows = self.fixture()
        rows[-1]['ocr']['neural'][0]['text'] = 'Skill Pts went up by 4..'
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(events[1]['effects'], [])

    def fixture(self,field='skill_points',label='Skill Pts',amount=4):
        effect=dict(kind='stat_change',field=field,amount=amount,raw_text=f'{label} went up by {amount}.')
        rows=[]
        for t,text,confidence,explicit in (
            (1000,effect['raw_text'],99,True),
            (1250,f'{label} weby {amount}.',94,False),
            (1500,f'{label} went up by.',99,False),
            (1750,effect['raw_text'],99,True)):
            rows.append(dict(source_timestamp_ms=t,evidence=f'{t}.png',screen='event_outcome',facts={},
                effects=[copy.deepcopy(effect)] if explicit else [],ocr=dict(neural=[dict(text=text,confidence=confidence,box=[315,805,530,837])]),context_title=None))
        key=f'stat_change|{field}|'
        events=[dict(id=f'event-{i}',first_seen_ms=t,last_seen_ms=t,context_title=None,
                     effects=[copy.deepcopy(effect)],field_evidence={key:[f'{t}.png']},conflicting_readings=[])
                for i,t in enumerate((1000,1750))]
        return events,rows

    def test_persistent_stat_receipt_counts_once_without_filling_gap_values(self):
        for field,label in (('skill_points','Skill Pts'),('power','Power')):
            events,rows=self.fixture(field,label)
            # Lost glyphs can shorten the OCR box without moving the row.
            rows[2]['ocr']['neural'][0]['box']=[316,807,528,835]
            events[1]['effects'].append(dict(kind='friendship_change',name='Example Person',amount=5))
            before=copy.deepcopy(rows)
            collapse_cross_event_stat_duplicates(events,rows)
            self.assertEqual([e['kind'] for e in events[1]['effects']],['friendship_change'])
            self.assertEqual(events[0]['effects'][0]['amount'],4)
            self.assertEqual(rows,before)
            track=events[1]['deduplicated_receipt_effects'][0]['track_evidence']
            self.assertFalse(track[2]['amount_observed'])
            self.assertTrue(all(x['accepted_as_effect'] is False for x in track))

    def test_source_bound_occlusion_fact_can_bridge_a_hidden_middle_frame(self):
        events, rows = self.fixture()
        middle = rows[2]['ocr']['neural'][0]
        middle['overlay_occluded'] = True
        middle['confidence'] = 0
        rows[2]['facts']['occluded_receipt_lines'] = [{
            'text': 'Skill Pts went up by 4.',
            'box': [315, 805, 530, 837],
            'confidence': 97,
            'overlay_boxes': [[410, 815, 430, 840]],
        }]
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(events[1]['effects'], [])
        track = events[1]['deduplicated_receipt_effects'][0]['track_evidence']
        self.assertEqual(track[2]['source'], 'source_occlusion')

    def test_same_box_amount_conflict_cannot_be_hidden_by_a_valid_neural_line(self):
        events, rows = self.fixture()
        rows[1]['ocr']['neural'][0]['text'] = 'Skill Pts went up by 4.'
        rows[1]['ocr']['neural'].append({
            'text': 'Skill Pts went up by 9.',
            'confidence': 99,
            'box': [315, 805, 530, 837],
        })
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_same_box_direction_conflict_cannot_be_hidden_by_a_valid_neural_line(self):
        events, rows = self.fixture()
        rows[1]['ocr']['neural'][0]['text'] = 'Skill Pts went up by 4.'
        rows[1]['ocr']['neural'].append({
            'text': 'Skill Pts went down by 4.',
            'confidence': 99,
            'box': [315, 805, 530, 837],
        })
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_same_box_source_conflict_cannot_be_hidden_by_a_valid_neural_line(self):
        events, rows = self.fixture()
        rows[1]['ocr']['neural'][0]['text'] = 'Skill Pts went up by 4.'
        rows[1]['facts']['occluded_receipt_lines'] = [{
            'text': 'Skill Pts went up by 9.',
            'confidence': 99,
            'box': [315, 805, 530, 837],
            'overlay_boxes': [[410, 815, 430, 840]],
        }]
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_a_line_covered_on_every_frame_between_and_restored_by_the_reread_counts_once(self):
        # A sparkle over the amount from the second frame on: every frame
        # between records the line under it, and the dense reread of a later
        # frame restores it as a new event's receipt, 1.3 s after the first.
        effect = dict(kind='stat_change', field='wit', amount=10, raw_text='Wit went up by 10.')
        box = [315, 805, 504, 837]
        rows = [dict(source_timestamp_ms=450500, evidence='450500.png', screen='event_outcome', facts={}, context_title='Chores',
                     effects=[copy.deepcopy(effect)], ocr=dict(neural=[dict(text=effect['raw_text'], confidence=99.9, box=list(box))]))]
        for t in (450750, 451000, 451250, 451500, 451750):
            rows.append(dict(source_timestamp_ms=t, evidence=f'{t}.png', screen='unknown', effects=[], context_title='Chores',
                             ocr=dict(neural=[dict(text=effect['raw_text'], confidence=0, box=list(box), overlay_occluded=True)]),
                             facts=dict(occluded_receipt_lines=[dict(text=effect['raw_text'], box=list(box), confidence=99.9,
                                                                     overlay_boxes=[[486, 816, 490, 824]])])))
        restored = dict(effect, source_bound_receipt_proof=dict(line_box=list(box), confidence=99.7))
        rows.append(dict(source_timestamp_ms=451800, evidence='reread-451800.png', screen='event_outcome', facts={}, context_title='Chores',
                         effects=[copy.deepcopy(restored)],
                         ocr=dict(neural=[dict(text=effect['raw_text'], confidence=0, box=list(box), overlay_occluded=True)])))
        key = 'stat_change|wit|'
        events = [dict(id='event-0', first_seen_ms=450500, last_seen_ms=451000, context_title='Chores',
                       effects=[copy.deepcopy(effect)], field_evidence={key: ['450500.png']}, conflicting_readings=[]),
                  dict(id='event-1', first_seen_ms=451750, last_seen_ms=452000, context_title='Chores',
                       effects=[copy.deepcopy(restored)], field_evidence={key: ['reread-451800.png']}, conflicting_readings=[])]
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(events[1]['effects'], [])
        proof = events[1]['deduplicated_receipt_effects'][0]
        self.assertEqual(proof['basis'], 'stationary_stat_receipt_across_covered_line')
        self.assertEqual([t['source'] for t in proof['track_evidence']],
                         ['ocr'] + ['source_occlusion'] * 5 + ['source_restored'])
        # A frame between that lost the line breaks the chain.
        events[1]['effects'] = [copy.deepcopy(restored)]
        del events[1]['deduplicated_receipt_effects']
        rows[3]['facts'] = {}
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_unproven_occlusion_fact_cannot_bridge_a_hidden_middle_frame(self):
        events, rows = self.fixture()
        middle = rows[2]['ocr']['neural'][0]
        middle['overlay_occluded'] = True
        middle['confidence'] = 0
        rows[2]['facts']['occluded_receipt_lines'] = [{
            'text': 'Skill Pts went up by 4.',
            'box': [315, 805, 530, 837],
            'confidence': 97,
            'overlay_boxes': [],
        }]
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_boundaries_and_contradictions_prevent_suppression(self):
        for change in ('amount','visible_amount','grammar','field','title','screen','narrative','missing_frame','duplicate_slot','motion','no_strong_middle','conflict','candidate_conflict'):
            events,rows=self.fixture()
            if change=='amount':events[1]['effects'][0]['amount']=5
            elif change=='visible_amount':rows[1]['ocr']['neural'][0]['text']='Skill Pts weby 8.'
            elif change=='grammar':rows[1]['ocr']['neural'][0]['text']='Skill Pts will help us win.'
            elif change=='field':rows[1]['ocr']['neural'][0]['text']='Power weby 4.'
            elif change=='title':rows[1]['context_title']='Another Event'
            elif change=='screen':rows[1]['screen']='training_preview'
            elif change=='narrative':rows[1]['ocr']['neural'].append(dict(text='We should try something different today.',confidence=99,box=[315,838,730,868]))
            elif change=='missing_frame':rows.pop(1)
            elif change=='duplicate_slot':rows[1]['ocr']['neural'].append(copy.deepcopy(rows[1]['ocr']['neural'][0]))
            elif change=='motion':rows[1]['ocr']['neural'][0]['box'][1:4:2]=[845,877]
            elif change=='no_strong_middle':rows[2]['ocr']['neural'][0]['confidence']=94
            elif change=='conflict':events[0]['conflicting_readings']=[dict(field='stat_change|skill_points|')]
            elif change=='candidate_conflict':rows[1]['facts']['effect_candidates']=[dict(kind='stat_change',field='skill_points',amount=5)]
            collapse_cross_event_stat_duplicates(events,rows)
            with self.subTest(change=change):self.assertEqual(len(events[1]['effects']),1)

    def sampled(self, middle, times=(1000, 1267, 1533), label='Skill Pts', amount=4):
        """Two reads of one receipt 4 frames a second apart on a 30 fps recording, one frame between."""
        effect = dict(kind='stat_change', field='skill_points', amount=amount, raw_text=f'{label} went up by {amount}.')
        full = [315, 805, 531, 837]
        middle_box = middle[2] if len(middle) > 2 else full
        texts = [(effect['raw_text'], 96, True, full), (*middle[:2], False, middle_box), (effect['raw_text'], 97, True, full)]
        rows = [dict(source_timestamp_ms=t, evidence=f'{t}.png', screen='event_outcome', facts={}, context_title=None,
                     effects=[copy.deepcopy(effect)] if explicit else [],
                     ocr=dict(neural=[dict(text=text, confidence=confidence, box=list(box))]))
                for t, (text, confidence, explicit, box) in zip(times, texts)]
        key = 'stat_change|skill_points|'
        events = [dict(id=f'event-{i}', first_seen_ms=t, last_seen_ms=t, context_title=None,
                       effects=[copy.deepcopy(effect)], field_evidence={key: [f'{t}.png']}, conflicting_readings=[])
                  for i, t in enumerate((times[0], times[-1]))]
        return events, rows

    def test_one_frame_cut_short_between_two_reads_counts_once(self):
        # The box ends where the missing amount would start.
        events, rows = self.sampled(('Skill Pts went up by', 97.6, [315, 805, 509, 836]))
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(events[1]['effects'], [])

    def test_a_cut_line_must_still_start_in_the_same_place(self):
        events, rows = self.sampled(('Skill Pts went up by', 97.6, [335, 805, 509, 836]))
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_one_whole_line_just_under_parse_confidence_counts_once(self):
        events, rows = self.sampled(('Skill Pts went up by 4.', 94.7))
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(events[1]['effects'], [])
        track = events[1]['deduplicated_receipt_effects'][0]['track_evidence']
        self.assertEqual([x['source_timestamp_ms'] for x in track], [1000, 1267, 1533])

    def test_a_whole_line_at_parse_confidence_between_two_reads_is_no_proof(self):
        events, rows = self.sampled(('Skill Pts went up by 4.', 99))
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_a_skipped_sample_breaks_the_chain(self):
        events, rows = self.sampled(('Skill Pts went up by 4.', 94.7), times=(1000, 1267, 1600))
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual(len(events[1]['effects']), 1)

    def test_unchanged_complete_receipts_and_long_gaps_are_not_enough(self):
        events,rows=self.fixture()
        for row in rows:row['ocr']['neural'][0]['text']='Skill Pts went up by 4.'
        collapse_cross_event_stat_duplicates(events,rows)
        self.assertEqual(len(events[1]['effects']),1)
        events,rows=self.fixture()
        rows[-1]['source_timestamp_ms']=2250
        events[-1]['first_seen_ms']=events[-1]['last_seen_ms']=2250
        collapse_cross_event_stat_duplicates(events,rows)
        self.assertEqual(len(events[1]['effects']),1)

    def test_reordered_reused_or_invalid_observations_cannot_prove_continuity(self):
        for change in ('reordered','duplicate_time','duplicate_endpoint','reused_evidence','nan','infinity','malformed_line'):
            events,rows=self.fixture()
            if change=='reordered':rows[1],rows[2]=rows[2],rows[1]
            elif change in ('duplicate_time','duplicate_endpoint'):
                duplicate=copy.deepcopy(rows[1 if change=='duplicate_time' else 0])
                duplicate['evidence']='different-path.png'
                rows.insert(1,duplicate)
            elif change=='reused_evidence':rows[2]['evidence']=rows[1]['evidence']
            elif change=='nan':rows[1]['source_timestamp_ms']=float('nan')
            elif change=='infinity':rows[1]['source_timestamp_ms']=float('inf')
            elif change=='malformed_line':rows[1]['ocr']['neural'].append(None)
            collapse_cross_event_stat_duplicates(events,rows)
            with self.subTest(change=change):self.assertEqual(len(events[1]['effects']),1)

    def blank_fixture(self):
        """One box read twice around a frame the award's animation blanked."""
        effect = dict(kind='stat_change', field='skill_points', amount=5, raw_text='Skill Pts went up by 5.')
        friendship = dict(kind='friendship_status', name='Director Akikawa', value='maximum',
                          raw_text='Friendship with Director Akikawa is maxed out.')
        rows = [
            dict(source_timestamp_ms=1000, evidence='1000.png', screen='event_outcome', facts={}, effects=[copy.deepcopy(effect)],
                 ocr=dict(neural=[dict(text=effect['raw_text'], confidence=97.7, box=[316, 806, 532, 836])]), context_title=None),
            dict(source_timestamp_ms=1267, evidence='1267.png', screen='unknown', facts={}, effects=[],
                 ocr=dict(neural=[dict(text='Career', confidence=99.9, box=[20, 10, 80, 30])]), context_title=None),
            dict(source_timestamp_ms=1533, evidence='1533.png', screen='event_outcome', facts={},
                 effects=[copy.deepcopy(effect), copy.deepcopy(friendship)],
                 ocr=dict(neural=[dict(text=effect['raw_text'], confidence=97.5, box=[316, 805, 530, 835]),
                                  dict(text='Friendship with Director Akikawa is maxed out.', confidence=98.7, box=[316, 830, 766, 859])]),
                 context_title=None)]
        key = 'stat_change|skill_points|'
        events = [dict(id='event-0', first_seen_ms=1000, last_seen_ms=1000, context_title=None,
                       effects=[copy.deepcopy(effect)], field_evidence={key: ['1000.png']}, conflicting_readings=[]),
                  dict(id='event-1', first_seen_ms=1533, last_seen_ms=1533, context_title=None,
                       effects=[copy.deepcopy(effect), copy.deepcopy(friendship)], field_evidence={key: ['1533.png']}, conflicting_readings=[])]
        return events, rows

    def test_one_blank_frame_under_the_awards_animation_bridges_the_same_line(self):
        events, rows = self.blank_fixture()
        before = copy.deepcopy(rows)
        collapse_cross_event_stat_duplicates(events, rows)
        self.assertEqual([e['kind'] for e in events[1]['effects']], ['friendship_status'])
        proof = events[1]['deduplicated_receipt_effects'][0]
        self.assertEqual(proof['basis'], 'stationary_stat_receipt_across_blank_frame')
        self.assertTrue(proof['track_evidence'][1]['blank'])
        self.assertEqual(rows, before)

    def test_a_blank_frame_bridges_nothing_unless_alone_blank_and_on_the_same_pixels(self):
        with self.subTest('two blank frames'):
            events, rows = self.blank_fixture()
            rows.insert(2, dict(source_timestamp_ms=1400, evidence='1400.png', screen='unknown', facts={}, effects=[],
                                ocr=dict(neural=[]), context_title=None))
            rows[3].update(source_timestamp_ms=1667, evidence='1667.png')
            events[1].update(first_seen_ms=1667, last_seen_ms=1667, field_evidence={'stat_change|skill_points|': ['1667.png']})
            collapse_cross_event_stat_duplicates(events, rows)
            self.assertEqual(len(events[1]['effects']), 2)
        with self.subTest('another line in the band'):
            events, rows = self.blank_fixture()
            rows[1]['ocr']['neural'].append(dict(text='Some other line entirely.', confidence=99, box=[316, 806, 532, 836]))
            collapse_cross_event_stat_duplicates(events, rows)
            self.assertEqual(len(events[1]['effects']), 2)
        with self.subTest('the line back on other pixels'):
            events, rows = self.blank_fixture()
            rows[2]['ocr']['neural'][0]['box'] = [316, 840, 530, 870]
            collapse_cross_event_stat_duplicates(events, rows)
            self.assertEqual(len(events[1]['effects']), 2)
        with self.subTest('a frame of another screen'):
            events, rows = self.blank_fixture()
            rows[1]['screen'] = 'training_result'
            collapse_cross_event_stat_duplicates(events, rows)
            self.assertEqual(len(events[1]['effects']), 2)


if __name__=='__main__':unittest.main()
