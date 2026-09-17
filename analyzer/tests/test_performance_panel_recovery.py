import copy
import unittest
import json
from pathlib import Path

from tracen_replay.vision import enrich_performance_panels, parse, performance_panel_facts


ROWS = (
    ('dance', 'Da', 320, 334, 59),
    ('passion', 'Pa', 376, 390, 42),
    ('vocal', 'Vo', 432, 446, 27),
    ('visual', 'Vi', 488, 502, 19),
    ('composure', 'Co', 544, 558, 74),
)


def line(text, box, confidence=99):
    return dict(text=text, confidence=confidence, box=list(box))


def panel_lines(values=None, caps=None):
    values = dict((field, value) for field, _label, _label_y, _cap_y, value in ROWS) if values is None else values
    caps = dict.fromkeys((field for field, _label, _label_y, _cap_y, _value in ROWS), 200) if caps is None else caps
    lines = [line('Performance', (169, 262, 247, 282)),
             line('Points', (186, 276, 231, 297))]
    for field, label, label_y, cap_y, default in ROWS:
        lines.append(line(label, (158, label_y - 12, 188, label_y + 12)))
        lines.append(line(str(values[field]), (208, label_y - 26, 256, label_y + 9)))
        lines.append(line('/' + str(caps[field]), (202, cap_y - 13, 252, cap_y + 12)))
    return lines


