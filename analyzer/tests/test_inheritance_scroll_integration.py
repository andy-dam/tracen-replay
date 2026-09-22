"""Scrolling receipt counts retain their own proof and window ownership."""
from copy import deepcopy
import unittest

from tracen_replay.transactions import outcome_events, attach_inheritance_occurrences
from tests.test_inheritance_scroll import effect, source_row


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

    def test_cached_count_cannot_survive_missing_source_continuity(self):
        target, rows, event = case()
        rows['gap'] = source_row(1100, 'gap', target, [], screen='training_preview')
        attach_inheritance_occurrences(event, rows)
        self.assertNotIn('inheritance_scroll_evidence', event)
