from copy import deepcopy
import unittest

from tests.test_hint_identity_fallback import _case, _row, _weak_effect
from tracen_replay.transactions import outcome_events


class HintIdentityFallbackIntegrationTests(unittest.TestCase):
    def test_proven_circle_survives_unproven_name_gap(self):
        _, mapping=_case()
        rows=[_row(100,'weak-before',_weak_effect()), *mapping.values(),
              _row(450,'damaged',_weak_effect(name='Example Skll')),
              _row(550,'weak-after',_weak_effect())]
        for row in rows:row.update(stats={},facts={})
        original=deepcopy(rows)
        event=outcome_events(rows)[0]
        names=[effect['name'] for effect in event['effects']]
        self.assertIn('Example Skill ○',names)
        self.assertNotIn('Example Skill',names)
        strong=next(effect for effect in event['effects'] if effect['name']=='Example Skill ○')
        self.assertEqual(strong['visual_symbol_observation'],mapping['strong-2']['effects'][0]['visual_symbol_observation'])
        # The bare spelling sits at the circle-proven line's own slot on the
        # frames around it: one line at one slot is one award, its glyph
        # unread, and the spelling joins as evidence rather than staying a
        # candidate.
        self.assertEqual(event['ambiguous_effect_candidates'],[])
        self.assertIn(dict(name='Example Skill',evidence=['weak-before','weak-after']),strong['alternate_name_evidence'])
        # The damaged spelling on the frame between joins later by the
        # recovered-spelling rule, which then names the resolution.
        self.assertIn(strong['name_resolution'],('same_slot_circle_glyph_unread','uncorroborated_recovered_spelling'))
        self.assertEqual(rows,original)
        # The slot decides even when the run read the bare spelling elsewhere:
        # a slot holds one line, whatever another box showed.
        elsewhere=[_row(9000,'elsewhere',_weak_effect())]
        for row in elsewhere:row.update(stats={},facts={})
        event=outcome_events(rows+elsewhere)[0]
        self.assertEqual(event['ambiguous_effect_candidates'],[])
        self.assertEqual([e['name'] for e in event['effects']],['Example Skill ○'])
        # At another slot the bare spelling stays the unresolved candidate the
        # fallback made, and read elsewhere too it may be an award of its own.
        shifted=deepcopy(rows)
        for row in shifted:
            if row['evidence'] in ('weak-before','weak-after'):
                row['ocr']['neural'][0]['box']=[660,850,720,890]
        event=outcome_events(shifted+elsewhere)[0]
        candidate=event['ambiguous_effect_candidates'][0]
        self.assertEqual(candidate['evidence'],['weak-before','weak-after'])
        self.assertFalse(candidate['continuity_proven'])
        self.assertIsNone(candidate['occurrence_count'])

    def test_unverified_circle_keeps_existing_unknown_variant_behavior(self):
        weak=_weak_effect()
        suffix=dict(weak,name='Example Skill ○',raw_text='Gained 1 hint level(s) for Example Skill ○.')
        rows=[_row(100,'weak',weak),_row(250,'suffix',suffix)]
        for row in rows:row.update(stats={},facts={})
        event=outcome_events(rows)[0]
        self.assertEqual(len(event['effects']),1)
        self.assertEqual(event['effects'][0]['name'],'Example Skill')
        self.assertFalse(event['effects'][0]['circle_variant_verified'])

    def test_proven_circle_does_not_leave_letter_o_as_a_new_active_hint(self):
        _,mapping=_case()
        rows=[_row(100,'weak-before',_weak_effect()),*mapping.values(),
              _row(450,'letter-o',_weak_effect(name='Example Skill O')),
              _row(550,'weak-after',_weak_effect())]
        for row in rows:row.update(stats={},facts={})
        event=outcome_events(rows)[0]
        self.assertEqual([e['name'] for e in event['effects']],['Example Skill ○'])
        # The letter O sits at the proven line's slot too: the glyph read as
        # a letter, one award, the spelling kept as evidence.
        self.assertEqual(event['ambiguous_effect_candidates'],[])
        strong=event['effects'][0]
        self.assertIn(dict(name='Example Skill O',evidence=['letter-o']),strong['alternate_name_evidence'])

    def test_insufficient_repeat_preserves_pixel_observation_as_unresolved(self):
        _,mapping=_case(proof_count=1)
        rows=[_row(100,'weak-before',_weak_effect()),*mapping.values()]
        for row in rows:row.update(stats={},facts={})
        event=outcome_events(rows)[0]
        strong=next(e for e in event['effects'] if e['name']=='Example Skill ○')
        self.assertEqual(strong['visual_symbol_observation'],mapping['strong-1']['effects'][0]['visual_symbol_observation'])
        self.assertTrue(any(c['reason']=='unresolved_circle_variant_relation' for c in event['conflicting_readings']))

    def test_inheritance_summary_preserves_hint_conflict_evidence_list(self):
        _,mapping=_case(proof_count=1)
        rows=[_row(100,'weak-before',_weak_effect()),*mapping.values()]
        for row in rows:
            row.update(stats={},facts={})
            row['effects'].append(dict(kind='inheritance_spark',name='Power',amount=None,
                                       raw_text='Power spark activated!',confidence=99))
            row['ocr']['neural'].append(dict(text='Power spark activated!',confidence=99,box=[315,900,600,930]))
        event=outcome_events(rows)[0]
        self.assertIn('inheritance_occurrence_evidence',event)
        conflicts=[c for c in event['conflicting_readings'] if c['reason']=='unresolved_circle_variant_relation']
        self.assertTrue(conflicts)
        self.assertTrue(all(isinstance(c['evidence'],list) for c in conflicts))


if __name__=='__main__':
    unittest.main()
