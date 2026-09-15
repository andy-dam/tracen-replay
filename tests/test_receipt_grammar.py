import copy
import json
from pathlib import Path
import unittest

from tracen_replay.receipt_grammar import normalize
from tracen_replay.receipt_occlusion import friendship_name_bounds
from tracen_replay.vision import parse


class ReceiptGrammarTests(unittest.TestCase):
    def test_source_cursor_over_fixed_word_preserves_recipient_and_amount(self):
        fixture = json.loads(Path('tests/fixtures/receipt-fixed-grammar-484750.json').read_text(encoding='utf-8'))
        raw = fixture['raw']; before = copy.deepcopy(raw)
        effects = parse(raw)['effects']
        self.assertEqual({e['name']: e['amount'] for e in effects if e['kind'] == 'friendship_change'}, fixture['expected_friendships'])
        hello = next(e for e in effects if e.get('name') == 'Light Hello')
        self.assertEqual(hello['original_text'], 'Friendship Dith Light Hello went up by 2.')
        self.assertEqual(raw, before)

    def test_single_fixed_word_damage_generalizes_without_name_repair(self):
        expected = 'Friendship with Example Name went up by 12.'
        for text in ('Friendship ith Example Name went up by 12.',
                     'Friendship wit Example Name went up by 12.',
                     'Friendship wth Example Name went up by 12.',
                     'Frendship with Example Name went up by 12.',
                     'Friendship with Example Name wert up by 12.'):
            with self.subTest(text=text): self.assertEqual(normalize(text,allow_boundary_repair=True), expected)
        self.assertEqual(normalize('Friendship ith Exarnple Narne went up by 2.',allow_boundary_repair=True),
                         'Friendship with Exarnple Narne went up by 2.')

    def test_damaged_separator_does_not_promote_truncated_recipient(self):
        for text in ('Friendship wich Direct Akikawa went up by 4.',
                     'Friendship wite or Akikawa went up by 4.',
                     'Friendship ith Example Name went up by 2.'):
            self.assertEqual(normalize(text),text)

    def test_actual_click_particle_frames_do_not_create_partial_names(self):
        fixtures=json.loads(Path('tests/fixtures/receipt-boundary-obstructions.json').read_text(encoding='utf-8'))
        for fixture in fixtures:
            with self.subTest(source=fixture['source_sha256']):
                names={e.get('name') for e in parse(fixture['raw'])['effects'] if e['kind']=='friendship_change'}
                self.assertFalse(names.intersection(fixture['expected_absent_friendships']))

    def test_boundary_repair_needs_unchanged_name_and_localized_pixel_proof(self):
        fixture=json.loads(Path('tests/fixtures/receipt-fixed-grammar-484750.json').read_text(encoding='utf-8'))
        for mode in ('no_provenance','different_name','overlay_over_name'):
            raw=copy.deepcopy(fixture['raw']);proof=raw['receipt_overlay_evidence']
            if mode=='no_provenance':proof['provenance']={}
            elif mode=='different_name':proof['alignments'][0]['recognized_text']='Friendship ith Light went up by 2.'
            else:proof['overlay_boxes']=[[490,836,503,855]]
            self.assertFalse(any(e.get('name')=='Light Hello' for e in parse(raw)['effects']))

    def test_missing_number_tense_negation_incomplete_and_multiple_damage_are_not_repaired(self):
        for text in ('Friendship ith Name went up by .', 'Friendship ith Name went up by 1',
                     'Friendship ith Name will go up by 1.', 'Friendship ith Name did not go up by 1.',
                     'Friendship ith Name went down by 1.', 'Leadership with Name went up by 1.',
                     'Frendship ith Name went up by 1.', 'Friendship without Name went up by 1.'):
            with self.subTest(text=text): self.assertEqual(normalize(text), text)

    def test_repair_does_not_bypass_low_confidence_or_known_occlusion(self):
        fixture = json.loads(Path('tests/fixtures/receipt-fixed-grammar-484750.json').read_text(encoding='utf-8'))
        for confidence in (0, 80, 94.9):
            raw = copy.deepcopy(fixture['raw'])
            for line in raw['lines']:
                if 'ith Light Hello' in line['text']:
                    line['confidence'] = confidence
            self.assertFalse(any(e.get('name') == 'Light Hello' for e in parse(raw)['effects']))

    def test_occlusion_guard_recognizes_same_repairable_grammar(self):
        words = ['Friendship', 'ith', 'Example', 'Name', 'went', 'up', 'by', '7.']
        columns = []; start = 0
        for word in words:
            columns.append(list(range(start, start + len(word)))); start += len(word) + 1
        box = friendship_name_bounds([300, 820, 750, 850], words, columns, start)
        self.assertIsNotNone(box)
        scale = 450 / start
        self.assertLess(box[0], 300 + min(columns[2]) * scale)
        self.assertGreater(box[2], 300 + max(columns[3]) * scale)


if __name__ == '__main__': unittest.main()
