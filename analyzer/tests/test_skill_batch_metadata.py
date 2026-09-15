import unittest

from tests.test_neural_transactions import line, row
from tracen_replay.transactions import skill_transactions


def _source_row(timestamp, screen, facts=None, *, evidence=None, **kwargs):
    result = row(timestamp, screen, facts, **kwargs)
    if evidence is not None:
        result['evidence'] = evidence
    return result


def _batch_rows(*, post_repeat=True, post_after_receipt=True, price_repeat=True,
                receipt_lines=True, explicit_price_status=None,
                shared_evidence=False):
    known_cards = [
        dict(name='Known Skill', displayed_cost=300,
             menu_status='available', variant='single_circle'),
        dict(name='Other Skill', displayed_cost=400,
             menu_status='available', variant='single_circle'),
    ]
    rows = [
        _source_row(250, 'skill_selection', {
            'displayed_skill_points': 1000,
            'item_list_complete': False,
            'skill_cards': known_cards,
        }, evidence='same-price.png' if shared_evidence else '250.png'),
    ]
    if price_repeat:
        rows.append(_source_row(500, 'skill_selection', {
            'displayed_skill_points': 1000,
            'item_list_complete': False,
            'skill_cards': known_cards,
        }, evidence='same-price.png' if shared_evidence else '500.png'))
    rows.extend([
        row(750, 'skill_confirmation', {
            'visible_skill_names': ['Known Skill'],
            'item_list_complete': False,
            **({'price_status': explicit_price_status}
               if explicit_price_status is not None else {}),
        }),
        row(900, 'skill_confirmation', {
            'visible_skill_names': ['Known Skill'],
            'item_list_complete': False,
            **({'price_status': explicit_price_status}
               if explicit_price_status is not None else {}),
        }),
    ])
    post = _source_row(1000, 'skill_selection', {
        'skill_cards': [dict(name='Later Skill', displayed_cost=None,
                             menu_status='obtained_or_selected')],
    }, evidence='post.png' if shared_evidence else ('2000.png' if post_after_receipt else '1000.png'))
    post['source_timestamp_ms'] = 2000 if post_after_receipt else 1000
    rows.append(post)
    if post_repeat:
        second_post = _source_row(2250 if post_after_receipt else 1250, 'skill_selection', {
            'skill_cards': [dict(name='Later Skill', displayed_cost=None,
                                 menu_status='obtained_or_selected')],
        }, evidence='post.png' if shared_evidence else ('2250.png' if post_after_receipt else '1250.png'))
        rows.append(second_post)
    if not post_after_receipt:
        post_rows = rows[-(2 if post_repeat else 1):]
        rows = rows[:-len(post_rows)]
        receipt_ocr = {}
        if receipt_lines:
            receipt_ocr = {'neural': [line('our trainee learned new skills!', confidence=98)]}
        rows.extend([row(1500, 'skill_receipt', {}, ocr=receipt_ocr)])
        if receipt_lines:
            rows.append(row(1750, 'skill_receipt', {}, ocr={
                'neural': [line('Your trainee learned new skills!', confidence=99)],
            }))
        rows.extend(post_rows)
        return sorted(rows, key=lambda item: item['source_timestamp_ms'])
    receipt_ocr = {}
    if receipt_lines:
        receipt_ocr = {'neural': [line('our trainee learned new skills!', confidence=98)]}
    rows.append(row(1500, 'skill_receipt', {}, ocr=receipt_ocr))
    if receipt_lines:
        rows.append(row(1750, 'skill_receipt', {}, ocr={
            'neural': [line('Your trainee learned new skills!', confidence=99)],
        }))
    return sorted(rows, key=lambda item: item['source_timestamp_ms'])


def _batch_states():
    return [
        dict(first_seen_ms=0, last_seen_ms=0,
             values={'skill_points': 1000}, evidence='before.png'),
        dict(first_seen_ms=3000, last_seen_ms=3000,
             values={'skill_points': 700}, evidence='after.png'),
    ]


