import hashlib
import json
from pathlib import Path
import unittest
from PIL import Image
from tests.test_gameplay import workspace_temp
from tracen_replay.inventory import summarize


class InventoryPanelTests(unittest.TestCase):
    def setUp(self):
        self.raws=[x['raw'] for x in json.loads(Path('analyzer/tests/fixtures/final-owned-cards.json').read_text())['frames']]
        self.extras=json.loads(Path('analyzer/tests/fixtures/final-owned-panel.json').read_text())
        root=self.enterContext(workspace_temp())
        self.proof=root/'proof.png';Image.new('RGB',(810,1080),'white').save(self.proof)
        for extra in self.extras:extra['evidence_sha256']=hashlib.sha256(self.proof.read_bytes()).hexdigest()

    def test_details_need_distinct_frames_and_abstain_on_conflict(self):
        def reading(time,level,variant):
            return dict(source_timestamp_ms=time,evidence=f'{time}.png',facts=dict(visible_owned_skill_cards=[
                dict(name_text='Example',slot=[0,0],text_evidence=[],observed_level=level,observed_variant=variant)]))
        first=reading(0,5,'single_circle');second=reading(250,None,None)
        got=summarize([first,second])['observed_owned_cards'][0]
        self.assertFalse(got['level_verified']);self.assertFalse(got['variant_verified'])
        second=reading(250,5,'single_circle')
        got=summarize([first,second])['observed_owned_cards'][0]
        self.assertTrue(got['level_verified']);self.assertTrue(got['variant_verified'])
        got=summarize([first,second,reading(500,4,'double_circle')])['observed_owned_cards'][0]
        self.assertIsNone(got['level']);self.assertIsNone(got['variant'])
        self.assertEqual(got['level_conflicts'],[4,5])


if __name__=='__main__':unittest.main()
