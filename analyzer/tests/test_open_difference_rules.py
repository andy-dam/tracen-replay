"""Rules that closed the open turn differences: split captions, dim zeros, confirmed names, song glyph variants."""
import unittest

from tracen_replay.gameplay import CURRENCIES, effects_from_lines, garbled_song_receipt
from tracen_replay.lesson_offer_costs import _offer_prices, _offer_readings
from tracen_replay.transactions import (_recoverable_receipt_name_variant, _same_caption, lesson_receipts,
                                        outcome_events, repeated_projection)
from tracen_replay.vision import parse
from tests.test_neural_transactions import line, raw, row


def outcome_row(t, title, effects):
    return dict(source_timestamp_ms=t, evidence=f'{t}.png', screen='event_outcome', context_title=title, stats={},
                effects=[dict(kind='stat_change', field=f, amount=a) for f, a in effects], facts={}, ocr={'neural': []})


class SplitCaptionTests(unittest.TestCase):
    def test_a_caption_read_whole_and_with_its_tail_cut_is_one_caption(self):
        self.assertTrue(_same_caption('After the Mainichi Okan: Onward, to Light', 'After the Mainichi Okan: Onward, to'))
        self.assertTrue(_same_caption('After the Tenno Sho (Autumn): Beyond', 'After the Tenno Sho (Autumn): Beyond Limits'))
        self.assertTrue(_same_caption('The Correlation between Sleep and Effciency', 'The Correlation between Sleep and Efficiency'))
        # The cut can fall inside the caption's last word.
        self.assertTrue(_same_caption('After the NHK Mile C.: A Sharp Turn! Nowhe', 'After the NHK Mile C.: A Sharp Turn! Nowhere.'))
        self.assertFalse(_same_caption('The Correlation between Sleep', 'The Correlation between Sleeping Habits'))
        # A caption's tail alone is not the caption: the receipt's continuity decides that (test_title_continuity).
        self.assertFalse(_same_caption('Ready for a Challenge', 'Challenge'))
        self.assertFalse(_same_caption('Incline', 'Incline Run'))

    def test_a_race_name_read_a_glyph_off_on_one_frame_is_the_name_the_others_read(self):
        from tracen_replay.transactions import _race_name_variants
        reads = ['Queen Elizabeth IIl Cup'] + ['Queen Elizabeth II Cup'] * 6
        self.assertEqual(_race_name_variants(reads), ('Queen Elizabeth II Cup', ['Queen Elizabeth IIl Cup']))
        # Read as often as each other, or more than a glyph apart, the conflict stands.
        self.assertIsNone(_race_name_variants(['Queen Elizabeth IIl Cup', 'Queen Elizabeth II Cup']))
        self.assertIsNone(_race_name_variants(['Queen Elizabeth Cup'] + ['Queen Elizabeth II Cup'] * 6))
        self.assertFalse(_same_caption('After the Mainichi Okan: Onward, to Light', 'After the Tenno Sho (Autumn): Beyond'))
        self.assertFalse(_same_caption('Party Time', 'Party Times'))

    def test_race_reward_receipt_is_one_outcome_across_truncated_frames(self):
        full, cut = 'After the Mainichi Okan: Onward, to Light', 'After the Mainichi Okan: Onward, to'
        rows = [outcome_row(0, full, [('speed', 2)]), outcome_row(250, cut, [('speed', 2), ('power', 4)]),
                outcome_row(500, full, [('speed', 2), ('power', 4), ('guts', 4)]), outcome_row(750, cut, [('guts', 4), ('skill_points', 50)])]
        events = outcome_events(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['context_title'], full)
        self.assertEqual({(e['field'], e['amount']) for e in events[0]['effects']}, {('speed', 2), ('power', 4), ('guts', 4), ('skill_points', 50)})

    def test_a_different_caption_still_starts_another_outcome(self):
        rows = [outcome_row(0, 'An Unseen Summit', [('speed', 2)]), outcome_row(250, 'An Inspiring Streak', [('speed', 3)])]
        self.assertEqual(len(outcome_events(rows)), 2)


