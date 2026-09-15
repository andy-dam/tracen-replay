import unittest
from tracen_replay.gameplay import effects_from_lines
from tracen_replay.refine_receipts import consensus, receipt


class ReceiptAmountSpacingTests(unittest.TestCase):
    def test_crop_consensus_output_is_parseable_and_preserves_source_text(self):
        texts=['Friendship with Agnes Tachyon went up by5.',
               'Friendship with Agnes Tachyon went up by 5.',
               'Friendship with Agnes Tachyon went up by 5.']
        selected=consensus([dict(text=t,confidence=99) for t in texts])
        effects=effects_from_lines([selected])
        self.assertEqual(effects[0]['name'],'Agnes Tachyon')
        self.assertEqual(effects[0]['amount'],5)
        self.assertEqual(effects[0]['raw_text'],texts[0])
        self.assertEqual(receipt(texts[0]),receipt(texts[1]))

    def test_award_boundaries_work_across_resources(self):
        for text in ('Speed went up by12.', 'Energy recovered by8.',
                     'Energy went down by21.', 'Speed cap went up by3.'):
            with self.subTest(text=text):
                effect=effects_from_lines([dict(text=text,confidence=99)])
                self.assertEqual(len(effect),1)
                self.assertEqual(effect[0]['raw_text'],text)

    def test_names_digits_and_previews_are_not_repaired(self):
        text='Gained 2 hint level(s) for Standby5.'
        self.assertEqual(effects_from_lines([dict(text=text,confidence=99)])[0]['name'],'Standby5')
        for text in ('Speed went up by1?.','Speed +12','Energy recovered byO.'):
            with self.subTest(text=text):
                self.assertEqual(effects_from_lines([dict(text=text,confidence=99)]),[])
