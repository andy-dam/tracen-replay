import unittest

from tracen_replay.vision import parse


def line(text, box, confidence=99):
    return dict(text=text, confidence=confidence, box=list(box))


class VisionTrainingIdentityTests(unittest.TestCase):
    def test_result_attaches_same_frame_name_to_existing_selected_option(self):
        raw = dict(
            lines=[
                line('Training', (155, 0, 250, 29)),
                line('Wit Lvl 1', (232, 171, 304, 192)),
                line('Studying', (224, 197, 317, 234)),
            ],
            regions={},
            header='Training',
            current_grid=False,
            result_grid=True,
            inspection='training_result_only',
        )

        parsed = parse(raw)

        self.assertEqual(parsed['screen'], 'training_result')
        self.assertEqual(parsed['training_option'], 'wit')
        self.assertEqual(parsed['facts']['training_name'], 'Studying')
        self.assertEqual(parsed['facts']['training_level'], 1)
        self.assertEqual(
            parsed['facts']['training_identity_evidence']['basis'],
            'same_frame_training_heading_and_name',
        )
        self.assertNotIn('committed', parsed['facts'])
        self.assertNotIn('selected', parsed['facts'])

    def test_transition_frame_keeps_identity_without_committing_an_action(self):
        raw = dict(
            lines=[
                line('Training', (155, 0, 250, 29)),
                line('Wit Lvl 1', (232, 171, 304, 192)),
                line('Studying', (224, 197, 317, 234)),
            ],
            regions={},
            header='Training',
            current_grid=False,
            result_grid=False,
        )

        parsed = parse(raw)

        self.assertEqual(parsed['screen'], 'unknown')
        self.assertEqual(parsed['facts']['training_name'], 'Studying')
        self.assertEqual(parsed['facts']['training_level'], 1)
        self.assertIsNone(parsed['training_option'])
        self.assertNotIn('committed', parsed['facts'])


if __name__ == '__main__':
    unittest.main()
