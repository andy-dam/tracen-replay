"""The sidebar's rereads are asked for wherever the panel is on screen, not only beside the stat grid."""
import unittest

from tests.test_performance_panel_recovery import panel_lines
from tracen_replay.vision import _performance_panel_heading, _performance_panel_requests


def without_heading(lines):
    return [item for item in lines if item['text'] != 'Performance']


class PanelRereadsWithoutGridTests(unittest.TestCase):
    def test_a_result_frame_without_the_grid_is_asked_for_the_same_crops(self):
        # A training result: the stat grid is gone while the cards animate,
        # the sidebar is still up, and the detector boxed only the last
        # digit of Vocal 18.
        lines = panel_lines()
        vocal = next(item for item in lines if item['text'] == '27')
        vocal.update(text='8', box=[229, 406, 251, 441], confidence=100.0)
        self.assertTrue(_performance_panel_heading(lines))

        requests, _component, localized, _caps = _performance_panel_requests(lines, current=False)
        beside_grid, _, _, _ = _performance_panel_requests(lines, current=True)

        names = [name for name, _box in requests]
        self.assertIn('performance_panel_localized_slot.vocal', names)
        self.assertEqual(localized['performance_panel_localized_slot.vocal']['role'], 'panel_localized_current')
        self.assertEqual(requests, beside_grid)

    def test_without_the_heading_or_the_grid_nothing_is_asked(self):
        lines = without_heading(panel_lines())
        self.assertFalse(_performance_panel_heading(lines))
        self.assertEqual(_performance_panel_requests(lines, current=False), ([], {}, {}, {}))
        # Beside the grid the rows are still asked for, as before.
        requests, _, localized, _ = _performance_panel_requests(lines, current=True)
        self.assertTrue(requests)
        self.assertTrue(localized)

    def test_the_heading_alone_asks_only_for_the_absent_caps(self):
        heading = [item for item in panel_lines() if item['text'] == 'Performance']
        self.assertTrue(_performance_panel_heading(heading))
        requests, component, localized, caps = _performance_panel_requests(heading, current=False)
        # No row was read, so no value crop is made; the absent caps are
        # asked for, which is what the cap builder does beside the grid too.
        self.assertEqual((component, localized), ({}, {}))
        self.assertEqual(sorted(caps), sorted(f'performance_panel_cap.{field}'
                                              for field in ('dance', 'passion', 'vocal', 'visual', 'composure')))
        self.assertEqual(requests, _performance_panel_requests(heading, current=True)[0])
        self.assertFalse(_performance_panel_heading('not lines'))


if __name__ == '__main__':
    unittest.main()
