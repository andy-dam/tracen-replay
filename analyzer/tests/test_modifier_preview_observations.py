import unittest

from tests.test_preview_observations import reading
from tracen_replay.preview_observations import build_preview_observations


class ModifierPreviewTests(unittest.TestCase):
    def observations(self, effect, screen='lesson_confirmation'):
        rows = [reading(100, [effect], screen=screen, fact_key='projected_effects')]
        return build_preview_observations(rows)['observations']

    def test_typed_concert_modifier_stays_preview(self):
        observation = self.observations(dict(kind='queued_concert_bonus',
            field='specialty_priority', amount=7, awarded=False))[0]
        self.assertEqual(observation['phase'], 'preview')
        self.assertEqual(observation['payload'], dict(kind='training_modifier_change',
            field='specialty_priority', amount=7, timing='after concert'))
        self.assertIsNot(observation.get('awarded'), True)

    def test_training_bonus_is_not_a_stat_award(self):
        observation = self.observations(dict(kind='future_training_modifier',
            field='speed', amount=3, awarded=False))[0]
        self.assertEqual(observation['payload']['kind'], 'training_modifier_change')
        self.assertEqual(observation['payload']['field'], 'speed')

    def test_untyped_raw_text_does_not_supply_amount(self):
        self.assertEqual(self.observations(dict(kind='queued_concert_bonus',
            raw_text='Specialty Priority +7', awarded=False)), [])

    def test_applied_screen_and_awarded_flags_do_not_become_previews(self):
        effect = dict(kind='queued_concert_bonus', field='specialty_priority', amount=7)
        self.assertEqual(self.observations(effect, screen='concert_result'), [])
        self.assertEqual(self.observations(dict(effect, awarded=True)), [])


if __name__ == '__main__':
    unittest.main()
