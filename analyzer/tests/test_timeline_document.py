import json
import tempfile
import unittest
from pathlib import Path

from tests.test_report_contract import valid_report
from tracen_replay.timeline_document import SCHEMA, build, write


def _report():
    report = valid_report()
    report['gameplay_tracking']['readings'] = []
    report['gameplay_tracking']['performance_accounting'] = {'checkpoints': [], 'intervals': []}
    from tracen_replay.turn_ledger import build as build_ledger
    report['turn_ledger'] = build_ledger(report)
    from tracen_replay.causal_accounting import build as build_accounting
    try:
        report['causal_accounting'] = build_accounting(report)
    except (ValueError, KeyError):
        pass
    return report


class TimelineDocumentTests(unittest.TestCase):
    def test_document_is_a_subset_with_timestamps_and_no_image_paths(self):
        report = _report()
        document = build(report)
        self.assertEqual(document['schema_version'], SCHEMA)
        self.assertEqual(document['source']['sha256'], report['source']['sha256'])
        self.assertEqual(len(document['entries']), len(report['turn_ledger']['timeline']))
        self.assertEqual(len(document['turns']), len(report['turn_ledger']['turns']))
        text = json.dumps(document)
        for marker in ('.png', '.jpg', 'gameplay/', 'part-0'):
            self.assertNotIn(marker, text)
        for entry, source in zip(document['entries'], report['turn_ledger']['timeline']):
            self.assertEqual((entry['kind'], entry['first_seen_ms'], entry['last_seen_ms']),
                             (source['kind'], source['first_seen_ms'], source['last_seen_ms']))

    def test_a_card_only_commit_does_not_repeat_its_trainings_assigned_amounts(self):
        # A training committed from its result card alone: its committed action
        # and its result entry point at the same event.
        report = _report()
        event = '/gameplay_tracking/events/0'
        common = dict(first_seen_ms=100, last_seen_ms=100, assignment_basis='observed_within_calendar_window')
        report['turn_ledger']['timeline'] = [
            dict(common, id='entry-action', kind='committed_action', source_ref=event, action_kind='training',
                 identity_basis='result_card_only'),
            dict(common, id='entry-result', kind='training', source_ref=event)]
        report['causal_accounting'] = dict(report.get('causal_accounting') or {}, contributions=[
            dict(id=f'{event}/learned_reader_gains/speed', event_ref=event, channel='stats', field='speed', amount=9,
                 basis='observed_learned_training_gain'),
            dict(id=f'{event}/turn_difference_gains/wit', event_ref=event, channel='stats', field='wit', amount=4,
                 basis='turn_difference')])
        entries = {e['id']: e for e in build(report)['entries']}
        self.assertNotIn('changes', entries['entry-action'])
        self.assertEqual(entries['entry-result']['changes'], {'stats': {
            'speed': {'amount': 9, 'basis': 'observed_learned_training_gain'},
            'wit': {'amount': 4, 'basis': 'turn_difference'}}})

    def test_a_worked_out_amount_carries_what_the_card_showed_instead(self):
        report = _report()
        event = '/gameplay_tracking/events/0'
        report['turn_ledger']['timeline'] = [
            dict(id='entry-result', kind='training', source_ref=event, first_seen_ms=100, last_seen_ms=100,
                 assignment_basis='observed_within_calendar_window')]
        report['causal_accounting'] = dict(report.get('causal_accounting') or {}, contributions=[
            dict(id=f'{event}/turn_difference_gains/skill_points', event_ref=event, channel='stats',
                 field='skill_points', amount=13, basis='turn_difference',
                 contradicted_reads=[dict(gain=18, evidence=['a.png', 'b.png'])])])
        entries = {e['id']: e for e in build(report)['entries']}
        self.assertEqual(entries['entry-result']['changes'], {'stats': {
            'skill_points': {'amount': 13, 'basis': 'turn_difference', 'contradicted_by': [18]}}})

    def test_write_returns_the_size(self):
        report = _report()
        with tempfile.TemporaryDirectory() as tmp:
            size = write(report, Path(tmp) / 'timeline.json')
            self.assertGreater(size, 0)
            self.assertTrue((Path(tmp) / 'timeline.json').is_file())

    def test_a_disputed_performance_row_settled_by_the_bars_is_no_longer_a_conflict(self):
        # The card's Composure row was read as 1 and 12; the turn difference
        # settled it at 12, so the entry carries the amount with the other
        # read beside it and is not flagged.
        report = _report()
        ref = '/gameplay_tracking/events/0'
        events = report['gameplay_tracking'].setdefault('events', [])
        if not events:
            events.append({})
        events[0].update(kind='training', performance_reading_conflicts={'composure': [1, 12]},
                         settled_conflicting_readings={'composure': dict(amount=12, reads=[1, 12], settled_by='turn_difference')})
        report['turn_ledger']['timeline'] = [
            dict(id='entry-result', kind='training', source_ref=ref, first_seen_ms=100, last_seen_ms=100,
                 assignment_basis='observed_within_calendar_window', conflicts_present=True)]
        report['causal_accounting'] = dict(report.get('causal_accounting') or {}, contributions=[
            dict(id=f'{ref}/turn_difference_performance_gains/composure', event_ref=ref, channel='performance',
                 field='composure', amount=12, basis='turn_difference')])
        entry = build(report)['entries'][0]
        self.assertFalse(entry.get('conflicts_present'))
        change = entry['changes']['performance']['composure']
        self.assertEqual((change['amount'], change['disagreeing_reads']), (12, [1]))
        # Unsettled, the row keeps the flag.
        events[0].pop('settled_conflicting_readings')
        entry = build(report)['entries'][0]
        self.assertTrue(entry.get('conflicts_present'))

    def test_a_disputed_badge_settled_by_the_stat_bars_is_no_longer_a_conflict(self):
        # The card was read as +1, +12 and +18 for speed; the accounting settled
        # it at 12, one of those reads, so the entry carries the amount with the
        # other reads beside it and is not flagged.
        report = _report()
        ref = '/gameplay_tracking/events/0'
        events = report['gameplay_tracking'].setdefault('events', [])
        if not events:
            events.append({})
        events[0].update(kind='training', conflicting_readings={'speed': [1, 12, 18]},
                         settled_conflicting_readings={'speed': dict(amount=12, reads=[1, 12, 18], settled_by='learned_reader_on_card')})
        report['turn_ledger']['timeline'] = [
            dict(id='entry-result', kind='training', source_ref=ref, first_seen_ms=100, last_seen_ms=100,
                 assignment_basis='observed_within_calendar_window', conflicts_present=True)]
        report['causal_accounting'] = dict(report.get('causal_accounting') or {}, contributions=[
            dict(id=f'{ref}/learned_reader_gains/speed', event_ref=ref, channel='stats', field='speed', amount=12,
                 basis='observed_learned_training_gain')])
        entry = build(report)['entries'][0]
        self.assertFalse(entry.get('conflicts_present'))
        self.assertEqual(entry['changes'], {'stats': {'speed': {
            'amount': 12, 'basis': 'observed_learned_training_gain', 'disagreeing_reads': [1, 18]}}})

        # Anything short of a settlement of every disputed field keeps the flag.
        def flagged(mutate):
            doc = json.loads(json.dumps(report))
            mutate(doc['gameplay_tracking']['events'][0], doc)
            entry = build(doc)['entries'][0]
            return entry.get('conflicts_present'), 'disagreeing_reads' in entry['changes']['stats']['speed']
        cases = {
            'no settlement recorded': lambda e, d: e.pop('settled_conflicting_readings'),
            'settled at another amount': lambda e, d: e['settled_conflicting_readings']['speed'].update(amount=18),
            'a second field unsettled': lambda e, d: e['conflicting_readings'].update(guts=[4, 5]),
            'the card contradicts a worked-out amount': lambda e, d: e.update(contradicted_turn_difference={'guts': {}}),
            'an ambiguous effect on the same event': lambda e, d: e.update(ambiguous_effect_candidates=[{}]),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.assertEqual(flagged(mutate), (True, False))



if __name__ == '__main__':
    unittest.main()
