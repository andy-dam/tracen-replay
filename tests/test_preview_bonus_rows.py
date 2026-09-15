import unittest

from tracen_replay.preview_observations import parse_preview_overlay


def source(preview=17, bonus=3):
    return dict(header='Training', current_grid=True, result_grid=False, regions={}, lines=[
        dict(text='Wit Lvl 5', confidence=99, box=[230,169,319,193]),
        dict(text=f'+{bonus}', confidence=99, box=[291,633,345,672]),
        dict(text=f'+{preview}', confidence=99, box=[291,670,343,704]),
        dict(text='Speed', confidence=99, box=[299,697,361,721])])


class PreviewBonusRowTests(unittest.TestCase):
    def test_upper_song_bonus_never_replaces_adjacent_training_preview(self):
        for preview, bonus in [(17,3),(2,90),(0,4)]:
            result = parse_preview_overlay(source(preview,bonus))
            effects = result['preview_overlay_effects']
            self.assertEqual([(e['field'],e['amount']) for e in effects], [('speed',preview)])
            self.assertTrue(all(e['phase']=='preview' and e['awarded'] is False for e in effects))

    def test_unreadable_training_preview_does_not_fall_back_to_song_bonus(self):
        raw=source()
        del raw['lines'][2]
        self.assertFalse(parse_preview_overlay(raw).get('preview_overlay_effects'))

    def test_conflicting_adjacent_row_amounts_remain_unknown(self):
        raw=source()
        raw['lines'].append(dict(text='+19',confidence=99,box=[292,671,344,704]))
        self.assertFalse(parse_preview_overlay(raw).get('preview_overlay_effects'))


if __name__ == '__main__':
    unittest.main()
