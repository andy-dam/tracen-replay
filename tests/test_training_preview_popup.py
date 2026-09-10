import copy
import unittest
from tracen_replay.vision import parse
from tracen_replay.transactions import training_events


def sample(x=629):
    return dict(header='Training',current_grid=True,result_grid=False,regions={},lines=[
        dict(text='Failure',confidence=92.955,box=[x,770,x+62,794]),
        dict(text='0%',confidence=93.568,box=[x+12,787,x+52,814])])


class TrainingPreviewPopupTests(unittest.TestCase):
    def test_popup_above_every_training_option_is_preview_only(self):
        for x in (307,415,520,629,735):
            with self.subTest(x=x):
                raw=sample(x);original=copy.deepcopy(raw);row=parse(raw)
                self.assertEqual(row['screen'],'training_preview')
                self.assertTrue(row['stats']['training_preview'])
                self.assertEqual(row['effects'],[])
                row.update(source_timestamp_ms=1000,evidence='preview.png')
                self.assertEqual(training_events([row]),[])
                self.assertEqual(raw,original)

    def test_missing_rate_does_not_turn_a_preview_into_an_award(self):
        raw=sample();raw['lines']=raw['lines'][:1]
        self.assertEqual(parse(raw)['screen'],'training_preview')

    def test_popup_outweighs_a_noisy_result_grid_signal(self):
        raw=sample();raw['result_grid']=True
        row=parse(raw)
        self.assertEqual(row['screen'],'training_preview')
        row.update(source_timestamp_ms=1000,evidence='preview.png')
        self.assertEqual(training_events([row]),[])

    def test_failure_narrative_or_other_layout_does_not_establish_preview(self):
        for change in ('header','grid','result','narrative','low_confidence','above','wide'):
            raw=sample()
            if change=='header':raw['header']='Career'
            elif change=='grid':raw['current_grid']=False
            elif change=='result':raw.update(current_grid=False,result_grid=True)
            elif change=='narrative':raw['lines'][0]['text']='Failure is a chance to learn.'
            elif change=='low_confidence':raw['lines'][0]['confidence']=89
            elif change=='above':raw['lines'][0]['box']=[629,470,691,494]
            else:raw['lines'][0]['box']=[250,770,800,794]
            with self.subTest(change=change):
                row=parse(raw)
                self.assertNotEqual(row['screen'],'training_preview')
                self.assertFalse(row['stats']['training_preview'])


if __name__=='__main__':unittest.main()
