from copy import deepcopy
import unittest

from tracen_replay.transactions import outcome_events


class OutcomeObservationIsolationTests(unittest.TestCase):
    def rows(self):
        return [dict(source_timestamp_ms=time,evidence=f'{time}.png',screen='event_outcome',
                     effects=[dict(kind='skill_hint_change',name=name,amount=1,
                                   raw_text=f'Gained 1 hint level(s) for {name}.',confidence=99)],
                     facts={},ocr={'neural':[]})
                for time,name in [(100,'Example'),(350,'Example O')]]

    def test_resolving_alternatives_does_not_decorate_source_observations(self):
        rows=self.rows();original=deepcopy(rows)
        events=outcome_events(rows)
        effect=events[0]['effects'][0]
        self.assertEqual(effect['observed_name_candidates'],['Example','Example O'])
        self.assertFalse(effect['circle_variant_verified'])
        self.assertEqual(rows,original)
        self.assertEqual(outcome_events(rows),events)

    def test_result_nested_metadata_does_not_alias_source_or_a_later_reconstruction(self):
        rows=self.rows()
        rows[0]['effects'][0]['observation_proof']={'hashes':['a'*64]}
        original=deepcopy(rows)
        event=outcome_events(rows)[0]
        event['effects'][0]['observation_proof']['hashes'].append('changed')
        self.assertEqual(rows,original)
        self.assertEqual(outcome_events(rows)[0]['effects'][0]['observation_proof'],{'hashes':['a'*64]})
