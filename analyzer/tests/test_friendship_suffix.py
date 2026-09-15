import copy
import json
from pathlib import Path
import unittest

from tracen_replay.transactions import outcome_events


class FriendshipSuffixTests(unittest.TestCase):
    def rows(self):
        return json.loads(Path('analyzer/tests/fixtures/friendship-suffix-1000600.json').read_text(encoding='utf-8'))['rows']

    def resolved(self,rows):
        return [effect for event in outcome_events(rows) for effect in event['effects']
                if effect.get('name_resolution')=='repeated_visible_suffix_covered_in_shorter_reading']

    def test_source_frames_resolve_only_the_cursor_covered_suffix(self):
        rows=self.rows()
        original=copy.deepcopy(rows)
        result=self.resolved(rows)
        self.assertEqual(rows,original)
        self.assertEqual(len(result),1)
        self.assertEqual((result[0]['name'],result[0]['amount']),('Director Akikawa',5))
        variant=result[0]['occluded_name_variants'][0]
        self.assertEqual(variant['effect']['name'],'Director Akikaw')
        anchors=variant['evidence'][0]['anchors']
        self.assertEqual([a['source_timestamp_ms'] for a in anchors],[1000617,1000650])

    def test_names_are_unrestricted(self):
        rows=json.loads(json.dumps(self.rows()).replace('Director','Observer').replace('Akikawa','Example').replace('Akikaw','Exampl'))
        self.assertEqual(self.resolved(rows)[0]['name'],'Observer Example')

    def test_missing_or_wrong_cursor_and_missing_alignment_abstain(self):
        for mutation in ('missing_cursor','wrong_cursor','missing_alignment','covered_anchor'):
            rows=self.rows()
            if mutation=='missing_cursor':rows[0]['facts']['receipt_overlay_evidence']['overlay_boxes']=[]
            elif mutation=='wrong_cursor':rows[0]['facts']['receipt_overlay_evidence']['overlay_boxes']=[[680,830,692,863]]
            elif mutation=='missing_alignment':rows[0]['facts']['receipt_overlay_evidence']['alignments']=[]
            else:rows[-1]['facts']['receipt_overlay_evidence']['overlay_boxes']=[[615,830,628,862]]
            with self.subTest(mutation=mutation):self.assertEqual(self.resolved(rows),[])

    def test_two_distinct_exact_anchors_are_required(self):
        for mutation in ('one_anchor','weak_anchor','duplicate_time','weak_alignment'):
            rows=self.rows()
            if mutation=='one_anchor':rows=rows[:-1]
            elif mutation=='duplicate_time':rows[-1]['source_timestamp_ms']=rows[1]['source_timestamp_ms']
            elif mutation=='weak_alignment':rows[-1]['facts']['receipt_overlay_evidence']['alignments'][0]['confidence']=96
            else:
                next(e for e in rows[-1]['effects'] if e['kind']=='friendship_change')['confidence']=96
            with self.subTest(mutation=mutation):self.assertEqual(self.resolved(rows),[])

    def test_screen_change_scrolling_or_competing_receipt_prevents_resolution(self):
        for mutation in ('screen','scroll','competitor','simultaneous'):
            rows=self.rows()
            if mutation=='screen':rows[2]['screen']='training_result'
            elif mutation=='scroll':
                box=next(l['box'] for l in rows[2]['ocr']['neural'] if l['text'].startswith('Skill Pts'))
                box[1]+=24;box[3]+=24
            elif mutation=='competitor':
                e=copy.deepcopy(rows[1]['effects'][-1]);e['name']='Different Person';rows[2]['effects'].append(e)
            else:rows[0]['effects'].append(copy.deepcopy(rows[1]['effects'][-1]))
            with self.subTest(mutation=mutation):self.assertEqual(self.resolved(rows),[])

    def test_letter_positions_and_same_award_are_required(self):
        for mutation in ('positions','award','line_shift','missing_letter_column'):
            rows=self.rows()
            if mutation=='award':rows[0]['effects'][-1]['amount']=6
            elif mutation=='line_shift':
                next(l for l in rows[0]['ocr']['neural'] if l['text'].startswith('Friendship'))['box'][0]+=10
            elif mutation=='missing_letter_column':rows[0]['facts']['receipt_overlay_evidence']['alignments'][0]['columns'][3].pop()
            else:
                cols=rows[0]['facts']['receipt_overlay_evidence']['alignments'][0]['columns'][3]
                cols[0]+=5
            with self.subTest(mutation=mutation):self.assertEqual(self.resolved(rows),[])

    def test_conflicting_amount_for_same_name_is_not_erased(self):
        rows=self.rows()
        other=copy.deepcopy(rows[1]['effects'][-1]);other['amount']=6
        rows[2]['effects'].append(other)
        self.assertEqual(self.resolved(rows),[])

    def test_missing_cross_frame_and_cross_source_provenance_are_rejected(self):
        for mutation in ('missing','wrong_frame','wrong_source','missing_model','bad_hash'):
            rows=self.rows();metadata=rows[-1]['facts']['receipt_overlay_evidence']
            if mutation=='missing':metadata.pop('provenance')
            elif mutation=='wrong_frame':metadata['provenance']=copy.deepcopy(rows[1]['facts']['receipt_overlay_evidence']['provenance'])
            elif mutation=='wrong_source':metadata['provenance']['source_sha256']='0'*64
            elif mutation=='missing_model':metadata['provenance'].pop('model_sha256')
            else:metadata['provenance']['raw_sha256']='invalid'
            with self.subTest(mutation=mutation):self.assertEqual(self.resolved(rows),[])

    def test_at_least_one_anchor_must_have_zero_name_overlap(self):
        rows=self.rows()
        # Both names are still readable and overlap by less than 3px, but
        # neither is now a wholly clear supporting observation.
        rows[-1]['facts']['receipt_overlay_evidence']['overlay_boxes']=[[628,846,640,860]]
        self.assertEqual(self.resolved(rows),[])
