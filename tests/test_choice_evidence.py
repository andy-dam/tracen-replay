import unittest
import json
from pathlib import Path
from PIL import Image,ImageDraw
from tracen_replay.choice_evidence import observe,reconstruct


class ChoiceEvidenceTests(unittest.TestCase):
    def test_reviewed_candidate_regressions(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/choice-reviewed-candidates-v2.json').read_text(encoding='utf-8'))
        for case in fixture['cases']:
            with self.subTest(timestamp=case['selection_observed_ms']):
                events=reconstruct(case['observations'])
                self.assertEqual([{k:e[k] for k in ('options','selected_index','selected_text')} for e in events],case['expected'])

    def test_source_temporal_regression(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/choice-temporal-regression-v1.json').read_text(encoding='utf-8'))
        events=reconstruct(fixture['observations'])
        self.assertEqual([{k:e[k] for k in ('options','selected_index','selected_text')} for e in events],fixture['expected'])

    def observations(self):
        cards=[dict(text='First',text_box=[317,628,600,656]),dict(text='Second',text_box=[317,740,600,768])]
        rows=[dict(source_timestamp_ms=t,evidence=str(t)+'.png',offered_card_candidates=cards,selection_mark_pairs=[]) for t in (0,250)]
        rows.append(dict(source_timestamp_ms=500,evidence='500.png',offered_card_candidates=[],
                         selection_mark_pairs=[dict(left=dict(box=[258,703,313,736]),right=dict(box=[798,700,853,736]))]))
        return rows

    def test_repeated_menu_and_marks_choose_matching_option(self):
        event=reconstruct(self.observations())[0]
        self.assertEqual(event['selected_text'],'Second')
        self.assertIsNone(event['click_timestamp_ms'])
        self.assertEqual(event['kind'],'dialogue_choice')

    def test_marks_without_recent_repeated_menu_abstain(self):
        rows=self.observations()
        self.assertEqual(reconstruct(rows[1:]),[])
        rows[1]['source_timestamp_ms']=0
        self.assertEqual(reconstruct(rows),[])
        rows=self.observations();rows[-1]['source_timestamp_ms']=2000
        self.assertEqual(reconstruct(rows),[])

    def test_partial_menu_during_collapse_does_not_replace_stable_options(self):
        rows=self.observations();partial=dict(rows[1],source_timestamp_ms=400,offered_card_candidates=rows[1]['offered_card_candidates'][:1])
        self.assertEqual(reconstruct(rows[:2]+[partial]+rows[2:])[0]['selected_text'],'Second')

    def test_single_response_has_distinct_kind(self):
        rows=self.observations()
        for r in rows[:2]:r['offered_card_candidates']=r['offered_card_candidates'][1:]
        self.assertEqual(reconstruct(rows)[0]['kind'],'dialogue_response')

    def test_different_selected_response_invalidates_prior_menu(self):
        rows=self.observations()
        rows[-1]['selected_card_candidates']=[dict(text='A later response',text_box=[310,742,635,768])]
        self.assertEqual(reconstruct(rows),[])

    def test_incomplete_text_does_not_establish_menu(self):
        rows=self.observations()
        for row in rows[:2]:row['menu_text_complete']=False
        self.assertEqual(reconstruct(rows),[])

    def test_wrapped_card_text_is_one_option(self):
        im=Image.new('RGB',(810,1080));draw=ImageDraw.Draw(im)
        draw.rectangle((120,714,685,794),fill='white')
        lines=[dict(text=text,confidence=99,box=box) for text,box in [
            ('I will do my',[315,726,728,758]),('best.',[316,752,369,778])]]
        result=observe(im,lines)
        self.assertEqual([c['text'] for c in result['offered_card_candidates']],['I will do my best.'])
        self.assertTrue(result['menu_text_complete'])

    def test_unreadable_second_card_does_not_look_like_single_response(self):
        im=Image.new('RGB',(810,1080));draw=ImageDraw.Draw(im)
        for y in (603,714):draw.rectangle((120,y,685,y+80),fill='white')
        result=observe(im,[dict(text='First',confidence=99,box=[317,628,600,656])])
        self.assertEqual(len(result['offered_card_candidates']),1)
        self.assertFalse(result['menu_text_complete'])

    def test_green_selected_card_keeps_text_for_consistency_check(self):
        im=Image.new('RGB',(810,1080));ImageDraw.Draw(im).rectangle((120,714,685,794),fill=(200,255,130))
        result=observe(im,[dict(text='Later response',confidence=99,box=[310,742,635,768])])
        self.assertEqual(result['offered_card_candidates'],[])
        self.assertEqual(result['selected_card_candidates'][0]['text'],'Later response')

    def test_requires_gameplay_crop(self):
        with self.assertRaises(ValueError):observe(Image.new('RGB',(1920,1080)))

    def test_single_yellow_badge_is_not_selection(self):
        im=Image.new('RGB',(810,1080));draw=ImageDraw.Draw(im)
        draw.rectangle((115,700,140,725),fill=(255,240,0))
        self.assertEqual(observe(im)['selection_mark_pairs'],[])

    def test_bilateral_marks_are_evidence_not_selected_text(self):
        im=Image.new('RGB',(810,1080));draw=ImageDraw.Draw(im)
        for x in (115,660):draw.rectangle((x,700,x+25,725),fill=(255,240,0))
        result=observe(im)
        self.assertEqual(len(result['selection_mark_pairs']),1)
        self.assertIsNone(result['selected_option'])
        self.assertFalse(result['selection_verified'])

    def test_misaligned_marks_do_not_match(self):
        im=Image.new('RGB',(810,1080));draw=ImageDraw.Draw(im)
        draw.rectangle((115,600,140,625),fill=(255,240,0))
        draw.rectangle((660,700,685,725),fill=(255,240,0))
        self.assertEqual(observe(im)['selection_mark_pairs'],[])

    def test_text_requires_visible_white_card(self):
        im=Image.new('RGB',(810,1080));line=dict(text='An option.',confidence=99,box=[317,740,600,768])
        self.assertEqual(observe(im,[line])['offered_card_candidates'],[])
        ImageDraw.Draw(im).rectangle((120,714,685,794),fill='white')
        self.assertEqual(observe(im,[line])['offered_card_candidates'][0]['text'],'An option.')