class DimZeroTests(unittest.TestCase):
    def test_a_dim_zero_in_a_currency_slot_is_zero(self):
        sample = raw([line('Lessons', (155, 0, 230, 25)), line('Performance Points', (400, 40, 700, 70)),
                      line('39', (343, 87, 391, 123)), line('0', (470, 92, 491, 118), confidence=66),
                      line('0', (572, 90, 597, 120), confidence=78), line('29', (655, 87, 702, 123)), line('24', (758, 87, 807, 123))])
        sample['header'] = 'Lessons'
        got = parse(sample)
        self.assertEqual(got['facts']['performance_points'], dict(dance=39, passion=0, vocal=0, visual=29, composure=24))
        self.assertEqual({d['field'] for d in got['facts']['dim_zero_currency_fields']}, {'passion', 'vocal'})

    def test_a_low_confidence_nonzero_or_a_second_token_stays_unread(self):
        sample = raw([line('Lessons', (155, 0, 230, 25)), line('Performance Points', (400, 40, 700, 70)),
                      line('39', (343, 87, 391, 123)), line('8', (470, 92, 491, 118), confidence=66),
                      line('0', (572, 90, 590, 120), confidence=78), line('0', (591, 90, 597, 120), confidence=70)])
        sample['header'] = 'Lessons'
        got = parse(sample)
        self.assertIsNone(got['facts']['performance_points']['passion'])
        self.assertIsNone(got['facts']['performance_points']['vocal'])

    def test_offer_prices_keep_a_dim_zero_and_treat_other_low_confidence_prices_as_unknown(self):
        def price(field, value, confidence):
            return dict(field=field, value=value, status='accepted', confidence=confidence)
        prices = [price('dance', 0, 89), price('passion', 14, 100), price('vocal', 10, 100), price('visual', 7, 80), price('composure', 25, 100)]
        self.assertEqual(_offer_prices(dict(prices=prices)), dict(dance=0, passion=14, vocal=10, composure=25))

    def test_one_unusable_menu_frame_does_not_discard_the_card(self):
        def offer(title_confidence=99.5, reasons=()):
            return dict(card_index=1, status='complete' if not reasons else 'unknown', unknown_reasons=list(reasons),
                        title=dict(text='Facial-Slimming Massage', confidence=title_confidence),
                        prices=[dict(field=f, value=25 if f == 'composure' else 0, status='accepted', confidence=100) for f in CURRENCIES])
        rows = [dict(source_timestamp_ms=t, evidence=f'{t}.png', screen='lesson_selection', facts=dict(lesson_offer_observations=[o]))
                for t, o in ((0, offer()), (250, offer(title_confidence=95.8, reasons=['uncertain_title'])), (500, offer()))]
        result = _offer_readings(rows, 'Facial-Slimming Massage')
        self.assertIsNotNone(result)
        title_rows, values = result
        self.assertEqual([r['timestamp_ms'] for r in title_rows], [0, 500])
        self.assertEqual([v['value'] for v in values['composure']], [25, 25])


def purchase_rows(receipt_names, kind='named_acquisition', confirmed='Audience Involvement Intermediate Class', blank_frame=False, symbol=False):
    before = dict.fromkeys(CURRENCIES, 100)
    after = dict(before, passion=84)
    rows = [row(t, 'lesson_selection', {'performance_points': before}) for t in (0, 250)]
    rows += [row(500, 'lesson_confirmation', {'name_candidates': [confirmed], 'projected_performance_points': after})]
    if blank_frame:
        rows += [row(600, 'unknown')]
    rows += [row(750, 'lesson_confirmation', {'name_candidates': [confirmed], 'projected_performance_points': after})]
    rows += [row(t, 'lesson_selection', {'performance_points': after}) for t in (2000, 2250)]
    effects = []
    for name in receipt_names:
        effect = dict(kind=kind, name=name)
        if symbol and name.endswith('♪'):
            effect.update(original_text=f'Learned the song "{name[:-2]}".',
                          visual_symbol_observation=dict(method='isolated_note_stem_flag_head_and_closing_quote', symbol='♪', independent_observations=False))
        effects.append(effect)
    event = dict(id='e', first_seen_ms=1000, last_seen_ms=1500, evidence='receipt.png', effects=effects, deltas={})
    return rows, event


