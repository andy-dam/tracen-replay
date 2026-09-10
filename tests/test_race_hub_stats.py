import copy
import json
from pathlib import Path
import unittest

from tracen_replay.race_hub_stats import observation, pixel_layout
from tracen_replay.vision import parse


class RaceHubStatsTests(unittest.TestCase):
    def setUp(self):
        self.raw=json.loads(Path('tests/fixtures/race-day-hub-879000.json').read_text(encoding='utf-8'))

    def test_source_totals_are_read_without_counting_a_race_or_training(self):
        before=copy.deepcopy(self.raw)
        stats=parse(self.raw)['stats']
        self.assertEqual(stats['values'],dict(speed=642,stamina=462,power=515,guts=358,wit=671,skill_points=1085))
        self.assertEqual(stats['observation_profile'],'race_day_lower_totals')
        self.assertFalse(stats['training_preview'])
        self.assertIsNone(stats['completed_action'])
        self.assertEqual(stats['field_observations']['speed']['confidence'],99.978)
        self.assertEqual(self.raw,before)

    def test_other_numbers_are_read_without_expected_totals(self):
        raw=copy.deepcopy(self.raw)
        next(l for l in raw['lines'] if l['text']=='642')['text']='17'
        next(l for l in raw['lines'] if l['text']=='1085')['text']='0'
        result=observation(raw['lines'])
        self.assertEqual(result['values']['speed'],17)
        self.assertEqual(result['values']['skill_points'],0)

    def test_shared_performance_sidebar_reads_race_day_totals_without_awarding_gains(self):
        raw=copy.deepcopy(self.raw)
        raw['lines'].append(dict(text='Performance',confidence=99,box=[155,255,250,278]))
        for i,value in enumerate((69,51,56,44,77)):
            raw['lines'].append(dict(text=str(value),confidence=99,box=[210,298+56*i,244,323+56*i]))
        raw['lines'].append(dict(text='+10',confidence=99,box=[260,298,300,323]))
        row=parse(raw)
        self.assertEqual(row['facts']['performance_points'],dict(dance=69,passion=51,vocal=56,visual=44,composure=77))
        self.assertNotIn('awarded_performance_gains',row['facts'])
        self.assertNotIn('projected_performance_gains',row['facts'])
        self.assertIsNone(row['completed_action'])
        # Identical sidebar text without an established screen/profile is not enough.
        raw['lines']=[l for l in raw['lines'] if l['text']!='Race!']
        self.assertNotIn('performance_points',parse(raw)['facts'])

    def test_performance_sidebar_leaves_clipped_or_ambiguous_currency_unknown(self):
        from tracen_replay.vision import performance_panel_facts
        stats=dict(observation_profile='race_day_lower_totals',values={f:1 for f in ('speed','stamina','power','guts','wit','skill_points')})
        header=dict(text='Performance',confidence=99,box=[155,255,250,278])
        for lines in ([dict(text='69',confidence=96,box=[210,298,244,323])],
                      [dict(text='/250',confidence=99,box=[210,298,244,323])],
                      [dict(text=t,confidence=99,box=[210,298,244,323]) for t in ('69','68')]):
            self.assertNotIn('dance',performance_panel_facts([header,*lines],'unknown',stats)['performance_points'])
        self.assertEqual(performance_panel_facts([], 'unknown', stats),{})

    def test_caps_projections_partial_and_low_confidence_values_do_not_qualify(self):
        for replacement in ('/1600','+642','642/1600','64?'):
            raw=copy.deepcopy(self.raw)
            next(l for l in raw['lines'] if l['text']=='642')['text']=replacement
            with self.subTest(replacement=replacement):self.assertIsNone(observation(raw['lines']))
        raw=copy.deepcopy(self.raw)
        next(l for l in raw['lines'] if l['text']=='642')['confidence']=96.9
        self.assertIsNone(observation(raw['lines']))

    def test_full_label_row_and_race_control_are_required(self):
        for removed in ('Race!','Power','Skill Pts'):
            with self.subTest(removed=removed):
                self.assertIsNone(observation([l for l in self.raw['lines'] if l['text']!=removed]))
        raw=copy.deepcopy(self.raw)
        next(l for l in raw['lines'] if l['text']=='Race!')['box']=[400,200,500,220]
        self.assertIsNone(observation(raw['lines']))

    def test_duplicate_values_or_shifted_labels_abstain(self):
        raw=copy.deepcopy(self.raw)
        raw['lines'].append(dict(next(l for l in raw['lines'] if l['text']=='642'),text='643'))
        self.assertIsNone(observation(raw['lines']))
        raw=copy.deepcopy(self.raw)
        next(l for l in raw['lines'] if l['text']=='Power')['box']=[494,650,549,678]
        self.assertIsNone(observation(raw['lines']))

    def test_pixel_grid_requires_all_five_bands_at_the_supported_position(self):
        from PIL import Image, ImageDraw
        pane=Image.new('RGB',(810,1080),'white');draw=ImageDraw.Draw(pane)
        self.assertFalse(pixel_layout(pane))
        for x in (310,410,510,610,710):
            draw.rectangle((x-148,760,x-148+34,778),fill=(50,130,220))
        self.assertTrue(pixel_layout(pane))
        draw.rectangle((710-148,760,710-148+34,778),fill='white')
        self.assertFalse(pixel_layout(pane))
        self.assertFalse(pixel_layout(Image.new('RGB',(1920,1080),(50,130,220))))

    def test_pixel_layout_can_replace_flickering_labels_but_not_numeric_confidence(self):
        lines=[l for l in self.raw['lines'] if l['text'] not in ('Race!','Speed')]
        self.assertIsNone(observation(lines))
        self.assertEqual(observation(lines,grid_verified=True)['values']['speed'],642)
        lines=copy.deepcopy(lines)
        next(l for l in lines if l['text']=='642')['confidence']=96.99
        self.assertIsNone(observation(lines,grid_verified=True))


if __name__=='__main__':unittest.main()
