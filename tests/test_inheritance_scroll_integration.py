"""Scrolling receipt counts retain their own proof and window ownership."""
from copy import deepcopy
import unittest

from tracen_replay.effect_evaluate import evaluate
from tracen_replay.transactions import outcome_events, attach_inheritance_occurrences
from test_inheritance_scroll import effect, source_row
from test_inheritance_effect_evaluate import reference, report


def case():
    target = effect('Power')
    rows = {
        'a': source_row(1000, 'a', target, [800, 880], anchor_centers=(840, 900)),
        'b': source_row(1250, 'b', target, [840, 930], anchor_centers=(800, 860)),
        'c': source_row(1500, 'c', target, [840, 930], anchor_centers=(800, 860)),
    }
    return target, rows, outcome_events(list(rows.values()))[0]


class InheritanceScrollIntegrationTests(unittest.TestCase):
    def test_report_keeps_simultaneous_and_tracked_counts_separate(self):
        target, rows, event = case()
        key = 'inheritance_spark||Power'
        simultaneous = event['inheritance_occurrence_evidence']['by_key'][key]
        tracked = event['inheritance_scroll_evidence']['by_key'][key]
        self.assertEqual(simultaneous['minimum_observed_count'], 2)
        self.assertEqual(tracked['minimum_observed_count'], 3)
        self.assertIsNone(tracked['total_count'])
        self.assertFalse(tracked['count_complete'])
        self.assertEqual(event['deltas'], {})
        self.assertEqual(len(event['effects']), 1)
        before = deepcopy(event)
        attach_inheritance_occurrences(event, rows)
        self.assertEqual(event, before)

    def test_default_evaluation_stays_one_and_opt_in_counts_three(self):
        target, rows, event = case()
        ref = reference([deepcopy(target)] * 3, group_start=1000, group_end=1750, end_ms=2000)
        value = report([event], rows)
        before = deepcopy(value)
        self.assertEqual(evaluate(ref, value)['predicted'], 1)
        result = evaluate(dict(ref, include_inheritance_occurrences=True), value)
        self.assertEqual(result['matched'], 3)
        self.assertEqual(result['predicted'], 3)
        self.assertEqual(value, before)

    def test_new_entry_owns_later_window_and_is_not_recounted(self):
        target, rows, event = case()
        for start, end, count in ((0, 1250, 2), (1250, 1500, 1), (1500, 2000, 0)):
            with self.subTest(start=start):
                ref = reference([deepcopy(target)] * count,
                                timing_basis='first_exact_effect_observation',
                                group_start=max(1000, start), group_end=end-250,
                                start_ms=start, end_ms=end)
                if not count:
                    ref['groups'] = []
                result = evaluate(dict(ref, include_inheritance_occurrences=True), report([event], rows))
                self.assertEqual(result['predicted'], count)
                self.assertEqual(result['matched'], count)
                if start == 1250:
                    occurrence = result['inheritance_occurrence_diagnostics'][0]['occurrence']
                    self.assertEqual(occurrence['ordinal'], 3)
                    self.assertEqual(occurrence['witness']['source_timestamp_ms'], 1250)
                    self.assertEqual(occurrence['witness']['basis'], 'source_tracked_scroll')

    def test_cached_count_cannot_survive_missing_source_continuity(self):
        target, rows, event = case()
        rows['gap'] = source_row(1100, 'gap', target, [], screen='training_preview')
        ref = reference([deepcopy(target)] * 2, group_start=1000, group_end=1750, end_ms=2000)
        result = evaluate(dict(ref, include_inheritance_occurrences=True), report([event], rows))
        self.assertEqual(result['predicted'], 2)
        attach_inheritance_occurrences(event, rows)
        self.assertNotIn('inheritance_scroll_evidence', event)