class PerformancePanelRecoveryTests(unittest.TestCase):
    def test_unknown_screen_reads_only_a_strictly_identified_sidebar(self):
        facts = performance_panel_facts(panel_lines(), 'unknown', {})
        self.assertEqual(facts, {'performance_points': {
            'dance': 59, 'passion': 42, 'vocal': 27, 'visual': 19, 'composure': 74,
        }})

    def test_parse_preserves_sidebar_balance_when_screen_classifier_is_unknown(self):
        raw = dict(lines=panel_lines(), regions={}, header='', current_grid=False, result_grid=False)
        parsed = parse(raw)
        self.assertEqual(parsed['screen'], 'unknown')
        self.assertEqual(parsed['facts']['performance_points']['dance'], 59)
        self.assertEqual(parsed['facts']['performance_points']['composure'], 74)
        self.assertNotIn('projected_performance_gains', parsed['facts'])
        self.assertNotIn('awarded_performance_gains', parsed['facts'])

    def test_header_alone_or_missing_row_identity_does_not_accept_numbers(self):
        full = panel_lines()
        for remove in ('Performance', 'Points', 'Da', '/200'):
            with self.subTest(remove=remove):
                lines = [item for item in full if item['text'] != remove]
                self.assertEqual(performance_panel_facts(lines, 'unknown', {}), {})

    def test_low_confidence_merged_preview_value_stays_unknown(self):
        lines = panel_lines()
        dance = next(item for item in lines if item['text'] == '59')
        dance.update(text='59+18', confidence=90.988)
        facts = performance_panel_facts(lines, 'unknown', {})
        self.assertNotIn('dance', facts['performance_points'])
        self.assertEqual(facts['performance_points']['passion'], 42)

    def test_source_derived_cap_250_panel_is_accepted(self):
        facts = performance_panel_facts(
            panel_lines(values={'dance': 58, 'passion': 35, 'vocal': 26, 'visual': 21, 'composure': 35},
                        caps=dict.fromkeys(('dance', 'passion', 'vocal', 'visual', 'composure'), 250)),
            'unknown', {})
        self.assertEqual(facts['performance_points'],
                         {'dance': 58, 'passion': 35, 'vocal': 26, 'visual': 21, 'composure': 35})

    def test_each_balance_must_fit_its_own_positive_source_cap(self):
        values = {'dance': 201, 'passion': 42, 'vocal': 27, 'visual': 19, 'composure': 74}
        facts = performance_panel_facts(
            panel_lines(values=values, caps={'dance': 200, 'passion': 250, 'vocal': 200,
                                              'visual': 200, 'composure': 200}),
            'unknown', {})
        self.assertNotIn('dance', facts['performance_points'])
        self.assertEqual(facts['performance_points']['passion'], 42)

    def test_zero_or_non_numeric_cap_rejects_panel_identity(self):
        for cap in (0, 'cap'):
            with self.subTest(cap=cap):
                caps = dict.fromkeys((field for field, _label, _label_y, _cap_y, _value in ROWS), 200)
                caps['dance'] = cap
                self.assertEqual(performance_panel_facts(panel_lines(caps=caps), 'unknown', {}), {})

    def test_baseline_ocr_fixture_covers_200_and_250_panels(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'performance-panel-recovery-v1.json').read_text(encoding='utf-8'))
        self.assertEqual(len(fixture['observations']), 3)
        for observation in fixture['observations']:
            with self.subTest(timestamp=observation['source_timestamp_ms']):
                facts = performance_panel_facts(observation['lines'], observation['screen'], {})
                self.assertEqual(facts['performance_points'], observation['expected_points'])

    def test_enrichment_recovers_fixture_values_and_records_same_row_provenance(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'performance-panel-recovery-v1.json').read_text(encoding='utf-8'))
        rows = [dict(source_timestamp_ms=item['source_timestamp_ms'], screen=item['screen'],
                     evidence=item['evidence'], facts={},
                     ocr={'neural': item['lines']}) for item in fixture['observations']]
        enriched = enrich_performance_panels(rows)
        for index, (item, result) in enumerate(zip(fixture['observations'], enriched)):
            with self.subTest(index=index):
                self.assertEqual(result['facts']['performance_points'], item['expected_points'])
                recovery = result['facts']['performance_panel_recovery']
                self.assertEqual(recovery['method'], 'strict_performance_panel_identity')
                self.assertEqual(recovery['basis'], 'same_reading_ocr_neural')
                self.assertEqual(recovery['source_reading_index'], index)
                self.assertEqual(recovery['source_timestamp_ms'], item['source_timestamp_ms'])
                self.assertEqual(recovery['evidence'], item['evidence'])
                self.assertEqual(recovery['cap_values'],
                                 dict.fromkeys(('dance', 'passion', 'vocal', 'visual', 'composure'),
                                               200 if index < 2 else 250))
                self.assertEqual(recovery['status'], 'recovered')

    def test_enrichment_fills_only_none_fields_and_preserves_row_semantics(self):
        source = dict(source_timestamp_ms=226500, screen='unknown', evidence='source.png',
                      facts={'performance_points': {'dance': None, 'passion': 42},
                             'unrelated': {'keep': True}},
                      stats={'values': None}, effects=[{'kind': 'dialogue'}],
                      completed_action=None, ocr={'neural': panel_lines()})
        original = copy.deepcopy(source)
        result = enrich_performance_panels([source])[0]
        self.assertEqual(source, original)
        self.assertEqual(result['facts']['performance_points'],
                         {'dance': 59, 'passion': 42, 'vocal': 27, 'visual': 19, 'composure': 74})
        self.assertEqual(result['facts']['unrelated'], {'keep': True})
        self.assertEqual(result['stats'], source['stats'])
        self.assertEqual(result['effects'], source['effects'])
        self.assertIsNone(result['completed_action'])
        self.assertEqual(result['screen'], 'unknown')
        self.assertEqual(result['evidence'], 'source.png')
        self.assertEqual(result['facts']['performance_panel_recovery']['added_fields'],
                         ['dance', 'vocal', 'visual', 'composure'])

    def test_repeated_enrichment_preserves_recovery_provenance(self):
        source = dict(source_timestamp_ms=226500, screen='unknown', evidence='source.png',
                      facts={'performance_points': {'dance': None}},
                      ocr={'neural': panel_lines()})
        first = enrich_performance_panels([source])[0]
        second = enrich_performance_panels([first])[0]
        self.assertEqual(second, first)
        self.assertEqual(second['facts']['performance_panel_recovery']['added_fields'],
                         ['dance', 'passion', 'vocal', 'visual', 'composure'])

    def test_repeated_enrichment_updates_only_index_after_native_row_insertion(self):
        source = dict(source_timestamp_ms=226500, screen='unknown', evidence='source.png',
                      facts={}, ocr={'neural': panel_lines()})
        first = enrich_performance_panels([source])[0]
        shifted = [dict(source_timestamp_ms=225000, screen='boundary_state_recovery',
                        evidence='native.png', facts={}, ocr={'neural': []}), first]
        second = enrich_performance_panels(shifted)[1]
        original_recovery = first['facts']['performance_panel_recovery']
        updated_recovery = second['facts']['performance_panel_recovery']
        self.assertEqual(updated_recovery['source_reading_index'], 1)
        for field in ('method', 'basis', 'source_timestamp_ms', 'evidence',
                      'cap_values', 'recognized_values', 'existing_values',
                      'added_fields', 'conflicts', 'status'):
            with self.subTest(field=field):
                self.assertEqual(updated_recovery[field], original_recovery[field])

    def test_complete_existing_balance_needs_no_recovery_provenance(self):
        source = dict(source_timestamp_ms=226500, screen='unknown', evidence='source.png',
                      facts={'performance_points': {
                          'dance': 59, 'passion': 42, 'vocal': 27, 'visual': 19, 'composure': 74,
                      }}, ocr={'neural': panel_lines()})
        result = enrich_performance_panels([source])[0]
        self.assertEqual(result, source)

    def test_repeated_conflict_preserves_original_diagnostic(self):
        source = dict(source_timestamp_ms=226500, screen='unknown', evidence='source.png',
                      facts={'performance_points': {'dance': 60, 'passion': None}},
                      ocr={'neural': panel_lines()})
        first = enrich_performance_panels([source])[0]
        second = enrich_performance_panels([first])[0]
        self.assertEqual(second, first)
        self.assertEqual(second['facts']['performance_panel_recovery']['status'], 'conflict')

    def test_conflicting_existing_balance_refuses_all_panel_fills(self):
        source = dict(source_timestamp_ms=226500, screen='unknown', evidence='source.png',
                      facts={'performance_points': {'dance': 60, 'passion': None}},
                      ocr={'neural': panel_lines()})
        result = enrich_performance_panels([source])[0]
        self.assertEqual(result['facts']['performance_points'], {'dance': 60, 'passion': None})
        recovery = result['facts']['performance_panel_recovery']
        self.assertEqual(recovery['status'], 'conflict')
        self.assertEqual(recovery['added_fields'], [])
        self.assertEqual(recovery['conflicts']['dance'], {'existing': 60, 'panel': 59})

    def test_enrichment_does_not_create_facts_without_independent_panel_identity(self):
        source = dict(screen='unknown', facts={'keep': True}, effects=[], ocr={'neural': [
            line('Performance', (169, 262, 247, 282)),
        ]})
        result = enrich_performance_panels([source])[0]
        self.assertEqual(result, source)

    def test_unknown_screen_never_promotes_a_projection_to_a_gain(self):
        lines = panel_lines()
        lines.append(line('+18', (260, 294, 315, 329)))
        facts = performance_panel_facts(lines, 'unknown', {})
        self.assertNotIn('projected_performance_gains', facts)
        self.assertNotIn('awarded_performance_gains', facts)

    def test_a_more_badge_over_a_row_is_not_that_row_value(self):
        # A concert bonus draws "8 more" over the Vocal row, and the detector
        # splits it into a number and the word while missing the row's own 4.
        lines = [item for item in panel_lines() if item['text'] != '27']
        lines += [line('8', (202, 404, 216, 418), 100), line('more', (212, 403, 254, 421), 100)]
        facts = performance_panel_facts(lines, 'unknown', {})
        self.assertNotIn('vocal', facts['performance_points'])
        self.assertEqual(facts['performance_points']['dance'], 59)

    def test_a_more_badge_does_not_hide_a_row_whose_value_was_read(self):
        lines = panel_lines()
        lines += [line('8', (202, 404, 216, 418), 100), line('more', (212, 403, 254, 421), 100)]
        facts = performance_panel_facts(lines, 'unknown', {})
        self.assertEqual(facts['performance_points']['vocal'], 27)

    def test_a_covered_row_is_reread_from_below_its_badge(self):
        from tracen_replay.vision import _performance_panel_localized_requests
        lines = [item for item in panel_lines() if item['text'] != '27']
        lines += [line('8', (202, 404, 216, 418), 100), line('more', (212, 403, 254, 421), 100)]
        requests = dict((name, box) for name, box, _meta in _performance_panel_localized_requests(lines))
        self.assertEqual(requests['performance_panel_localized_current.vocal'][1], 421)
        # A row no badge covers keeps the ordinary crop.
        plain = [item for item in panel_lines() if item['text'] != '19']
        requests = dict((name, box) for name, box, _meta in _performance_panel_localized_requests(plain))
        self.assertEqual(requests['performance_panel_localized_current.visual'][1], 488 - 31)

    def test_legacy_training_semantics_remain_screen_gated(self):
        lines = panel_lines()
        dance = next(item for item in lines if item['text'] == '59')
        dance.update(text='59+18', confidence=99)
        facts = performance_panel_facts(lines, 'training_preview', {})
        self.assertEqual(facts['performance_points']['dance'], 59)
        self.assertEqual(facts['projected_performance_gains']['dance'], 18)
        self.assertNotIn('awarded_performance_gains', facts)


if __name__ == '__main__':
    unittest.main()
