"""The baseline table counts coverage and accuracy over labeled crops only."""
import unittest

from tools.reader_baseline import markdown, summarize, totals


def row(split, kind, field, read, label, status, time=0, run='r'):
    return dict(run=run, split=split, kind=kind, field=field, read=read, label=label, status=status, source_timestamp_ms=time)


class ReaderBaselineTests(unittest.TestCase):
    def test_counts_only_labeled_crops_by_frame_and_by_visit(self):
        rows = [
            # One card sampled on three frames: read right once, wrong once, not at all once.
            row('train', 'stat_badge', 'speed', 137, 137, 'confirmed', 1000),
            row('train', 'stat_badge', 'speed', 131, 137, 'hard', 1250),
            row('train', 'stat_badge', 'speed', None, 137, 'hard', 1500),
            # A second card a minute later, never read.
            row('train', 'stat_badge', 'speed', None, 150, 'hard', 61000),
            row('train', 'stat_badge', 'speed', 200, None, 'unlabeled', 90000),
            row('holdout', 'performance_counter', 'dance', 30, 30, 'confirmed'),
        ]
        table = summarize(rows)
        self.assertEqual([(r['split'], r['kind'], r['field'], r['labeled'], r['read'], r['correct'], r['cards'], r['cards_read']) for r in table],
                         [('holdout', 'performance_counter', 'dance', 1, 1, 1, 1, 1), ('train', 'stat_badge', 'speed', 4, 2, 1, 2, 1)])
        speed = table[1]
        self.assertAlmostEqual(speed['coverage'], 2 / 4)
        self.assertAlmostEqual(speed['accuracy'], 1 / 2)
        self.assertAlmostEqual(speed['exact'], 1 / 4)
        self.assertAlmostEqual(speed['card_exact'], 1 / 2)
        self.assertEqual([(r['split'], r['kind'], r['labeled'], r['cards']) for r in totals(table)],
                         [('holdout', 'performance_counter', 1, 1), ('train', 'stat_badge', 4, 2)])
        text = markdown(table)
        self.assertIn('| train | stat_badge | speed | 4 | 50.0% | 50.0% | 25.0% | 2 | 50.0% |', text)


if __name__ == '__main__':
    unittest.main()
