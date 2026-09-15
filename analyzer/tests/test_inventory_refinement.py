import copy
import hashlib
import json
from pathlib import Path
import unittest
from PIL import Image
from tests.test_gameplay import workspace_temp
from tracen_replay.refine_inventory import apply
from tracen_replay.inventory import summarize
from tracen_replay.vision import parse


class InventoryPanelTests(unittest.TestCase):
    def setUp(self):
        self.raws=[x['raw'] for x in json.loads(Path('analyzer/tests/fixtures/final-owned-cards.json').read_text())['frames']]
        self.extras=json.loads(Path('analyzer/tests/fixtures/final-owned-panel.json').read_text())
        root=self.enterContext(workspace_temp())
        self.proof=root/'proof.png';Image.new('RGB',(810,1080),'white').save(self.proof)
        for extra in self.extras:extra['evidence_sha256']=hashlib.sha256(self.proof.read_bytes()).hexdigest()

    def test_real_ocr_recovers_wrapped_card_without_changing_inventory_claim(self):
        rows=[dict(apply(parse(raw),raw,extra,self.proof),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence'])
              for raw,extra in zip(self.raws,self.extras)]
        result=summarize(rows)
        self.assertEqual(len(result['observed_owned_cards']),14)
        self.assertIn('Front Runner Straightaways',{c['name_text'] for c in result['observed_owned_cards']})
        self.assertFalse(result['complete'])
        self.assertTrue(all(not c['variant_verified'] for c in result['observed_owned_cards']))
        unique=next(c for c in result['observed_owned_cards'] if c['name_text']=='Festive Miracle')
        self.assertEqual(unique['level'],5)
        self.assertTrue(unique['level_verified'])
        self.assertEqual(summarize([rows[0],rows[0]])['observed_owned_cards'],[])

    def test_mismatched_provenance_rejected(self):
        for field,value in [('raw_sha256','wrong'),('evidence_sha256','wrong'),
                            ('source_timestamp_ms',0),('box',[0,0,1,1]),('evidence','other.png')]:
            with self.subTest(field=field):
                extra=copy.deepcopy(self.extras[0]);extra[field]=value
                with self.assertRaises(ValueError):apply(parse(self.raws[0]),self.raws[0],extra,self.proof)

    def test_conflicting_high_confidence_name_abstains(self):
        raw=self.raws[0];extra=copy.deepcopy(self.extras[0])
        next(l for l in extra['lines'] if l['text']=='Murmur')['text']='Different Name'
        got=apply(parse(raw),raw,extra,self.proof)['facts']
        self.assertTrue(got['owned_skill_panel_conflicts'])
        self.assertFalse(any(c['name_text'] in ('Murmur','Different Name') for c in got['visible_owned_skill_cards']))

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

    def test_panel_cannot_overwrite_a_conflicting_accepted_base_level(self):
        raw=self.raws[0];base=parse(raw)
        next(c for c in base['facts']['visible_owned_skill_cards'] if c['name_text']=='Festive Miracle')['observed_level']=4
        refined=apply(base,raw,self.extras[0],self.proof)
        card=next(c for c in refined['facts']['visible_owned_skill_cards'] if c['name_text']=='Festive Miracle')
        self.assertIsNone(card['observed_level']);self.assertEqual(card['level_conflicts'],[4,5])
        rows=[dict(refined,source_timestamp_ms=0,evidence='a.png')]
        second=apply(parse(self.raws[1]),self.raws[1],self.extras[1],self.proof)
        rows.extend(dict(second,source_timestamp_ms=t,evidence=f'{t}.png') for t in (250,500))
        got=next(c for c in summarize(rows)['observed_owned_cards'] if c['name_text']=='Festive Miracle')
        self.assertFalse(got['level_verified']);self.assertIsNone(got['level'])


if __name__=='__main__':unittest.main()
