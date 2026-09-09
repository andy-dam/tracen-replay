import hashlib
import unittest
from PIL import Image,ImageDraw
from tests.test_neural_transactions import raw,line
from tracen_replay.receipt_occlusion import annotate,overlay_boxes,numeric_bounds,annotate_path
from tracen_replay.vision import parse
from tracen_replay.transactions import outcome_events


class ReceiptOcclusionTests(unittest.TestCase):
    def test_omitted_recipient_suffix_still_lies_inside_protected_name_span(self):
        from unittest.mock import patch
        from tracen_replay.receipt_occlusion import friendship_name_bounds
        # Source-backed alignment at262500ms: OCR omits letters beneath the
        # cursor, leaving a gap between "Otonas" and the following "went".
        box=[314,828,759,862]
        words=['Friendship','with','Etsuko','Otonas','went','up','by','7.']
        columns=[[2,3,5,6,8,10,12,14,15,17],[20,22,23,25],[28,29,31,33,35,37],
                 [40,43,44,46,48,50],[56,58,60,62],[65,67],[70,72],[75,76]]
        bounds=friendship_name_bounds(box,words,columns,78)
        self.assertGreater(bounds[2],620)
        self.assertLess(bounds[2],314+56*445/78)
        pane=Image.new('RGB',(810,1080),'white')
        source=raw([line('Friendship with Etsuko Otonash went up by 7.',box)])
        source['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        source['overlay_alignment']=[dict(line_box=box,words=words,columns=columns,line_length=78,confidence=95.63)]
        with patch('tracen_replay.receipt_occlusion.overlay_boxes',return_value=[[608,830,620,846]]):
            marked=annotate(source,pane)
        self.assertEqual(parse(marked)['effects'],[])
        self.assertTrue(marked['occluded_receipt_lines'][0]['recipient_name_occluded'])

    def test_recipient_obstruction_is_not_excused_by_clear_amount(self):
        from unittest.mock import patch
        pane=Image.new('RGB',(810,1080),'white')
        source=raw([line('Friendship with Example Name went up by 7.',(300,820,740,850))])
        source['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        words=['Friendship','with','Example','Name','went','up','by','7.']
        columns=[[1,2],[4,5],[7,8,9],[11,12],[14,15],[17,18],[20,21],[24,25]]
        source['overlay_alignment']=[dict(line_box=[300,820,740,850],words=words,columns=columns,line_length=27,confidence=99)]
        with patch('tracen_replay.receipt_occlusion.overlay_boxes',return_value=[[445,824,458,846]]):
            marked=annotate(source,pane)
            self.assertEqual(parse(marked)['effects'],[])
            self.assertTrue(marked['occluded_receipt_lines'][0]['recipient_name_occluded'])
        self.assertEqual(source['lines'][0]['confidence'],99)

    def test_numeric_alignment_includes_hidden_leading_digit_gap(self):
        box=numeric_bounds([317,856,542,882],['Skill','Pts','went','up','by','7.'],
                           [[2,4,6,7,9],[12,14,16],[20,23,26,28],[32,34],[38,41],[48,50]],52)
        self.assertLess(box[0],510)
        self.assertGreater(box[0],494)
        self.assertLess(box[2],538)
        self.assertGreater(box[2],524)
        punctuation=numeric_bounds([314,829,562,864],['Skill','Pts','went','up','by','10.'],
                                   [[1,3,4,6,8],[10,11,13],[16,18,19,21],[24,26],[29,31],[34,36,38]],42)
        self.assertLess(punctuation[2],546)
        self.assertIsNone(numeric_bounds([0,0,100,20],['by','7.'],[[4],[5]],0))
        self.assertIsNone(numeric_bounds([0,0,100,20],['by','7.'],[[4],[25]],20))
        self.assertIsNone(numeric_bounds([0,0,100,20],['by','?'],[[4],[5]],20))

    def test_cursor_on_prefix_keeps_separately_localized_numeric_phrase(self):
        source,pane=self.sample()
        source['overlay_alignment']=[dict(line_box=source['lines'][0]['box'],numeric_box=[535,852,560,879])]
        reading=parse(annotate(source,pane))
        self.assertEqual(next(e['amount'] for e in reading['effects'] if e.get('field')=='skill_points'),7)
        source['overlay_alignment'][0]['numeric_box']=[500,852,560,879]
        self.assertFalse(any(e.get('field')=='skill_points' for e in parse(annotate(source,pane))['effects']))

    def test_fan_amount_excludes_the_unit_label(self):
        box=numeric_bounds([316,901,518,930],['Gained','2000','fans.'],
                           [[1,3,5,7,9,11],[14,16,18,20],[24,26,28,30,32]],34)
        self.assertLess(box[2],499)
        self.assertGreater(box[0],380)
        self.assertIsNone(numeric_bounds([316,901,518,930],['Gained','2000','hints.'],
                                        [[1],[14,16,18,20],[24]],34))

    def test_alignment_tampering_is_rejected(self):
        import json
        from tests.test_gameplay import workspace_temp
        from tracen_replay.refine_contrast import fingerprint
        with workspace_temp() as directory:
            path=directory/'proof.png';source,pane=self.sample();pane.save(path)
            extra=dict(raw_sha256=fingerprint(source),evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),lines=[])
            path.with_suffix('.overlay.json').write_text(json.dumps(extra),encoding='utf-8')
            annotate_path(source,path)
            extra['raw_sha256']='changed'
            path.with_suffix('.overlay.json').write_text(json.dumps(extra),encoding='utf-8')
            with self.assertRaises(ValueError):annotate_path(source,path)

    def sample(self,covered=True):
        pane=Image.new('RGB',(810,1080),'white')
        if covered:ImageDraw.Draw(pane).polygon([(364,860),(373,866),(369,866),(370,872),(367,873)],fill=(100,190,60))
        observation=raw([line('Skill Pts went up by 7.',(315,852,560,879)),
                         line('Wit went up by 12.',(315,820,550,845))])
        observation['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        return observation,pane

    def test_capture_identity_is_retained_and_cannot_override_a_different_source(self):
        import json
        from tests.test_gameplay import workspace_temp
        from tracen_replay.refine_contrast import fingerprint
        with workspace_temp() as directory:
            path=directory/'proof.png';source,pane=self.sample();pane.save(path)
            source.update(evidence='proof.png',source_timestamp_ms=100,source_frame_sha256='b'*64)
            def sidecar():
                path.with_suffix('.overlay.json').write_text(json.dumps(dict(
                    raw_sha256=fingerprint(source),evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    model_sha256={'recognizer':'c'*64},lines=[])),encoding='utf-8')
            sidecar()
            marked=annotate_path(source,path,source_sha256='a'*64)
            provenance=marked['receipt_overlay_evidence']['provenance']
            self.assertEqual(provenance['source_sha256'],'a'*64)
            self.assertEqual(provenance['source_timestamp_ms'],100)
            self.assertEqual(provenance['model_sha256'],{'recognizer':'c'*64})
            self.assertNotIn('source_sha256',source)
            source['source_sha256']='d'*64;sidecar()
            with self.assertRaises(ValueError):annotate_path(source,path,source_sha256='a'*64)

    def test_obstruction_abstains_without_replacing_digit_or_modifying_source(self):
        source,pane=self.sample();marked=annotate(source,pane);reading=parse(marked)
        self.assertEqual(source['lines'][0]['confidence'],99)
        self.assertEqual([(e['field'],e['amount']) for e in reading['effects']],[('wit',12)])
        self.assertEqual(reading['facts']['occluded_receipt_lines'][0]['text'],'Skill Pts went up by 7.')
        self.assertTrue(marked['lines'][0]['overlay_occluded'])

    def test_unobstructed_receipt_is_not_replaced_by_ledger_guess(self):
        source,pane=self.sample(False)
        self.assertEqual(next(e['amount'] for e in parse(annotate(source,pane))['effects'] if e['field']=='skill_points'),7)
        self.assertEqual(overlay_boxes(pane),[])
        ImageDraw.Draw(pane).rectangle((300,850,450,890),fill=(100,190,60))
        self.assertEqual(overlay_boxes(pane),[])

    def test_changed_pixels_and_wrong_crop_are_rejected(self):
        source,pane=self.sample();pane.putpixel((0,0),(0,0,0))
        with self.assertRaises(ValueError):annotate(source,pane)
        with self.assertRaises(ValueError):overlay_boxes(Image.new('RGB',(1920,1080)))

    def test_neighboring_line_is_not_obscured_by_padding_alone(self):
        source,pane=self.sample()
        source['lines'].append(line('Gained 1 hint level(s) for Firm Conditions.',(315,874,740,900)))
        reading=parse(annotate(source,pane))
        self.assertTrue(any(e['kind']=='skill_hint_change' for e in reading['effects']))

    def test_cursor_below_glyph_center_does_not_erase_readable_amount(self):
        source,pane=self.sample()
        source['lines'][0]['box']=[315,832,560,866]
        reading=parse(annotate(source,pane))
        self.assertTrue(any(e.get('field')=='skill_points' and e['amount']==7 for e in reading['effects']))

    def test_cursor_over_nonnumeric_friendship_status_abstains_on_full_line(self):
        source,pane=self.sample()
        source['lines'][0]['text']="Friendship with Agnes Digital didn't go up."
        marked=annotate(source,pane)
        self.assertFalse(any(e['kind']=='friendship_status' for e in parse(marked)['effects']))
        self.assertTrue(marked['lines'][0]['overlay_occluded'])
        self.assertFalse(marked['occluded_receipt_lines'][0]['recipient_name_occluded'])

    def test_padded_views_can_recover_a_covered_leading_digit(self):
        source,pane=self.sample();l=source['lines'][0];l['text']='Energy went down by18.'
        l['receipt_crop_views']=[dict(text=l['text'],confidence=99) for _ in range(3)]
        source['overlay_alignment']=[dict(line_box=l['box'],numeric_box=[500,852,560,879],
                                          recognized_text='Energy went down by8.',confidence=99)]
        reading=parse(annotate(source,pane))
        self.assertTrue(any(e['kind']=='energy_change' and e['amount']==-18 for e in reading['effects']))
        self.assertEqual(reading['facts']['resolved_receipt_occlusions'][0]['independent_frame_count'],1)
        source['overlay_alignment'][0]['recognized_text']='Energy went down by18.'
        self.assertFalse(any(e['kind']=='energy_change' for e in parse(annotate(source,pane))['effects']))
        source['overlay_alignment'][0]['recognized_text']='Energy went down by8.'
        l['receipt_crop_views'][0]['text']='Energy went down by8.'
        self.assertFalse(any(e['kind']=='energy_change' for e in parse(annotate(source,pane))['effects']))

    def test_only_uncovered_frames_supply_numeric_evidence(self):
        source,pane=self.sample();covered=parse(annotate(source,pane))
        clear=parse(raw([line('Skill Pts went up by 57.')]))
        rows=[dict(reading,source_timestamp_ms=t,evidence=f'{t}.png')
              for t,reading in ((100,clear),(350,clear),(600,covered),(850,covered))]
        event=outcome_events(rows)[0]
        self.assertEqual(event['deltas']['skill_points'],57)
        self.assertEqual(event['field_evidence']['stat_change|skill_points|'],['100.png','350.png'])
        self.assertEqual(event['conflicting_readings'],[])
