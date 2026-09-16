"""A reader is judged per card, and false reads are counted per frame."""
import unittest

from tools.reader_baseline import current_reads, judge, markdown, parse, summarize, union_cards


def box(visit, field, before, after, value=None, gain_read=None, content='unknown', split='holdout'):
    return dict(split=split, kind='result_box', field=field, visit=visit, before=before, after=after,
                gain=None if before is None else after - before, read_value=value, read_gain=gain_read, content=content)


class ReaderBaselineTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse('190/1341'), (190, None))
        self.assertEqual(parse('2097'), (2097, None))
        self.assertEqual(parse('+16'), (None, 16))
        self.assertEqual(parse(''), (None, None))
        self.assertEqual(parse('+'), (None, None))

    def test_cards_and_false_reads(self):
        rows = [
            # Speed card, 100 -> 115: one frame shows the value before, one misreads, one counts up.
            box('c1', 'speed', 100, 115, value=100, content='badge'),
            box('c1', 'speed', 100, 115, value=199),
            box('c1', 'speed', 100, 115, value=108),
            # Wit card, 60 -> 70: the gain is read right on one frame and wrong on another.
            box('c1', 'wit', 60, 70, gain_read=10, content='gain'),
            box('c1', 'wit', 60, 70, gain_read=7),
            # Power card, 300 -> 300: the value after is read, but the card raised nothing.
            box('c1', 'power', 300, 300, value=300, content='badge'),
            # A blank box read as anything is false, bracketed or not.
            box(None, 'guts', None, None, value=50, content='blank'),
            box('c1', 'guts', 50, 50, content='blank'),
            dict(split='holdout', kind='performance_counter', field='dance', expected=30, read_value=30),
            dict(split='holdout', kind='performance_counter', field='dance', expected=30, read_value=31),
        ]
        frames, cards = judge(rows, current_reads(rows))
        self.assertEqual(cards[('holdout', 'speed', 'c1')], dict(gain=15, value_read=True, gain_recovered=False))
        self.assertEqual(cards[('holdout', 'wit', 'c1')], dict(gain=10, value_read=False, gain_recovered=True))
        self.assertEqual(cards[('holdout', 'power', 'c1')], dict(gain=0, value_read=True, gain_recovered=True))
        self.assertEqual(frames[('holdout', 'result_box', 'speed')]['false_values'], 1)
        self.assertEqual(frames[('holdout', 'result_box', 'wit')]['false_gains'], 1)
        self.assertEqual(frames[('holdout', 'result_box', 'guts')]['false_values'], 1)
        self.assertEqual(frames[('holdout', 'performance_counter', 'dance')], dict(frames=2, value_reads=2, false_values=1, gain_reads=0, false_gains=0, exact=1))
        table = summarize(frames, cards)
        total = next(r for r in table if r['kind'] == 'result_box' and r['field'] == 'all')
        self.assertEqual((total['cards'], total['cards_read'], total['gain_cards'], total['gains_recovered']), (4, 2, 2, 1))
        self.assertEqual((total['false_values'], total['false_gains']), (2, 1))
        self.assertIn('| holdout | all | 4 | 50.0% | 2 | 50.0% |', markdown(table))

    def test_pooling_readers(self):
        a = {('holdout', 'speed', 'c1'): dict(gain=15, value_read=True, gain_recovered=False)}
        b = {('holdout', 'speed', 'c1'): dict(gain=15, value_read=False, gain_recovered=True),
             ('holdout', 'wit', 'c1'): dict(gain=0, value_read=True, gain_recovered=False)}
        pooled = union_cards(a, b)
        self.assertEqual(pooled[('holdout', 'speed', 'c1')], dict(gain=15, value_read=True, gain_recovered=True))
        self.assertEqual(len(pooled), 2)


if __name__ == '__main__':
    unittest.main()
