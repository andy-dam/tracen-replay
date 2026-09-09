import copy
import hashlib
import json
import unittest
from pathlib import Path
from tracen_replay.vision import parse
from tracen_replay.concert_evaluate import evaluate
from tests.test_gameplay import workspace_temp


class ConcertPanelsTests(unittest.TestCase):
    def setUp(self):
        self.rows=json.loads(Path('tests/fixtures/concert-panels.json').read_text(encoding='utf-8'))

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

    def test_evaluator_penalizes_swapped_columns_and_rejects_changed_proof(self):
        with workspace_temp() as root:
            proof=root/'panel.png';proof.write_bytes(b'reviewed evidence')
            item=dict(source_timestamp_ms=100,evidence='panel.png',evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                      current={'specialty_priority':5},planned={'specialty_priority':15})
            reference=dict(source_sha256='source',scope='two values',items=[item])
            facts=dict(current_concert_bonuses={'specialty_priority':15},planned_concert_bonuses={'specialty_priority':5})
            report=dict(source={'sha256':'source'},gameplay_tracking=dict(auxiliary_log_used=False,
                        readings=[dict(source_timestamp_ms=100,evidence='panel.png',facts=facts)]))
            score=evaluate(reference,report,root)
            self.assertEqual(score['incorrect'],2);self.assertFalse(score['passed'])
            facts['current_concert_bonuses']={}
            self.assertEqual(evaluate(reference,report,root)['missing'],1)
            proof.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'proof changed'):evaluate(reference,report,root)


if __name__=='__main__':unittest.main()
