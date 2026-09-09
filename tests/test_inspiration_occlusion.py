"""Inheritance receipts must obey the same pixel obstruction gate as awards."""
import hashlib
import unittest
from unittest.mock import patch

from PIL import Image
from tests.test_neural_transactions import raw, line
from tracen_replay.receipt_occlusion import annotate, inspiration_name_bounds, receipt_line
from tracen_replay.vision import parse


class InspirationOcclusionTests(unittest.TestCase):
    def observation(self, text='Inspired by Example Name!'):
        pane=Image.new('RGB',(810,1080),'white')
        source=raw([line(text,[300,820,600,850])])
        source['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        return source,pane

    def mark(self,source,pane,boxes):
        with patch('tracen_replay.receipt_occlusion.overlay_boxes',return_value=boxes):
            return annotate(source,pane)

    def test_name_obstruction_abstains_without_guessing_a_replacement(self):
        source,pane=self.observation('Inspired by Seiun S!')
        marked=self.mark(source,pane,[[520,830,534,849]])
        self.assertEqual(parse(marked)['effects'],[])
        self.assertTrue(marked['lines'][0]['overlay_occluded'])
        self.assertEqual(marked['occluded_receipt_lines'][0]['text'],'Inspired by Seiun S!')
        self.assertEqual(source['lines'][0]['confidence'],99)

    def test_cursor_on_adjacent_line_does_not_claim_confetti_detection(self):
        # Actual source geometry at 1100500 ms: confetti corrupts this name,
        # but the detected cursor lies below it. This gate cannot resolve it.
        source,pane=self.observation('Inspired by Mihono Sourbon!')
        source['lines'][0]['box']=[318,836,596,863]
        marked=self.mark(source,pane,[[528,867,540,884]])
        effects=parse(marked)['effects']
        self.assertEqual([e['name'] for e in effects],['Mihono Sourbon'])
        self.assertNotIn('overlay_occluded',marked['lines'][0])

    def test_clear_unknown_name_is_preserved_without_a_character_catalog(self):
        source,pane=self.observation()
        effects=parse(self.mark(source,pane,[]))['effects']
        self.assertEqual([e['name'] for e in effects],['Example Name'])
        self.assertFalse(receipt_line(line('Inspired by Example Name!',[300,100,600,130])))
        self.assertFalse(receipt_line(line('I was inspired by someone.',[300,820,600,850])))

    def test_verified_alignment_can_separate_prefix_from_name(self):
        source,pane=self.observation()
        words=['Inspired','by','Example','Name!']
        columns=[list(range(1,9)),[11,12],list(range(15,22)),list(range(24,29))]
        alignment=dict(line_box=source['lines'][0]['box'],words=words,columns=columns,
                       line_length=30,confidence=99,recognized_text=source['lines'][0]['text'])
        source['overlay_alignment']=[alignment]
        marked=self.mark(source,pane,[[310,829,325,849]])
        self.assertEqual([e['name'] for e in parse(marked)['effects']],['Example Name'])
        marked=self.mark(source,pane,[[470,829,485,849]])
        self.assertEqual(parse(marked)['effects'],[])
        self.assertTrue(marked['occluded_receipt_lines'][0]['recipient_name_occluded'])
        # A reread of different text cannot narrow the obstruction region.
        alignment['recognized_text']='Inspired by Different Name!'
        self.assertEqual(parse(self.mark(source,pane,[[310,829,325,849]]))['effects'],[])

    def test_malformed_alignment_cannot_narrow_the_protected_line(self):
        words=['Inspired','by','Example!']
        columns=[list(range(1,9)),[11,12],list(range(15,23))]
        self.assertIsNotNone(inspiration_name_bounds([300,820,600,850],words,columns,30))
        for changed in ([list(reversed(columns[0])),*columns[1:]],
                        [columns[0],columns[1],[15]*8],
                        [columns[0],columns[1],list(range(15,22))]):
            self.assertIsNone(inspiration_name_bounds([300,820,600,850],words,changed,30))
