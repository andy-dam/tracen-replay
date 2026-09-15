import unittest

from tracen_replay.training_identity import read_identity, summarize


def observed(timestamp, heading_confidence, name_confidence, name='Novel Exercise',
             screen='training_result', evidence=None):
    lines = [dict(text='Wit Lvl 1', confidence=heading_confidence, box=[229, 167, 311, 194]),
             dict(text=name, confidence=name_confidence, box=[223, 196, 418, 235])]
    return dict(screen=screen, training_option='wit', source_timestamp_ms=timestamp,
                evidence=evidence or f'gameplay/{timestamp}.png',
                facts=read_identity(lines, screen, 'Wit'))


class TrainingIdentityCorroborationTests(unittest.TestCase):
    def test_complete_pairs_corroborate_complementary_confidence(self):
        rows = [observed(100, 99.504, 93.021), observed(350, 95.961, 99.997)]
        self.assertNotIn('training_name', rows[0]['facts'])
        result = summarize(rows, 'wit')
        self.assertEqual(result['training_name'], 'Novel Exercise')
        self.assertEqual(len(result['training_name_evidence']), 2)
        self.assertTrue(all(item['basis'] == 'repeated_complete_training_identity'
                            for item in result['training_name_observations']))

    def test_single_pair_or_repeated_weak_name_is_insufficient(self):
        for rows in ([observed(100, 99, 93)],
                     [observed(100, 99, 93), observed(350, 99, 94)]):
            self.assertNotIn('training_name', summarize(rows, 'wit'))

    def test_different_names_do_not_supply_each_others_confidence(self):
        self.assertNotIn('training_name', summarize([
            observed(100, 99, 93), observed(350, 95, 99, name='Other Exercise')], 'wit'))

    def test_preview_or_other_option_cannot_corroborate_result(self):
        other_option = observed(350, 95, 99)
        other_option['training_option'] = 'speed'
        for second in (observed(350, 95, 99, screen='training_preview'), other_option):
            self.assertNotIn('training_name', summarize([observed(100, 99, 93), second], 'wit'))

    def test_repeated_physical_source_is_not_independent(self):
        for mode in ('time', 'path', 'hash'):
            rows = [observed(100, 99, 93), observed(350, 95, 99)]
            if mode == 'time':
                rows[1]['source_timestamp_ms'] = 100
            elif mode == 'path':
                rows[1]['evidence'] = 'GAMEPLAY\\100.png'
            else:
                for row in rows:
                    row['source_frame_sha256'] = 'a' * 64
            self.assertNotIn('training_name', summarize(rows, 'wit'))

    def test_changed_candidate_payload_cannot_inherit_proof(self):
        rows = [observed(100, 99, 93), observed(350, 95, 99)]
        rows[1]['facts']['training_identity_candidate']['training_name'] = 'Invented'
        self.assertNotIn('training_name', summarize(rows, 'wit'))


if __name__ == '__main__':
    unittest.main()
