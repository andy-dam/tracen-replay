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


if __name__=='__main__':unittest.main()
