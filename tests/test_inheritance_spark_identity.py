"""Source-backed ambiguity tests for inheritance spark names."""

import copy
import json
from pathlib import Path
import unittest

from tracen_replay.inheritance_spark_identity import resolve, _simultaneous


def line(text, box, confidence=99):
    return dict(text=text, box=list(box), confidence=confidence)


def spark(name):
    return dict(kind='inheritance_spark', name=name, amount=None,
                awarded_effects_unknown=True,
                raw_text=f'{name} spark activated!', confidence=99)


def row(timestamp, evidence, effects, *, target_box=(316, 830, 695, 859),
        anchor_box=None, title=None, screen='event_outcome', anchor=True):
    lines = [line(effect['raw_text'], target_box) for effect in effects]
    if anchor:
        anchor_box = anchor_box or (317, 903, 555, 930)
        lines.append(line('Stamina spark activated!', anchor_box))
    return dict(source_timestamp_ms=timestamp, evidence=evidence, screen=screen,
                context_title=title, ocr=dict(neural=lines))


def event(effects, proofs):
    return dict(
        effects=effects,
        field_evidence={f"inheritance_spark||{effect['name']}": proofs[effect['name']]
                        for effect in effects},
        conflicting_readings=[])


class InheritanceSparkIdentityTests(unittest.TestCase):
    def test_corrected_line_and_its_original_text_do_not_prove_two_receipts(self):
        plain = spark('Example Skill ')
        marked = spark('Example Skill ○')
        marked['original_text'] = plain['raw_text']
        source = row(1000, 'one', [plain], anchor=False)
        self.assertFalse(_simultaneous(marked, plain, [dict(row=source)]))
        # Duplicate OCR observations of the same physical line still prove one.
        source['ocr']['neural'].append(copy.deepcopy(source['ocr']['neural'][0]))
        self.assertFalse(_simultaneous(marked, plain, [dict(row=source)]))

    def test_simultaneous_identity_requires_disjoint_receipt_lines(self):
        plain = spark('Example Skill ')
        marked = spark('Example Skill ○')
        marked['original_text'] = plain['raw_text']
        source = row(1000, 'both', [marked], anchor=False)
        source['ocr']['neural'].append(line(plain['raw_text'], (316, 900, 695, 929)))
        self.assertTrue(_simultaneous(marked, plain, [dict(row=source)]))
        # Real neighboring lines have small overlaps in OCR-box margins.
        source['ocr']['neural'][1]['box'] = [316, 854, 695, 883]
        self.assertTrue(_simultaneous(marked, plain, [dict(row=source)]))
        source['ocr']['neural'][1]['box'] = [316, 834, 695, 863]
        self.assertFalse(_simultaneous(marked, plain, [dict(row=source)]))

    def test_current_recording_retains_repeated_recovery_and_audits_recover(self):
        fixture = json.loads(Path('tests/fixtures/inheritance-spark-identity-real.json')
                             .read_text(encoding='utf-8'))
        self.assertEqual(fixture['source']['sha256'],
                         '24e000837fa4bba40d4e9e7c1c7d6121300a51d166424ea7bf5cfea24f521c18')
        self.assertEqual(fixture['published_report']['sha256'],
                         '95e117aedf222fdfdf89cdb351f7c08100d9df626846724913e32d2af39e8846')
        current = copy.deepcopy(fixture['event'])
        rows = copy.deepcopy(fixture['rows_by_evidence'])

        resolve(current, rows)

        names = {effect['name'] for effect in current['effects']
                 if effect['kind'] == 'inheritance_spark'}
        self.assertIn('Straightaway Recovery', names)
        self.assertNotIn('Straightaway Recover', names)
        candidates = {candidate['effect']['name']
                      for candidate in current['ambiguous_effect_candidates']}
        self.assertIn('Straightaway Recover', candidates)
        recovery = next(effect for effect in current['effects']
                        if effect.get('name') == 'Straightaway Recovery')
        self.assertEqual(recovery['observed_name_candidates'],
                         ['Straightaway Recovery', 'Straightaway Recover'])
        self.assertEqual(
            current['field_evidence']['inheritance_spark||Straightaway Recovery'],
            ['gameplay/part-004-frame-000162.png',
             'gameplay/part-004-frame-000163.png'])
        self.assertFalse(current['conflicting_readings'])
        self.assertEqual(
            {detail['field'] for detail in current['resolved_reading_conflict_details']},
            {'inheritance_spark||Straightaway Recovery',
             'inheritance_spark||Straightaway Recover'})
        self.assertTrue(all(detail['continuity']['mode'] == 'stationary_slot'
                            for detail in current['resolved_reading_conflict_details']))

    def test_stationary_anchor_resolves_only_with_independent_repeated_name(self):
        first = spark('Alpha Complete')
        variant = spark('Alpha Comple')
        rows = {
            'a0': row(1000, 'a0', [first], target_box=(316, 900, 695, 929)),
            'a1': row(1250, 'a1', [first]),
            'b1': row(1500, 'b1', [variant]),
        }
        current = event([first, variant], {
            first['name']: ['a0', 'a1'], variant['name']: ['b1']})

        resolve(current, rows)

        self.assertEqual([effect['name'] for effect in current['effects']],
                         ['Alpha Complete'])
        self.assertEqual(current['effects'][0]['observed_name_candidates'],
                         ['Alpha Complete', 'Alpha Comple'])
        self.assertEqual({candidate['effect']['name']
                          for candidate in current['ambiguous_effect_candidates']},
                         {'Alpha Comple'})
        self.assertEqual(current['resolved_reading_conflicts'][0]['accepted_name'],
                         'Alpha Complete')
        self.assertEqual(current['resolved_reading_conflict_details'][0]['continuity']['mode'],
                         'stationary_slot')

    def test_resolution_preserves_unrelated_active_conflicts(self):
        first = spark('Unrelated Complete')
        variant = spark('Unrelated Comple')
        rows = {
            'a0': row(1000, 'a0', [first], target_box=(316, 900, 695, 929)),
            'a1': row(1250, 'a1', [first]),
            'b1': row(1500, 'b1', [variant]),
        }
        current = event([first, variant], {
            first['name']: ['a0', 'a1'], variant['name']: ['b1']})
        existing = dict(field='stat_change|power|', reason='existing_unrelated_conflict')
        current['conflicting_readings'] = [existing]

        resolve(current, rows)

        self.assertEqual(current['conflicting_readings'], [existing])
        self.assertTrue(current['resolved_reading_conflicts'])

    def test_upward_scroll_requires_exact_unique_anchor_motion(self):
        first = spark('Beta Complete')
        variant = spark('Beta Comple')
        rows = {
            'a0': row(1000, 'a0', [first], target_box=(316, 900, 695, 929),
                      anchor_box=(317, 973, 555, 1000)),
            'a1': row(1250, 'a1', [first], target_box=(316, 840, 695, 869),
                      anchor_box=(317, 913, 555, 940)),
            'b1': row(1500, 'b1', [variant], target_box=(316, 780, 695, 809),
                      anchor_box=(317, 853, 555, 880)),
        }
        current = event([first, variant], {
            first['name']: ['a0', 'a1'], variant['name']: ['b1']})

        resolve(current, rows)

        self.assertEqual([effect['name'] for effect in current['effects']],
                         ['Beta Complete'])
        self.assertEqual(current['resolved_reading_conflict_details'][0]['continuity']['mode'],
                         'upward_scroll')
        self.assertEqual(current['resolved_reading_conflict_details'][0]['continuity']['anchors'][0]['text'],
                         'Stamina spark activated!')

    def test_degraded_anchor_does_not_fake_scroll_proof(self):
        first = spark('Gamma Complete')
        variant = spark('Gamma Comple')
        rows = {
            'a': row(1000, 'a', [first], target_box=(316, 900, 695, 929), anchor=False),
            'b': row(1250, 'b', [variant], target_box=(316, 840, 695, 869), anchor=False),
        }
        rows['a']['ocr']['neural'].append(
            line('Stamina spark ac wed!', (317, 973, 555, 1000)))
        rows['b']['ocr']['neural'].append(
            line('Stamina spark activated!', (317, 913, 555, 940)))
        current = event([first, variant], {
            first['name']: ['a'], variant['name']: ['b']})
        before = copy.deepcopy(current)

        resolve(current, rows)

        # A moving target without an exact moving anchor may be a different
        # spark line entering the slot. Preserve both observations instead of
        # inventing a scroll track or suppressing a valid spark.
        self.assertEqual(current, before)

    def test_same_slot_without_shared_anchor_remains_distinct(self):
        first = spark('Delta Complete')
        variant = spark('Delta Comple')
        rows = {
            'a': row(1000, 'a', [first], anchor=False),
            'b': row(1250, 'b', [variant], anchor=False),
        }
        current = event([first, variant], {
            first['name']: ['a'], variant['name']: ['b']})

        resolve(current, rows)

        # Similar y-coordinates alone do not establish that two different
        # spark lines are one receipt; preserve both source observations.
        self.assertEqual(current['effects'], [first, variant])
        self.assertNotIn('ambiguous_effect_candidates', current)

    def test_intervening_source_row_blocks_endpoint_pairing(self):
        first = spark('Intervening Complete')
        variant = spark('Intervening Comple')
        middle_cases = (
            row(1125, 'middle-screen', [], screen='training_preview', anchor=False),
            row(1125, 'middle-target', [spark('Other Spark')], title='Different receipt'),
        )
        for middle in middle_cases:
            with self.subTest(evidence=middle['evidence']):
                rows = {
                    'first': row(1000, 'first', [first]),
                    middle['evidence']: middle,
                    'last': row(1250, 'last', [variant]),
                }
                current = event([first, variant], {
                    first['name']: ['first'], variant['name']: ['last']})
                before = copy.deepcopy(current)

                resolve(current, rows)

                self.assertEqual(current, before)

    def test_huge_or_nonfinite_numbers_are_ignored_without_overflow(self):
        first = spark('Numeric Complete')
        variant = spark('Numeric Comple')
        huge = 10 ** 10000
        rows = {
            'huge': row(1000, 'huge', [first],
                        target_box=(huge, 830, huge + 1, 859)),
            'infinite': row(1250, 'infinite', [variant]),
        }
        rows['infinite']['ocr']['neural'][0]['confidence'] = float('inf')
        current = event([first, variant], {
            first['name']: ['huge'], variant['name']: ['infinite']})
        before = copy.deepcopy(current)

        resolve(current, rows)

        self.assertEqual(current, before)

    def test_different_slot_and_simultaneous_lines_remain_distinct(self):
        left = spark('Epsilon Complete')
        right = spark('Epsilon Comple')
        cases = (
            {
                'left': row(1000, 'left', [left], target_box=(316, 830, 695, 859)),
                'right': row(1250, 'right', [right], target_box=(500, 830, 850, 859)),
            },
            {
                'both': row(1000, 'both', [left, right]),
            },
        )
        for rows in cases:
            with self.subTest(rows=tuple(rows)):
                current = event([left, right], {
                    left['name']: [next(iter(rows))],
                    right['name']: [next(iter(rows))],
                })
                # In the first case each proof is its own row; in the second
                # both effects are visibly present in one row.
                if 'both' not in rows:
                    current['field_evidence'][f"inheritance_spark||{left['name']}"] = ['left']
                    current['field_evidence'][f"inheritance_spark||{right['name']}"] = ['right']
                resolve(current, rows)
                self.assertEqual({effect['name'] for effect in current['effects']},
                                 {left['name'], right['name']})
                self.assertNotIn('ambiguous_effect_candidates', current)

    def test_duplicate_anchor_is_not_unique(self):
        left = spark('Zeta Complete')
        right = spark('Zeta Comple')
        rows = {
            'left': row(1000, 'left', [left]),
            'right': row(1250, 'right', [right]),
        }
        for source in rows.values():
            source['ocr']['neural'].append(
                line('Stamina spark activated!', (317, 903, 555, 930)))
        current = event([left, right], {
            left['name']: ['left'], right['name']: ['right']})
        rows['left']['ocr']['neural'].append(
            line('Stamina spark activated!', (317, 903, 555, 930)))
        rows['right']['ocr']['neural'].append(
            line('Stamina spark activated!', (317, 903, 555, 930)))

        resolve(current, rows)

        self.assertEqual(current['effects'], [])
        self.assertEqual({candidate['effect']['name']
                          for candidate in current['ambiguous_effect_candidates']},
                         {'Zeta Complete', 'Zeta Comple'})
        self.assertEqual(
            {detail['field'] for detail in current['conflicting_readings']},
            {'inheritance_spark||Zeta Complete', 'inheritance_spark||Zeta Comple'})


if __name__ == '__main__':
    unittest.main()