class ConfirmedNameTests(unittest.TestCase):
    def test_receipt_spellings_that_are_all_variants_of_the_confirmed_name_are_that_purchase(self):
        rows, event = purchase_rows(['Audience Involvement Iermediate Class', 'Audience Involvement Inrmediate Class'])
        got = lesson_receipts(rows, [event])
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]['name'], got[0]['performance_cost']['passion']), ('Audience Involvement Intermediate Class', 16))
        self.assertEqual(event['resolved_acquisition_name_variants'][0]['basis'], 'confirmed_request_name_with_bounded_ocr_variants')

    def test_a_mangled_first_word_collapses_beside_an_exact_spelling(self):
        rows, event = purchase_rows(['Composure Training Basics', 'Compduire Training Basics', 'Compos Training Basics'], confirmed='Composure Training Basics')
        got = lesson_receipts(rows, [event])
        self.assertEqual([p['name'] for p in got], ['Composure Training Basics'])
        self.assertTrue(_recoverable_receipt_name_variant('Composure Training Basics', 'Compduire Training Basics', anchored=True))
        self.assertFalse(_recoverable_receipt_name_variant('Composure Training Basics', 'Compduire Training Basics'))

    def test_an_extra_word_or_a_digit_is_another_item(self):
        self.assertFalse(_recoverable_receipt_name_variant('Hoppity Sunny Days', 'Hoppity Sunny Days D', anchored=True))
        self.assertFalse(_recoverable_receipt_name_variant('Present March', 'Present March 2', anchored=True))
        rows, event = purchase_rows(['Audience Involvement Intermediate Class D'])
        self.assertEqual(lesson_receipts(rows, [event]), [])

    def test_a_space_inside_a_word_is_a_misread_not_an_extra_word(self):
        # "Advanced" read as "Ad nced": the recognizer split one word and lost
        # two letters. Counting words alone called that a different item and
        # threw the whole purchase away, cost included.
        self.assertTrue(_recoverable_receipt_name_variant(
            'Composure Training Advanced Class', 'Composure Training Ad nced Class', anchored=True))
        # A split that gains characters is still another item.
        self.assertFalse(_recoverable_receipt_name_variant(
            'Composure Training Advanced Class', 'Composure Training Advanced Cla ss X', anchored=True))
        rows, event = purchase_rows(['Audience Involvement In ermediate Class',
                                     'Audience Involvement Inrmediate Class'])
        got = lesson_receipts(rows, [event])
        self.assertEqual([p['name'] for p in got], ['Audience Involvement Intermediate Class'])
        self.assertEqual(got[0]['performance_cost']['passion'], 16)

    def test_a_card_opened_and_left_earlier_does_not_block_the_receipt(self):
        # The player opens one card, backs out, then buys another inside the
        # same five seconds. Only the last request run is this receipt's; the
        # abandoned one used to make the name ambiguous and drop the purchase.
        before = dict.fromkeys(CURRENCIES, 100)
        after = dict(before, passion=84)
        rows = [row(t, 'lesson_selection', {'performance_points': before}) for t in (0, 250)]
        rows += [row(t, 'lesson_confirmation', {'name_candidates': ['Facial-Slimming Massage']})
                 for t in (500, 750)]
        rows += [row(t, 'lesson_selection', {'performance_points': before}) for t in (1500, 1750)]
        rows += [row(t, 'lesson_confirmation', {'name_candidates': ['Audience Involvement Intermediate Class'],
                                                'projected_performance_points': after}) for t in (2000, 2250)]
        rows += [row(t, 'lesson_selection', {'performance_points': after}) for t in (3500, 3750)]
        event = dict(id='e', first_seen_ms=2500, last_seen_ms=3000, evidence='receipt.png', deltas={},
                     effects=[dict(kind='named_acquisition', name='Audience Involvement Iermediate Class')])
        got = lesson_receipts(rows, [event])
        self.assertEqual([p['name'] for p in got], ['Audience Involvement Intermediate Class'])
        self.assertEqual(got[0]['performance_cost']['passion'], 16)

    def test_two_names_inside_the_one_request_run_still_leave_the_receipt_alone(self):
        rows, event = purchase_rows(['Audience Involvement Iermediate Class'])
        rows += [row(600, 'lesson_confirmation', {'name_candidates': ['Facial-Slimming Massage']})]
        rows.sort(key=lambda r: r['source_timestamp_ms'])
        self.assertEqual(lesson_receipts(rows, [event]), [])

    def test_a_single_glitching_dialog_frame_loses_to_the_repeated_projection(self):
        rows, event = purchase_rows(['Audience Involvement Intermediate Class'])
        rows += [row(700, 'lesson_confirmation', {'name_candidates': ['Audience Involvement Intermediate Class'],
                                                  'projected_performance_points': dict(dict.fromkeys(CURRENCIES, 100), passion=84, composure=0)})]
        rows.sort(key=lambda r: r['source_timestamp_ms'])
        got = lesson_receipts(rows, [event])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['performance_cost'], dict(dance=0, passion=16, vocal=0, visual=0, composure=0))
        self.assertEqual(repeated_projection([r for r in rows if r['screen'] == 'lesson_confirmation'])['composure'], 100)
        # Two repeated, disagreeing readings stay unresolved.
        self.assertIsNone(repeated_projection([row(t, 'lesson_confirmation', {'projected_performance_points': dict(composure=v)})
                                               for t, v in ((0, 5), (1, 5), (2, 9), (3, 9))])['composure'])

    def test_a_blank_learn_press_frame_does_not_end_the_request_run(self):
        rows, event = purchase_rows(['Audience Involvement Intermediate Class'], blank_frame=True)
        got = lesson_receipts(rows, [event])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['performance_cost']['passion'], 16)


