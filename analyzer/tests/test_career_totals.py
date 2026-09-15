import unittest
from tests.test_neural_transactions import raw,line
from tracen_replay.vision import parse
from tracen_replay.gameplay import classify


class CareerTotalsTests(unittest.TestCase):
    def sample(self):
        return raw([line('Bond Level',(281,117,366,138)),line('9',(505,214,530,243)),
                    line('1,240/1,600(+19)',(563,234,825,267)),line('Total Fans',(277,359,362,383)),
                    line('20,281,640(+322,038)',(303,410,696,450)),
                    line('Fans Earned This Month',(277,474,455,497)),line('Club',(323,508,366,531)),
                    line('330,751,832(+322,038)',(302,541,728,581))])

    def test_account_awards_are_not_another_race(self):
        result=parse(self.sample());facts=result['facts']
        self.assertEqual(result['screen'],'career_account_totals')
        self.assertFalse(facts['counts_as_career_action'])
        self.assertEqual(result['effects'],[])
        self.assertEqual(facts['account_fans'],{'total':20281640,'increase':322038})
        self.assertEqual(facts['monthly_fans'],{'total':330751832,'increase':322038,'scope':'club'})
        self.assertEqual(facts['bond_level'],9)
        self.assertEqual(facts['bond_progress'],{'current':1240,'required':1600,'increase':19})

    def test_mission_banner_and_conflicting_counters_cannot_supply_totals(self):
        sample=self.sample()
        sample['lines']=[l for l in sample['lines'] if l['text']!='20,281,640(+322,038)']
        sample['lines'].append(line('500,000(+100)',(303,20,696,60)))
        self.assertNotIn('account_fans',parse(sample)['facts'])
        sample=self.sample();sample['lines'].append(line('20,281,641(+322,038)',(303,410,696,450)))
        self.assertNotIn('account_fans',parse(sample)['facts'])

    def test_distinct_account_labels_and_race_receipts_remain_separate(self):
        self.assertEqual(classify('Total Fans 200 (+100) Fans Earned This Month',''),'career_account_totals')
        self.assertEqual(classify('Fans 200 (+100)',''),'race_result')
