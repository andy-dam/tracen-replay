import unittest

from tracen_replay.lesson_offer_adapter import adapt_lesson_offer_frame


def line(text, box):
    return dict(text=text, box=box, confidence=99)


def source(effects):
    return dict(header='Lessons', evidence='frame.png', lines=[
        line('Select a technique or song to learn.', [260, 150, 700, 180]),
        line('Example Technique', [300, 390, 620, 420]),
        *[line(text, [440, 440 + 32 * i, 650, 462 + 32 * i])
          for i, text in enumerate(effects)],
        line('Performance Point Cost', [270, 600, 440, 620]),
        line('12', [550, 600, 570, 620]),
    ])


class PortableLessonOfferTests(unittest.TestCase):
    def test_multifield_offer_keeps_unknown_costs_and_one_purchase_identity(self):
        result = adapt_lesson_offer_frame(source(['Speed +7', 'Wit +9']))
        self.assertEqual(len(result['offers']), 1)
        offer = result['offers'][0]
        self.assertEqual(offer['cost'], {'passion': 12})
        self.assertEqual({effect['field']: effect['amount'] for effect in offer['effects']},
                         {'speed': 7, 'wit': 9})
        self.assertTrue(result['preview_only'])
        self.assertFalse(result['committed'])

    def test_conflicting_same_card_effects_are_not_canonical(self):
        result = adapt_lesson_offer_frame(source(['Speed +7', 'Speed +9']))
        offer = result['offers'][0]
        self.assertEqual(offer['effects'], [])
        self.assertIn('duplicate_effect_field', offer['unknown_reasons'])
        self.assertEqual([row['text'] for row in offer['source_geometry']['unknown_effects']],
                         ['Speed +7','Speed +9'])

    def test_receipt_or_applied_row_cannot_become_preview(self):
        raw = source(['Speed +7'])
        raw['applied'] = True
        self.assertEqual(adapt_lesson_offer_frame(raw)['offers'], [])

    def test_duplicated_field_does_not_discard_independent_effect(self):
        result = adapt_lesson_offer_frame(source(['Speed +7', 'Speed +7', 'Wit +9']))
        offer = result['offers'][0]
        self.assertEqual(offer['effects'], [dict(kind='stat_change',field='wit',amount=9)])
        self.assertIn('duplicate_effect_field',offer['unknown_reasons'])


if __name__ == '__main__':
    unittest.main()
