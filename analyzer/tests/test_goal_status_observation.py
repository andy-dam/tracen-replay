import copy
import json
from pathlib import Path
import unittest

from tracen_replay.vision import parse


class GoalStatusObservationTests(unittest.TestCase):
    def setUp(self):
        self.raw=json.loads(Path('analyzer/tests/fixtures/goal-status-333750.json').read_text(encoding='utf-8'))

    def test_source_status_is_a_fact_and_not_an_award(self):
        row=parse(self.raw)
        fact=row['facts']['goal_status_observation']
        self.assertEqual(fact['status'],'achieved')
        self.assertEqual(fact['box'],[447,87,584,113])
        self.assertEqual(row['effects'],[])
        self.assertIsNone(row['completed_action'])

    def test_other_positions_partial_text_and_low_confidence_abstain(self):
        for replacement in (
            dict(box=[447,200,584,226]),
            dict(text='Goal Achieved'),
            dict(text='Goal Achieved?'),
            dict(text='Earn 3000 fans'),
            dict(confidence=94.9),
        ):
            raw=copy.deepcopy(self.raw)
            raw['lines']=[dict(l,**replacement) if l['text']=='Goal Achieved!' else l for l in raw['lines']]
            self.assertNotIn('goal_status_observation',parse(raw)['facts'])

    def test_duplicate_detection_does_not_supply_independent_support(self):
        raw=copy.deepcopy(self.raw)
        raw['lines'].append(next(l for l in raw['lines'] if l['text']=='Goal Achieved!'))
        self.assertNotIn('goal_status_observation',parse(raw)['facts'])