class SkillBatchMetadataTests(unittest.TestCase):
    def test_persists_only_repeated_source_backed_metadata(self):
        batches = skill_transactions(_batch_rows(), _batch_states())
        self.assertEqual(len(batches), 1)
        batch = batches[0]

        self.assertEqual(batch['spent_skill_points'], 300)
        self.assertEqual(batch['identity_status'], 'ambiguous')
        self.assertIn('750.png', batch['identity_status_evidence'])
        self.assertEqual(batch['post_learn_obtained_names'], ['Later Skill'])
        self.assertEqual(batch['post_learn_obtained_name_evidence']['Later Skill'],
                         ['2000.png', '2250.png'])
        self.assertEqual(batch['post_learn_obtained_name_provenance'][0]['observation_count'], 2)
        self.assertNotIn('price_status', batch)
        self.assertEqual(batch['precommit_price_visibility']['phase'], 'preview')
        self.assertEqual(batch['precommit_price_visibility']['status'], 'partially_visible')
        self.assertEqual(batch['precommit_price_visibility']['basis'],
                         'visible_skill_card_prices_with_incomplete_item_list')
        self.assertEqual(batch['precommit_price_visibility']['observed_timestamps'], [250, 500])
        self.assertEqual(batch['precommit_price_visibility']['evidence'], ['250.png', '500.png'])
        self.assertEqual(batch['confirmation_text'], 'our trainee learned new skills!')
        self.assertIn('1500.png', batch['confirmation_text_evidence'])
        self.assertIn('Your trainee learned new skills!', batch['confirmation_text_variants'])

    def test_price_repetition_requires_distinct_physical_evidence(self):
        rows = _batch_rows(post_repeat=False, price_repeat=True, shared_evidence=True)
        batches = skill_transactions(rows, _batch_states())
        batch = batches[0]
        self.assertNotIn('price_status', batch)
        self.assertNotIn('precommit_price_visibility', batch)

    def test_precommit_price_status_never_becomes_committed_price_status(self):
        rows = _batch_rows(receipt_lines=False)
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertNotIn('price_status', batch)
        self.assertEqual(batch['precommit_price_visibility']['status'], 'partially_visible')
        self.assertEqual(batch['precommit_price_visibility']['phase'], 'preview')

    def test_earlier_canceled_confirmation_and_receipt_are_outside_committed_window(self):
        rows = _batch_rows(receipt_lines=False)
        rows.insert(0, row(100, 'skill_confirmation', {
            'visible_skill_names': ['Canceled Skill'],
            'item_list_complete': False,
            'price_status': 'unreadable',
        }))
        rows.insert(0, row(50, 'skill_receipt', {
            'price_status': 'unreadable',
        }))
        batch = skill_transactions(sorted(rows, key=lambda item: item['source_timestamp_ms']),
                                   _batch_states())[0]
        self.assertNotIn('price_status', batch)
        self.assertEqual(batch['precommit_price_visibility']['status'], 'partially_visible')
        self.assertEqual(batch['precommit_price_visibility']['phase'], 'preview')

    def test_pre_receipt_draft_does_not_promote_obtained_name(self):
        rows = _batch_rows(post_repeat=True, post_after_receipt=False)
        batches = skill_transactions(rows, _batch_states())
        batch = batches[0]
        self.assertNotIn('post_learn_obtained_names', batch)

    def test_one_post_receipt_witness_promotes_inventory_name(self):
        rows = _batch_rows(post_repeat=False, price_repeat=False)
        post = next(item for item in rows
                    if item['screen'] == 'skill_selection'
                    and item['source_timestamp_ms'] == 2000)
        post['facts']['skill_cards'].append(
            dict(name='Known Skill', displayed_cost=None,
                 menu_status='obtained_or_selected'))
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertEqual(batch['post_learn_obtained_names'],
                         ['Known Skill', 'Later Skill'])
        provenance = {item['name']: item for item in batch['post_learn_obtained_name_provenance']}
        self.assertFalse(provenance['Known Skill']['acquisition_verified'])
        self.assertEqual(provenance['Known Skill']['ownership_status'],
                         'post_receipt_inventory_observed')

    def test_t053_like_single_post_receipt_witness_is_retained(self):
        rows = _batch_rows(post_repeat=False, price_repeat=False)
        post = next(item for item in rows
                    if item['screen'] == 'skill_selection'
                    and item['source_timestamp_ms'] == 2000)
        post['facts']['skill_cards'][0]['name'] = 'Flustered End Closers'
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertEqual(batch['post_learn_obtained_names'], ['Flustered End Closers'])

    def test_missing_price_ocr_stays_unresolved(self):
        rows = _batch_rows(receipt_lines=False)
        for item in rows:
            if item['screen'] == 'skill_selection' and item['source_timestamp_ms'] in (250, 500):
                item['facts']['skill_cards'] = []
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertNotIn('price_status', batch)
        self.assertNotIn('unreadable', batch.get('pricing_limitation', '').casefold())

    def test_explicit_unreadable_price_status_is_preserved(self):
        rows = _batch_rows(receipt_lines=False, explicit_price_status='unreadable')
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertEqual(batch['price_status'], 'unreadable')
        self.assertEqual(batch['price_status_basis'], 'explicit_source_price_status')
        self.assertEqual(batch['price_status_observed_timestamps'], [750, 900])
        self.assertEqual(batch['precommit_price_visibility']['status'], 'partially_visible')
        self.assertEqual(batch['precommit_price_visibility']['phase'], 'preview')

    def test_explicit_receipt_unreadable_price_status_is_committed(self):
        rows = _batch_rows(receipt_lines=False)
        for item in rows:
            if item['screen'] == 'skill_receipt':
                item['facts']['price_status'] = 'unreadable'
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertEqual(batch['price_status'], 'unreadable')
        self.assertEqual(batch['price_status_basis'], 'explicit_source_price_status')
        self.assertEqual(batch['price_status_observed_timestamps'], [1500])

    def test_selection_screen_explicit_status_is_preview_provenance(self):
        rows = _batch_rows(receipt_lines=False, price_repeat=True)
        for item in rows:
            if item['screen'] == 'skill_selection' and item['source_timestamp_ms'] in (250, 500):
                item['facts']['price_status'] = 'unreadable'
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertNotIn('price_status', batch)
        self.assertEqual(batch['precommit_price_visibility']['status'], 'unreadable')
        self.assertEqual(batch['precommit_price_visibility']['basis'],
                         'explicit_source_price_status')

    def test_receipt_text_requires_exact_source_wording(self):
        rows = _batch_rows()
        for item in rows:
            if item['screen'] == 'skill_receipt':
                item['ocr'] = {'neural': [line('trainee learned skills!', confidence=99)]}
        batch = skill_transactions(rows, _batch_states())[0]
        self.assertNotIn('confirmation_text', batch)


if __name__ == '__main__':
    unittest.main()
