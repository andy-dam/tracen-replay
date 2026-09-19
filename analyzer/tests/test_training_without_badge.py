import unittest

from tests.test_training_gain_g08_result_projection import _result_row
from tracen_replay.transactions import training_events
from tracen_replay.vision import _performance_panel_localized_candidates


class TrainingWithoutBadgeTests(unittest.TestCase):
    def test_a_training_whose_frames_show_no_badge_or_banner_awards_nothing(self):
        # The training scene keeps the preview's "+19" beside the Visual row
        # on screen; no frame shows a stat badge or an outcome banner.
        rows = [_result_row(1000, 'scene-1.png', option='guts', outcome=None, awards={'visual': 19}),
                _result_row(1250, 'scene-2.png', option='guts', outcome=None, awards={'visual': 19})]
        event = training_events(rows)[0]
        self.assertEqual(event['performance_deltas'], {})
        self.assertEqual(event['unawarded_performance_projection'], {'visual': 19})
        self.assertEqual(event['unawarded_performance_basis'], 'no_stat_badge_or_banner_on_any_frame')

    def test_a_badge_on_one_frame_or_a_success_banner_keeps_the_award(self):
        rows = [_result_row(1000, 'card-1.png', option='guts', outcome=None, awards={'visual': 19}),
                _result_row(1250, 'card-2.png', option='guts', outcome=None, gains={'guts': 12}, awards={'visual': 19})]
        event = training_events(rows)[0]
        self.assertEqual(event['performance_deltas'], {'visual': 19})
        self.assertNotIn('unawarded_performance_basis', event)
        rows = [_result_row(1000, 'card-1.png', option='guts', outcome='success', awards={'visual': 19}),
                _result_row(1250, 'card-2.png', option='guts', outcome=None, awards={'visual': 19})]
        event = training_events(rows)[0]
        self.assertEqual(event['performance_deltas'], {'visual': 19})

    def test_a_lone_digit_reread_of_a_missed_row_is_taken_from_seventy(self):
        def region(name, text, confidence):
            return {name: dict(text=text, confidence=confidence, box=[190, 520, 260, 560],
                               role='panel_localized_current', component='current',
                               geometry_basis='fixed_row_panel_geometry')}
        current = 'performance_panel_localized_current.composure'
        found = _performance_panel_localized_candidates(region(current, '1', 76.8), 'composure', 544)
        self.assertEqual([(c['value'], c['below_floor']) for c in found], [(1, True)])
        found = _performance_panel_localized_candidates(region(current, '7', 99.2), 'composure', 544)
        self.assertEqual([(c['value'], c['below_floor']) for c in found], [(7, False)])
        # Two digits keep the floor, one digit under 70 is nothing, and a
        # glyph crop is not the missed row's crop.
        self.assertEqual(_performance_panel_localized_candidates(region(current, '12', 76.8), 'composure', 544), [])
        self.assertEqual(_performance_panel_localized_candidates(region(current, '1', 60.0), 'composure', 544), [])
        glyph = 'performance_panel_localized_glyph.composure'
        self.assertEqual(_performance_panel_localized_candidates(region(glyph, '1', 76.8), 'composure', 544), [])


if __name__ == '__main__':
    unittest.main()
