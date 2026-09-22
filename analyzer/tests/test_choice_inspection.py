import json
import unittest
from pathlib import Path
from tests import test_choice_evidence as choice_tests
from tracen_replay.choice_evidence import collect,reconstruct
from tracen_replay.transactions import reconstruct as transactions


class ChoiceInspectionTests(unittest.TestCase):
    def test_source_dense_response_regression(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/choice-dense-response-v1.json').read_text(encoding='utf-8'))
        events=reconstruct(fixture['observations'])
        self.assertEqual([{k:e[k] for k in ('options','selected_index','selected_text','selection_observed_ms')} for e in events],fixture['expected'])
        self.assertEqual(events[0]['kind'],'dialogue_response')

    def test_duplicates_do_not_create_repeated_menu_evidence(self):
        row=choice_tests.ChoiceEvidenceTests().observations()[0]
        base=[dict(screen='unknown',source_timestamp_ms=0,evidence=row['evidence'],facts={'choice_observation':row})]
        self.assertEqual(len(collect(base,[row])),1)
        self.assertEqual(reconstruct(collect(base,[row,choice_tests.ChoiceEvidenceTests().observations()[-1]])),[])

    def test_known_screen_boundary_wins_in_both_directions(self):
        row=choice_tests.ChoiceEvidenceTests().observations()[0]
        base=[dict(screen='event_outcome',source_timestamp_ms=0)]
        self.assertTrue(collect(base,[row])[0]['screen_boundary'])
        base=[dict(screen='unknown',source_timestamp_ms=0,evidence='a.png',facts={'choice_observation':row})]
        self.assertTrue(collect(base,[dict(source_timestamp_ms=0,screen_boundary=True)])[0]['screen_boundary'])
        self.assertTrue(collect([dict(screen='event_outcome',source_timestamp_ms=0)]+base)[0]['screen_boundary'])

    def test_choice_only_inspection_cannot_change_accounting(self):
        before=transactions([]);after=transactions([],choice_tests.ChoiceEvidenceTests().observations())
        self.assertEqual(len(after.pop('dialogue_choices')),1)
        before.pop('dialogue_choices')
        self.assertEqual(before,after)
