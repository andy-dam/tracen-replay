import copy
import unittest

from tracen_replay.stat_state_details import read_main_stat_caps


def source_bar():
    # Source-transcribed header/cap lines from a gameplay stat bar. These
    # values are fixture expectations, never recognizer inputs or defaults.
    names = ['Speed', 'Stamina', 'Power', 'Guts', 'Wit']
    xs = [(305, 365), (402, 458), (495, 556), (590, 649), (686, 746)]
    values = [1600, 1341, 1348, 1500, 1300]
    lines = []
    for name, (left, right), cap in zip(names, xs, values):
        lines.extend([dict(text=name, confidence=99.9, box=[left, 696, right, 720]),
                      dict(text=f'/{cap}', confidence=99.9, box=[left, 742, right, 765])])
    return dict(current_grid=True, result_grid=False, lines=lines)


class StatStateDetailsTests(unittest.TestCase):
    def test_caps_follow_readable_values_and_line_order_not_fixture_defaults(self):
        for values in ([999, 1234, 1501, 1789, 2000], [2000, 999, 1234, 1501, 1789]):
            raw = source_bar()
            cap_lines = [line for line in raw['lines'] if line['text'].startswith('/')]
            for line, value in zip(cap_lines, values):
                line['text'] = f'123 / {value}'
            raw['lines'].reverse()
            caps, _ = read_main_stat_caps(raw)
            self.assertEqual(caps, dict(zip(['speed', 'stamina', 'power', 'guts', 'wit'], values)))

    def test_read_source_caps_with_column_proof_without_changing_input(self):
        raw = source_bar()
        original = copy.deepcopy(raw)
        caps, proof = read_main_stat_caps(raw)
        self.assertEqual(caps, dict(speed=1600, stamina=1341, power=1348, guts=1500, wit=1300))
        self.assertEqual(proof['speed']['readings'][0]['text'], '/1600')
        self.assertEqual(raw, original)

    def test_conflicting_caps_do_not_pick_the_larger_or_expected_default(self):
        raw = source_bar()
        raw['lines'].append(dict(text='/1700', confidence=99.99, box=[305, 742, 365, 765]))
        caps, proof = read_main_stat_caps(raw)
        self.assertNotIn('speed', caps)
        self.assertNotIn('speed', proof)
        self.assertEqual(caps['stamina'], 1341)

    def test_missing_header_and_low_confidence_caps_remain_unknown(self):
        raw = source_bar()
        raw['lines'] = [line for line in raw['lines'] if line['text'] != 'Speed']
        next(line for line in raw['lines'] if line['text'] == '/1341')['confidence'] = 80
        caps, _ = read_main_stat_caps(raw)
        self.assertNotIn('speed', caps)
        self.assertNotIn('stamina', caps)

    def test_currency_and_skill_menu_numbers_cannot_supply_stat_caps(self):
        raw = source_bar()
        raw['lines'] = [line for line in raw['lines'] if not line['text'].startswith('/')]
        raw['lines'] += [dict(text='/400', confidence=99.9, box=[200, 420, 260, 440]),
                         dict(text='1600', confidence=99.9, box=[305, 742, 365, 765])]
        self.assertEqual(read_main_stat_caps(raw), ({}, {}))
        raw = source_bar()
        raw['current_grid'] = False
        self.assertEqual(read_main_stat_caps(raw), ({}, {}))
        raw['current_grid'], raw['result_grid'] = True, True
        self.assertEqual(read_main_stat_caps(raw), ({}, {}))


if __name__ == '__main__':
    unittest.main()
