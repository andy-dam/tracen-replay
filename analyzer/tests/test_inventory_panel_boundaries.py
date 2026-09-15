import copy
import unittest

from tracen_replay.inventory import summarize, visible_cards


ATTRIBUTES = dict(speed=931, stamina=607, power=822, guts=586, wit=1167)


def line(text, box, confidence=99.9):
    return dict(text=text, confidence=confidence, box=list(box))


def summary_raw(content):
    lines = [
        line('Skills', (338, 453, 390, 478)),
        line('Inspiration', (507, 453, 599, 478)),
        line('Career Info', (695, 454, 788, 477)),
    ] + content
    return dict(lines=lines)


def reading(time, cards):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', facts={
        'visible_owned_skill_cards': cards,
    })


def card(name, panel='skills', slot=(0, 0)):
    return dict(name_text=name, panel=panel, slot=list(slot), text_evidence=[
        line(name, (315, 493, 500, 518)),
    ], observed_level=None, level_evidence=[], level_conflicts=[],
        observed_variant=None, variant_evidence=None, variant_conflicts=[])


class InventoryPanelBoundaryTests(unittest.TestCase):
    def test_first_row_preserves_digit_and_equals_skill_names(self):
        raw = summary_raw([
            line('U=ma2', (316, 493, 378, 518)),
            line('Lvl 6', (495, 495, 548, 521)),
            line('564 Escapades', (601, 494, 717, 518)),
            line('Keen Eye', (317, 557, 394, 582)),
            line('Subdued Front Runners', (602, 559, 772, 579)),
        ])

        got = visible_cards(raw, ATTRIBUTES)
        by_name = {entry['name_text']: entry for entry in got}

        self.assertIn('U=ma2', by_name)
        self.assertIn('564 Escapades', by_name)
        self.assertEqual(by_name['U=ma2']['observed_level'], 6)
        self.assertTrue(all('panel' not in entry for entry in got))

    def test_inspiration_and_legacy_rows_never_become_owned_skills(self):
        raw = summary_raw([
            line('Speed', (391, 519, 447, 545)),
            line('RANK', (331, 525, 364, 539)),
            line('Pace Chaser', (625, 519, 720, 543)),
            line('Satsuki Sho', (625, 569, 716, 593)),
            line('Tether', (623, 718, 682, 742)),
            line('Legacy Origin', (298, 809, 405, 836)),
            line('Power', (393, 854, 444, 876)),
            line('Medium', (624, 852, 692, 877)),
            line('Operation Cacao', (392, 903, 522, 927)),
            line('Satsuki Sho', (625, 902, 716, 926)),
        ])

        got = visible_cards(raw, ATTRIBUTES)

        self.assertEqual(got, [])

    def test_legacy_origin_marker_abstains_even_with_card_like_rows(self):
        raw = summary_raw([
            line('U=ma2', (316, 493, 378, 518)),
            line('Lvl 6', (495, 495, 548, 521)),
            line('Legacy Origin', (298, 809, 405, 836)),
        ])

        self.assertEqual(visible_cards(raw, ATTRIBUTES), [])

    def test_tabs_without_positive_skills_anchor_do_not_assign_panel_semantics(self):
        raw = summary_raw([
            line('Career history', (317, 523, 470, 546)),
            line('Race results', (600, 523, 750, 546)),
        ])

        self.assertEqual(visible_cards(raw, ATTRIBUTES), [])

    def test_repeated_skill_grid_geometry_survives_missing_level_ocr(self):
        raw = summary_raw([
            line('Keen Eye', (317, 550, 394, 575)),
            line('Subdued Front Runners', (607, 550, 773, 573)),
            line('Subdued Pace Chasers', (318, 612, 486, 635)),
            line('Subdued Late Surgers', (598, 611, 763, 637)),
            line('Hesitant Late Surgers', (317, 674, 476, 700)),
            line('Subdued End Closers', (601, 675, 759, 698)),
        ])

        got = visible_cards(raw, ATTRIBUTES)

        self.assertEqual({entry['name_text'] for entry in got}, {
            'Keen Eye', 'Subdued Front Runners', 'Subdued Pace Chasers',
            'Subdued Late Surgers', 'Hesitant Late Surgers',
            'Subdued End Closers',
        })

    def test_low_confidence_punctuation_cannot_shift_grid_origin(self):
        raw = summary_raw([
            line('*', (316, 484, 328, 497), confidence=98),
            line('Alpha Skill', (317, 520, 410, 544)),
            line('Beta Skill', (600, 520, 690, 544)),
            line('Gamma Skill', (317, 583, 420, 607)),
            line('Delta Skill', (600, 583, 700, 607)),
            line('Epsilon Skill', (317, 646, 430, 670)),
            line('Zeta Skill', (600, 646, 690, 670)),
        ])

        got = visible_cards(raw, ATTRIBUTES)

        self.assertEqual({entry['name_text'] for entry in got}, {
            'Alpha Skill', 'Beta Skill', 'Gamma Skill', 'Delta Skill',
            'Epsilon Skill', 'Zeta Skill',
        })

    def test_detached_level_and_unrelated_rows_do_not_establish_skills(self):
        raw = summary_raw([
            line('Lvl 1', (495, 520, 548, 545)),
            line('Career History', (317, 583, 470, 607)),
            line('Race Result', (600, 583, 750, 607)),
        ])

        self.assertEqual(visible_cards(raw, ATTRIBUTES), [])

    def test_associated_level_can_identify_a_single_partial_card(self):
        raw = summary_raw([
            line('Observed Skill', (317, 520, 440, 544)),
            line('Lvl 1', (495, 520, 548, 545)),
        ])

        got = visible_cards(raw, ATTRIBUTES)

        self.assertEqual([(entry['name_text'], entry['observed_level']) for entry in got],
                         [('Observed Skill', 1)])

    def test_short_edge_singleton_survives_a_proven_grid(self):
        raw = summary_raw([
            line('First Left', (317, 520, 410, 544)),
            line('First Right', (600, 520, 700, 544)),
            line('Middle Left', (317, 583, 420, 607)),
            line('Middle Right', (600, 583, 710, 607)),
            line('Last Left', (317, 646, 410, 670)),
            line('Last Right', (600, 646, 690, 670)),
            line('Murmur', (317, 709, 383, 733)),
        ])

        got = visible_cards(raw, ATTRIBUTES)

        self.assertIn('Murmur', {entry['name_text'] for entry in got})

    def test_level_belongs_to_its_horizontal_card_column(self):
        raw = summary_raw([
            line('Left Skill', (317, 520, 410, 544)),
            line('Lvl 1', (495, 520, 548, 545)),
            line('Right Skill', (600, 520, 700, 544)),
            line('Middle Left', (317, 583, 420, 607)),
            line('Middle Right', (600, 583, 710, 607)),
        ])

        got = {entry['name_text']: entry for entry in visible_cards(raw, ATTRIBUTES)}

        self.assertEqual(got['Left Skill']['observed_level'], 1)
        self.assertIsNone(got['Right Skill']['observed_level'])

    def test_unpaired_header_inside_grid_lattice_is_not_owned_card(self):
        raw = summary_raw([
            line('First Skill', (317, 520, 410, 544)),
            line('First Peer', (600, 520, 690, 544)),
            line('Middle Skill', (317, 583, 410, 607)),
            line('Middle Peer', (600, 583, 690, 607)),
            line('Header', (340, 614, 400, 638)),
            line('Last Skill', (317, 646, 410, 670)),
            line('Last Peer', (600, 646, 690, 670)),
        ])

        got = visible_cards(raw, ATTRIBUTES)

        self.assertNotIn('Header', {entry['name_text'] for entry in got})
        self.assertEqual({entry['name_text'] for entry in got}, {
            'First Skill', 'First Peer', 'Middle Skill', 'Middle Peer',
            'Last Skill', 'Last Peer',
        })

    def test_one_paired_row_with_associated_level_identifies_partial_panel(self):
        raw = summary_raw([
            line('Observed Skill', (317, 520, 440, 544)),
            line('Lvl 1', (495, 520, 548, 545)),
            line('Observed Peer', (600, 520, 700, 544)),
            line('Unrelated Text', (317, 646, 430, 670)),
        ])

        got = {entry['name_text']: entry for entry in visible_cards(raw, ATTRIBUTES)}

        self.assertEqual(got['Observed Skill']['observed_level'], 1)
        self.assertIsNone(got['Observed Peer']['observed_level'])
        self.assertNotIn('Unrelated Text', got)

    def test_wrapped_card_stays_together_at_row_boundary(self):
        raw = summary_raw([
            line('Boundary', (317, 548, 400, 570)),
            line('Continuation', (318, 568, 445, 590)),
            line('Paired Skill', (600, 552, 700, 576)),
            line('Next Left', (317, 611, 410, 635)),
            line('Next Right', (600, 612, 700, 636)),
            line('Last Left', (317, 674, 410, 698)),
            line('Last Right', (600, 675, 700, 699)),
        ])

        got = visible_cards(raw, ATTRIBUTES)

        self.assertIn('Boundary Continuation', {entry['name_text'] for entry in got})
        self.assertNotIn('Continuation', {entry['name_text'] for entry in got})

    def test_inspiration_inset_does_not_trigger_missing_level_fallback(self):
        raw = summary_raw([
            line('Speed', (391, 519, 447, 545)),
            line('Pace Chaser', (625, 519, 720, 543)),
            line('U=ma2', (391, 568, 452, 593)),
            line('Satsuki Sho', (625, 569, 716, 593)),
        ])

        self.assertEqual(visible_cards(raw, ATTRIBUTES), [])

    def test_summary_ignores_cards_explicitly_from_other_panels(self):
        good = card('Good Skill')
        leaked = card('Pace Chaser', panel='inspiration', slot=(0, 1))
        rows = [reading(1000, [good, leaked]), reading(1250, [good, leaked])]

        result = summarize(rows)

        self.assertEqual([entry['name_text'] for entry in result['observed_owned_cards']], ['Good Skill'])
        self.assertNotIn('panel', result['observed_owned_cards'][0])

    def test_legacy_readings_without_panel_field_remain_compatible(self):
        old_style = copy.deepcopy(card('Legacy Fixture'))
        old_style.pop('panel')
        rows = [reading(1000, [old_style]), reading(1250, [old_style])]

        result = summarize(rows)

        self.assertEqual([entry['name_text'] for entry in result['observed_owned_cards']], ['Legacy Fixture'])


if __name__ == '__main__':
    unittest.main()
