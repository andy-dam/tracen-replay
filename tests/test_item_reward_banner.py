from copy import deepcopy
import unittest

from tracen_replay.item_reward_banner import read_reward
from tracen_replay.transactions import outcome_events


def raw(name='Sample Carrot Prize'):
    return dict(header='Career', lines=[
        dict(text='Trainee Event', confidence=99, box=[243,171,356,194]),
        dict(text='Raffle Time!', confidence=99, box=[240,204,357,231]),
        dict(text=name, confidence=99, box=[379,680,727,710])])


class ItemRewardBannerTests(unittest.TestCase):
    def test_vision_parse_promotes_verified_banner_into_event_effects(self):
        from tracen_replay.vision import parse
        source = raw()
        source.update(regions={}, current_grid=False, result_grid=False)
        parsed = parse(source)
        self.assertEqual(parsed['screen'], 'event_outcome')
        self.assertEqual(
            [(effect['kind'], effect['name'], effect['quantity'])
             for effect in parsed['effects'] if effect['kind'] == 'item_reward'],
            [('item_reward', 'Sample Carrot Prize', None)],
        )

    def test_literal_name_without_catalog_or_numeric_inference(self):
        for name in ('Sample Carrot Prize', 'Different Unknown Reward'):
            source = raw(name)
            source['source_timestamp_ms'] = 987654
            result = read_reward(source)
            self.assertEqual(result['name'], name)
            self.assertIsNone(result['quantity'])
            self.assertFalse(result['inferred_numeric_effects'])
            self.assertNotIn('amount', result)

    def test_dialogue_or_other_event_does_not_establish_reward(self):
        for title in ('A Day at the Shop', 'Raffle Time?'):
            source = raw()
            source['lines'][1]['text'] = title
            self.assertIsNone(read_reward(source))
        source = raw()
        source['lines'][2]['box'] = [379,820,727,850]
        self.assertIsNone(read_reward(source))

    def test_conflicting_weak_and_incomplete_proofs_abstain(self):
        source = raw()
        source['lines'].append(deepcopy(source['lines'][2]))
        source['lines'][-1]['text'] = 'Other Prize'
        self.assertIsNone(read_reward(source))
        for index in range(3):
            source = raw()
            source['lines'][index]['confidence'] = 96
            self.assertIsNone(read_reward(source))
        source = raw()
        source['lines'][2]['box'][0] = float('nan')
        self.assertIsNone(read_reward(source))

    def test_proof_does_not_alias_input(self):
        source = raw()
        result = read_reward(source)
        source['lines'][2]['text'] = 'Changed'
        self.assertEqual(result['source_proof']['banner']['text'], 'Sample Carrot Prize')

    def test_repeated_banner_is_one_reward_but_later_occurrence_is_separate(self):
        reward = read_reward(raw())
        rows = [dict(source_timestamp_ms=t, evidence=f'{t}.png', screen='event_outcome',
                     context_title='Raffle Time!', effects=[reward], facts={})
                for t in (0, 250, 500, 5000, 5250)]
        events = outcome_events(rows)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(len(event['effects']) == 1 for event in events))
        self.assertEqual(events[0]['effects'][0]['name'], 'Sample Carrot Prize')


if __name__ == '__main__':
    unittest.main()
