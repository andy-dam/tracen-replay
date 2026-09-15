import unittest

from tracen_replay.training_outcome import classify_result_screen


def banner(text='SUCCESS!', confidence=99, box=None):
    return dict(text=text, confidence=confidence, box=box or [368, 691, 724, 771])


class TrainingResultScreenTests(unittest.TestCase):
    def test_explicit_result_banner_resolves_missing_grid(self):
        for screen in ('unknown', 'training_result_candidate'):
            for word in ('SUCCESS!', 'FAILURE'):
                self.assertEqual(classify_result_screen([banner(word)], 'Training', screen),
                                 'training_result')

    def test_preview_rate_and_existing_preview_class_are_not_promoted(self):
        self.assertEqual(classify_result_screen(
            [banner('Failure', box=[736, 770, 798, 794])], 'Training', 'unknown'), 'unknown')
        self.assertEqual(classify_result_screen([banner()], 'Training', 'training_preview'),
                         'training_preview')

    def test_weak_misspelled_or_conflicting_banners_do_not_classify(self):
        for lines in ([banner(confidence=93)], [banner('SUOCESS!')],
                      [banner(), banner('FAILURE!')]):
            self.assertEqual(classify_result_screen(lines, 'Training', 'unknown'), 'unknown')

    def test_context_and_full_geometry_are_required(self):
        for header in ('Career', 'Training Tips', ''):
            self.assertEqual(classify_result_screen([banner()], header, 'unknown'), 'unknown')
        self.assertEqual(classify_result_screen([banner(box=[250, 691, 850, 771])],
                                               'Training', 'unknown'), 'unknown')


if __name__ == '__main__':
    unittest.main()