class GarbledSongPhraseTests(unittest.TestCase):
    def test_the_quoted_title_anchors_a_song_receipt_whose_phrase_was_misread(self):
        for text in ('Learned the ong "Ring Ring Diary".', 'Learnd te song "Ring Ring Diary".', 'Learned the "Ring Ring Diary".'):
            effects = effects_from_lines([dict(text=text, confidence=99, box=[306, 785, 700, 814])])
            self.assertEqual([(e['kind'], e['name'], e.get('text_normalization')) for e in effects],
                             [('song_learned', 'Ring Ring Diary', 'fixed_phrase_repair')], text)

    def test_other_quoted_receipts_and_low_confidence_lines_are_not_songs(self):
        for text in ('Acquired "Practice Perfect".', 'Unlocked recreation with "Gold Ship".', 'Leaned "on".'):
            kinds = [e['kind'] for e in effects_from_lines([dict(text=text, confidence=99, box=[306, 785, 700, 814])])]
            self.assertNotIn('song_learned', kinds, text)
        low = effects_from_lines([dict(text='Learnd te song "Ring Ring Diary".', confidence=85, box=[306, 785, 700, 814])])
        self.assertEqual(low, [])
        self.assertIsNone(garbled_song_receipt('Learned the song "Ring Ring Diary".'))


class SongGlyphVariantTests(unittest.TestCase):
    def test_the_stray_letter_read_of_a_proven_note_glyph_is_dropped(self):
        rows, event = purchase_rows(['Present March D', 'Present March ♪'], kind='song_learned', confirmed='Present March', symbol=True)
        got = lesson_receipts(rows, [event])
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]['name'], got[0]['performance_cost']['passion']), ('Present March ♪', 16))
        self.assertEqual([e['name'] for e in event['effects'] if e['kind'] == 'song_learned'], ['Present March ♪'])
        self.assertEqual(event['resolved_acquisition_name_variants'][0]['basis'], 'proven_note_glyph_with_stray_letter_variant')

    def test_two_unproven_song_spellings_are_left_alone(self):
        rows, event = purchase_rows(['Present March D', 'Present March B'], kind='song_learned', confirmed='Present March')
        self.assertEqual(lesson_receipts(rows, [event]), [])
        self.assertEqual(len([e for e in event['effects'] if e['kind'] == 'song_learned']), 2)


if __name__ == '__main__':
    unittest.main()
