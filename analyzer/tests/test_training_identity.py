import unittest

from tracen_replay.training_identity import read_identity, summarize


def lines(name='Studying', option='Wit'):
    return [dict(text=option + ' Lvl 1', confidence=97.08, box=[232, 171, 304, 192]),
            dict(text=name, confidence=97.329, box=[224, 197, 317, 234])]


class TrainingIdentityTests(unittest.TestCase):
    def test_visible_name_is_not_a_fixed_option_name_mapping(self):
        for name in ('Studying', 'Video Research', 'Push-Button Quiz', 'Novel Training Name'):
            result = read_identity(lines(name), 'training_result', 'Wit')
            self.assertEqual(result['training_name'], name)
            self.assertEqual(result['training_level'], 1)

    def test_wrong_option_or_non_training_screen_does_not_supply_identity(self):
        self.assertEqual(read_identity(lines(), 'training_result', 'Speed'), {})
        self.assertEqual(read_identity(lines(), 'career_hub', 'Wit'), {})

    def test_missing_conflicting_or_unreadable_name_stays_unknown(self):
        source = lines()
        self.assertEqual(read_identity(source[:1], 'training_result', 'Wit'), {})
        self.assertEqual(read_identity(source + lines('Other')[1:], 'training_result', 'Wit'), {})
        source[1]['confidence'] = 80
        self.assertEqual(read_identity(source, 'training_result', 'Wit'), {})

    def test_preview_identity_does_not_claim_commitment(self):
        result = read_identity(lines(), 'training_preview', 'Wit')
        self.assertEqual(result['training_name'], 'Studying')
        self.assertNotIn('committed', result)
        self.assertNotIn('selected', result)

    def test_summary_uses_only_matching_result_rows(self):
        row = dict(screen='training_result', training_option='Wit', source_timestamp_ms=500,
                   evidence='result.png', facts=read_identity(lines(), 'training_result', 'Wit'))
        preview = dict(row, screen='training_preview', evidence='preview.png',
                       facts=read_identity(lines('Other'), 'training_preview', 'Wit'))
        other = dict(row, training_option='Speed', evidence='other.png')
        result = summarize([preview, other, row], 'Wit')
        self.assertEqual(result['training_name'], 'Studying')
        self.assertEqual(result['training_name_evidence'], ['result.png'])

    def test_summary_preserves_conflicts_and_rejects_unproven_names(self):
        def row(name, timestamp):
            return dict(screen='training_result', training_option='Wit',
                        source_timestamp_ms=timestamp, evidence=f'{timestamp}.png',
                        facts=read_identity(lines(name), 'training_result', 'Wit'))
        result = summarize([row('Studying', 500), row('Other', 600)], 'Wit')
        self.assertNotIn('training_name', result)
        self.assertEqual(result['training_name_conflicts'], ['Other', 'Studying'])
        invalid = row('Studying', 700)
        invalid['facts']['training_name'] = 'Invented'
        self.assertEqual(summarize([invalid], 'Wit'), {})


if __name__ == '__main__':
    unittest.main()
