import unittest

from tracen_replay.training_outcome import banner_facts, summarize


def row(timestamp, text, confidence=95, evidence=None, screen='training_result'):
    lines = [dict(text=text, confidence=confidence, box=[355, 665, 765, 785])]
    return dict(source_timestamp_ms=timestamp, evidence=evidence or f'frame-{timestamp}.png',
                facts=banner_facts(lines, screen))


class TrainingOutcomeCorroborationTests(unittest.TestCase):
    def test_exact_and_one_substitution_on_separate_frames(self):
        result = summarize([row(100, 'SUCCESS'), row(125, 'SUCCFSS!')])
        self.assertEqual(result['training_outcome'], 'success')
        self.assertEqual(len(result['success_evidence']), 2)
        self.assertTrue(all(o['basis'] == 'corroborated_fixed_result_word'
                            for o in result['outcome_observations']))

    def test_failure_uses_same_rule(self):
        self.assertEqual(summarize([row(100, 'FAILURE'), row(125, 'FAILVRE!')])
                         ['training_outcome'], 'failure')

    def test_single_frame_weak_reading_remains_unknown(self):
        self.assertEqual(summarize([row(100, 'SUCCESS')])['training_outcome'], 'unknown')

    def test_duplicate_time_or_evidence_cannot_corroborate(self):
        for rows in ([row(100, 'SUCCESS'), row(100, 'SUCCFSS!', evidence='other.png')],
                     [row(100, 'SUCCESS', evidence='same.png'),
                      row(125, 'SUCCFSS!', evidence='same.png')]):
            self.assertEqual(summarize(rows)['training_outcome'], 'unknown')

    def test_no_exact_word_cannot_corroborate(self):
        self.assertEqual(summarize([row(100, 'SUCCFSS'), row(125, 'SUCCLSS')])
                         ['training_outcome'], 'unknown')

    def test_same_source_pixels_cannot_inflate_repetition(self):
        rows = [row(100, 'SUCCESS'), row(125, 'SUCCFSS!')]
        for observed in rows:
            observed['source_frame_sha256'] = 'a' * 64
        self.assertEqual(summarize(rows)['training_outcome'], 'unknown')

    def test_path_aliases_and_invalid_paths_cannot_corroborate(self):
        for first, second in (('gameplay/frame.png', 'GAMEPLAY\\frame.png'),
                              ('frame.png?one', 'frame.png?two'),
                              ('../frame.png', 'elsewhere.png')):
            self.assertEqual(summarize([row(100, 'SUCCESS', evidence=first),
                                        row(125, 'SUCCESS', evidence=second)])
                             ['training_outcome'], 'unknown')

    def test_preview_words_and_low_confidence_do_not_count(self):
        for second in (row(125, 'SUCCESS', screen='training_preview'),
                       row(125, 'SUCCESS', confidence=80)):
            self.assertEqual(summarize([row(100, 'SUCCESS'), second])['training_outcome'], 'unknown')

    def test_opposing_corroborated_outcomes_remain_conflicted(self):
        result = summarize([row(100, 'SUCCESS'), row(125, 'SUCCESS'),
                            row(150, 'FAILURE'), row(175, 'FAILURE')])
        self.assertEqual(result['training_outcome'], 'unknown')
        self.assertEqual(set(result['training_outcome_conflicts']), {'success', 'failure'})


if __name__ == '__main__':
    unittest.main()
