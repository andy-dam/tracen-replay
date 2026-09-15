import copy
import json
import unittest
from pathlib import Path
from tracen_replay.vision import parse
from tracen_replay.inventory import summarize,visible_cards


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.raws=[x['raw'] for x in json.loads(Path('analyzer/tests/fixtures/final-owned-cards.json').read_text(encoding='utf-8'))['frames']]

    def readings(self):
        return [dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']) for raw in self.raws]

    def test_source_owned_cards_include_wrapped_names_but_not_complete_inventory(self):
        result=summarize(self.readings())
        expected={'Festive Miracle','Angling and Scheming','Barcarole of Blessings','Right-Handed','Firm Conditions',
                  'Fall Runner','Winter Runner','Swinging Maestro','Straightaway Adept','Focus',
                  'Medium Straightaways','Thunderbolt Step','Murmur'}
        self.assertEqual({c['name_text'] for c in result['observed_owned_cards']},expected)
        self.assertFalse(result['complete'])
        self.assertTrue(all(not c['variant_verified'] and not c['level_verified'] for c in result['observed_owned_cards']))

    def test_wrapped_low_confidence_card_abstains_instead_of_emitting_prefix(self):
        cards=parse(self.raws[1])['facts']['visible_owned_skill_cards']
        self.assertFalse(any(c['name_text'].startswith('Front Runner') for c in cards))

    def test_summary_tab_and_stat_anchors_are_required(self):
        raw=copy.deepcopy(self.raws[0]);raw['lines']=[l for l in raw['lines'] if l['text']!='Skills']
        self.assertEqual(visible_cards(raw,dict(speed=1,power=2,wit=3)),[])
        self.assertEqual(visible_cards(self.raws[0],dict(speed=1)),[])

    def test_repeated_same_frame_or_distant_observations_do_not_prove_ownership(self):
        rows=self.readings()
        self.assertEqual(summarize([rows[0],rows[0]])['observed_owned_cards'],[])
        rows[1]['source_timestamp_ms']+=10000
        self.assertEqual(summarize(rows)['observed_owned_cards'],[])


if __name__=='__main__':unittest.main()
