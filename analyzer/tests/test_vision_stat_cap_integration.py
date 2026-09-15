import unittest

from tracen_replay.vision import parse
from tests.test_stat_state_details import source_bar


class VisionStatCapIntegrationTests(unittest.TestCase):
    def test_parse_emits_same_frame_main_stat_caps_and_proof(self):
        raw = source_bar()
        raw.update(header='', regions={})

        facts = parse(raw)['facts']

        self.assertEqual(facts['stat_caps'], {
            'speed': 1600, 'stamina': 1341, 'power': 1348,
            'guts': 1500, 'wit': 1300,
        })
        self.assertEqual(facts['stat_cap_provenance']['speed']['basis'],
                         'labeled_main_stat_bar_cap')
        self.assertEqual(facts['stat_cap_provenance']['speed']['header']['text'], 'Speed')
        self.assertEqual(facts['stat_cap_provenance']['speed']['readings'][0]['text'], '/1600')

    def test_parse_does_not_use_result_grid_or_auxiliary_numbers_as_main_caps(self):
        raw = source_bar()
        raw.update(header='', regions={}, result_grid=True)
        raw['lines'].append(dict(text='/9999', confidence=99.9, box=[305, 742, 365, 765]))

        facts = parse(raw)['facts']

        self.assertNotIn('stat_cap_provenance', facts)
        self.assertNotIn('stat_caps', facts)


if __name__ == '__main__':
    unittest.main()

