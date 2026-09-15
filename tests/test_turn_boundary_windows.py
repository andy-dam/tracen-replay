import unittest

from tracen_replay.turn_boundary import TimeOrdered, _rows_between, ordered_rows


def _rows(*times):
    return [dict(source_timestamp_ms=t, evidence=f'frame-{i}.png') for i, t in enumerate(times)]


class TimeOrderedWindowTests(unittest.TestCase):
    def test_ordered_rows_sorts_finite_timestamps_and_keeps_input_order_for_ties(self):
        rows = _rows(500, 100, 300, 100) + [dict(source_timestamp_ms=None), dict(source_timestamp_ms=True), 'text']
        ordered = ordered_rows(rows)
        self.assertIsInstance(ordered, TimeOrdered)
        self.assertEqual([r['evidence'] for r in ordered], ['frame-1.png', 'frame-3.png', 'frame-2.png', 'frame-0.png'])
        self.assertEqual(ordered.times, [100, 100, 300, 500])

    def test_window_matches_a_full_scan_with_the_same_inclusive_bounds(self):
        rows = _rows(0, 250, 500, 500, 750, 1000, 1250)
        ordered = ordered_rows(rows)
        for lo, hi in ((250, 750), (0, 0), (600, 400), (-100, 5000), (500, 500), (501, 749)):
            scanned = [r for r in ordered if lo <= r['source_timestamp_ms'] <= hi]
            self.assertEqual(ordered.window(lo, hi), scanned, (lo, hi))
            self.assertEqual(_rows_between(ordered, lo, hi), scanned, (lo, hi))

    def test_rows_between_returns_a_plain_list_whole_so_callers_keep_filtering(self):
        rows = _rows(900, 100, 500)
        self.assertIs(_rows_between(rows, 200, 600), rows)


if __name__ == '__main__':
    unittest.main()
