import hashlib
import unittest
from unittest.mock import patch
from PIL import Image
from tests.test_neural_transactions import raw, line
from tracen_replay.vision import parse
from tracen_replay.receipt_occlusion import annotate, friendship_name_bounds


class FriendshipWithKeywordTests(unittest.TestCase):
    def test_fixed_grammar_repair_preserves_identity_amount_and_confidence(self):
        text='Friendship wh Example Person went up by 7.'
        value=line(text,(315,830,759,863))
        effect,=parse(raw([value]))['effects']
        self.assertEqual((effect['name'],effect['amount']),('Example Person',7))
        self.assertEqual(effect['original_text'],text)
        self.assertEqual(effect['confidence'],value['confidence'])

    def test_incomplete_future_and_low_confidence_are_not_promoted(self):
        for text,confidence in [('Friendship wh Example Person went up by 7',99),
                                ('Friendship wh Example Person will go up by 7.',99),
                                ('Friendship wh Example Person went up by 7.',80)]:
            value=dict(line(text,(315,830,759,863)),confidence=confidence)
            self.assertEqual(parse(raw([value]))['effects'],[])

    def test_recipient_occlusion_still_blocks_repaired_sentence(self):
        text='Friendship wh Example Person went up by 7.'
        box=[315,830,759,863]
        words=text.split()
        columns=[[1,3,5,6,8,11,12,14,16,17],[20,25],[29,30,32,33,36,38,39],
                 [42,44,46,48,50,51],[58,60,62,63],[67,68],[72,74],[77,79]]
        bounds=friendship_name_bounds(box,words,columns,81)
        self.assertIsNotNone(bounds)
        pane=Image.new('RGB',(810,1080),'white')
        value=raw([line(text,box)])
        value['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        value['overlay_alignment']=[dict(line_box=box,words=words,columns=columns,line_length=81,confidence=99)]
        with patch('tracen_replay.receipt_occlusion.overlay_boxes',return_value=[[500,838,511,856]]):
            checked=annotate(value,pane)
        self.assertTrue(checked['occluded_receipt_lines'][0]['recipient_name_occluded'])
        self.assertEqual(parse(checked)['effects'],[])
