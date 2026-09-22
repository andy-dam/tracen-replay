import copy
import json
import unittest
from pathlib import Path
from tracen_replay.vision import parse


class ConcertPanelsTests(unittest.TestCase):
    def setUp(self):
        self.rows=json.loads(Path('analyzer/tests/fixtures/concert-panels.json').read_text(encoding='utf-8'))

    def test_source_columns_remain_current_and_planned(self):
        matched=missing=0
        for item in self.rows:
            facts=parse(item['raw'])['facts']
            for column in ('current','planned'):
                for field,value in item['expected'][column].items():
                    got=facts[column+'_concert_bonuses'].get(field)
                    if item['source']=='v1' and item['expected']['source_timestamp_ms']==661750 and field=='support_chain_event_frequency':
                        # This visible zero remains below the confidence threshold.
                        self.assertIsNone(got);missing+=1
                    else:self.assertEqual(got,value);matched+=1
            self.assertTrue(facts['bonus_snapshot_is_not_activation'])
        self.assertEqual((matched,missing),(46,2))

    def test_level_zero_normalization_retains_text_and_threshold(self):
        raw=copy.deepcopy(self.rows[4]['raw'])
        facts=parse(raw)['facts'];field='support_chain_event_frequency'
        self.assertEqual(facts['current_concert_bonuses'][field],0)
        self.assertEqual(facts['planned_concert_bonuses'][field],1)
        self.assertEqual(facts['concert_bonus_evidence'][field]['text'],'Lvl OLvl 1')
        for line in raw['lines']:
            if line['text']=='Lvl OLvl 1':line['confidence']=89.9
        self.assertNotIn(field,parse(raw)['facts']['current_concert_bonuses'])

    def test_malformed_level_does_not_become_zero(self):
        raw=copy.deepcopy(self.rows[4]['raw'])
        for replacement in ('Lvl OO Lvl 1','Lvl Oops','Lvl O2','O','Lvl ? Lvl 1'):
            changed=copy.deepcopy(raw)
            for line in changed['lines']:
                if line['text']=='Lvl OLvl 1':line['text']=replacement
            self.assertNotIn('support_chain_event_frequency',parse(changed)['facts']['current_concert_bonuses'])


if __name__=='__main__':unittest.main()
