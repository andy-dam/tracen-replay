import json
import tempfile
import unittest
from pathlib import Path

from tests import localdata
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

    def test_write_returns_the_size(self):
        report = _report()
        with tempfile.TemporaryDirectory() as tmp:
            size = write(report, Path(tmp) / 'timeline.json')
            self.assertGreater(size, 0)
            self.assertTrue((Path(tmp) / 'timeline.json').is_file())

    def test_real_report_when_available(self):
        path = localdata.root("held_out_recording_report", "report.json")
        if not path.is_file():
            self.skipTest("local evidence 'held_out_recording_report' is not present")
        report = json.loads(path.read_text(encoding='utf-8'))
        payload = json.dumps(build(report), ensure_ascii=False, separators=(',', ':'))
        self.assertLess(len(payload.encode('utf-8')), 3_000_000)
        self.assertNotIn('.png', payload)
        self.assertNotIn('.jpg', payload)


if __name__ == '__main__':
    unittest.main()
