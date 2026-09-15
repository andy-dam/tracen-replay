import copy
import json
import unittest
from pathlib import Path
from tracen_replay.award_tracking import tracked_stats,coexisting_cap_stats


class AwardTrackingTests(unittest.TestCase):
    def cap_rows(self):
        rows=[]
        for t in (0,25,50):
            rows.append(dict(source_timestamp_ms=t,evidence=f'{t}.png',screen='event_outcome',ocr=dict(neural=[
                dict(text='+54',confidence=99,box=[296,443,445,523]),
                dict(text='Stamina',confidence=99,box=[324,519,474,564]),
                dict(text='Stamina cap',confidence=99,box=[464,638,651,688]),
                dict(text='Stamina cap went up by 7.',confidence=99,box=[315,832,600,860])])))
        receipts={'stat_change|stamina|':[(t,f'{t}.png',dict(amount=54,raw_text='Stamina went up by 54.',confidence=99)) for t in (600,650,700)]}
        return rows,receipts

    def test_distinct_stat_and_cap_badges_require_repeated_stat_receipts(self):
        rows,receipts=self.cap_rows();got=coexisting_cap_stats(rows,0,1000,receipts)
        self.assertEqual([c['amount'] for _,c in got],[54,54,54])
        self.assertIsNone(got[0][1]['receipt_text'])
        self.assertEqual(len(got[0][1]['receipt_anchors']),3)
        self.assertEqual(got[0][1]['cap_disambiguation']['raw_text'],'Stamina cap')
        self.assertEqual(coexisting_cap_stats(rows,0,1000,{}),[])
        self.assertEqual(coexisting_cap_stats(rows,0,100,receipts),[])
        duplicate_times={'stat_change|stamina|':[(600,p,e) for _,p,e in receipts['stat_change|stamina|']]}
        self.assertEqual(coexisting_cap_stats(rows,0,1000,duplicate_times),[])
        for _,_,e in receipts['stat_change|stamina|']:e['amount']=7
        self.assertEqual(coexisting_cap_stats(rows,0,1000,receipts),[])

    def test_missing_or_overlapping_cap_badge_is_not_disambiguated(self):
        for mode in ('missing','overlap','low_confidence'):
            rows,receipts=self.cap_rows()
            for r in rows:
                lines=r['ocr']['neural']
                if mode=='missing':lines.pop(2)
                elif mode=='overlap':lines[2]['box']=lines[1]['box']
                else:lines[2]['confidence']=96
            self.assertEqual(coexisting_cap_stats(rows,0,1000,receipts),[])

    def test_cap_only_display_and_distant_receipts_are_not_stat_awards(self):
        rows,receipts=self.cap_rows()
        for r in rows:r['ocr']['neural'].pop(1)
        self.assertEqual(coexisting_cap_stats(rows,0,1000,receipts),[])
        rows,receipts=self.cap_rows();receipts['stat_change|stamina|']=[(t+2000,p,e) for t,p,e in receipts['stat_change|stamina|']]
        self.assertEqual(coexisting_cap_stats(rows,0,3000,receipts),[])

    def test_source_mixed_stat_and_cap_awards(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/stat-cap-coexistence-v1.json').read_text(encoding='utf-8'))
        got=coexisting_cap_stats(fixture['readings'],522250,523500,fixture['receipts'])
        self.assertEqual([(r['source_timestamp_ms'],c['field'],c['amount']) for r,c in got],
                         [tuple(x) for x in fixture['expected']])

    def rows(self):
        return [dict(source_timestamp_ms=t,evidence=f'{t}.png',screen='event_outcome',ocr=dict(neural=[
            dict(text='+57',confidence=99 if t<75 else 90,box=[468,562,618,643]),
            dict(text='SkillPts' if t in (0,75) else 'Skill-Pts',confidence=99 if t in (0,75) else 91,box=[495,633,650,689]),
            dict(text='Skill Pts went up by 57.',confidence=99,box=[315,900,620,930])])) for t in (0,25,50,75)]

    def test_two_label_anchors_support_repeated_visible_values(self):
        result=tracked_stats(self.rows(),0,75)
        self.assertEqual([(r['source_timestamp_ms'],c['field'],c['amount']) for r,c in result],[(0,'skill_points',57),(25,'skill_points',57),(50,'skill_points',57)])
        self.assertEqual(len(result[1][1]['label_anchors']),2)
        self.assertEqual(result[1][1]['confidence'],99)

    def test_no_single_anchor_or_unbracketed_values(self):
        rows=self.rows();rows[-1]['ocr']['neural'][1]['confidence']=90
        self.assertEqual(tracked_stats(rows,0,75),[])
        rows=self.rows();rows[0]['ocr']['neural'][0]['confidence']=90
        self.assertEqual(tracked_stats(rows,0,75),[])

    def test_different_amount_or_label_interrupts_tracking(self):
        for text,index in (('+37',0),('Stamina',1)):
            rows=self.rows();rows[1]['ocr']['neural'][index]['text']=text
            self.assertEqual(tracked_stats(rows,0,75),[])

    def test_ambiguous_gain_cannot_be_hidden_by_other_agreeing_frames(self):
        rows=self.rows();extra=copy.deepcopy(rows[2]);extra['source_timestamp_ms']=62;rows.insert(3,extra)
        conflict=copy.deepcopy(rows[1]['ocr']['neural'][0]);conflict['text']='+37'
        rows[1]['ocr']['neural'].append(conflict)
        self.assertEqual(tracked_stats(rows,0,75),[])

    def test_moving_label_or_screen_boundary_rejects_track(self):
        rows=self.rows();rows[1]['ocr']['neural'][1]['box'][1]+=12
        self.assertEqual(tracked_stats(rows,0,75),[])
        rows=self.rows();rows[1]['screen']='training_preview'
        self.assertEqual(tracked_stats(rows,0,75),[])

    def test_duplicate_timestamps_and_long_gaps_do_not_support_tracking(self):
        rows=self.rows();rows[1]['source_timestamp_ms']=0;rows[3]['source_timestamp_ms']=50
        self.assertEqual(tracked_stats(rows,0,75),[])
        rows=self.rows();rows[-1]['source_timestamp_ms']=400
        self.assertEqual(tracked_stats(rows,0,400),[])

    def test_far_apart_anchors_do_not_license_continuous_weak_labels(self):
        template=self.rows()[1];rows=[]
        for t in range(0,301,25):
            r=copy.deepcopy(template);r['source_timestamp_ms']=t
            if t in (0,300):r['ocr']['neural'][1].update(text='SkillPts',confidence=99)
            rows.append(r)
        self.assertEqual(tracked_stats(rows,0,300),[])

    def test_award_caption_required_and_caps_excluded(self):
        for text in ('Skill Pts cap went up by 57.','Skill Pts Bonus went up by 57.','Unrelated dialogue'):
            rows=self.rows()
            for r in rows:r['ocr']['neural'][-1]['text']=text
            self.assertEqual(tracked_stats(rows,0,75),[])

    def test_source_bracketed_animation(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/award-label-tracking-v1.json').read_text(encoding='utf-8'))
        result=tracked_stats(fixture['readings'],1480750,1481500)
        self.assertEqual([(r['source_timestamp_ms'],c['field'],c['amount']) for r,c in result],
                         [tuple(v) for v in fixture['expected']])
