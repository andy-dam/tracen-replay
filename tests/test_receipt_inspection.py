import unittest
from tracen_replay.inspect_receipts import merge
from tracen_replay.refine_receipts import apply, consensus, fingerprint
from tracen_replay.candidate_review import evaluate


class ReceiptInspectionTests(unittest.TestCase):
    def test_inheritance_and_recreation_do_not_invent_rewards(self):
        from tracen_replay.gameplay import effects_from_lines
        effects=effects_from_lines([dict(text=t,confidence=99) for t in
            ('Unlocked recreation with Light Hello.','Inspired by Maruzensky!','Power spark activated!')])
        self.assertEqual([e['kind'] for e in effects],['recreation_unlocked','inheritance_inspiration','inheritance_spark'])
        self.assertTrue(all(e['amount'] is None for e in effects))
        self.assertTrue(effects[-1]['awarded_effects_unknown'])
        self.assertEqual(effects_from_lines([dict(text='Unlock recreation with Light Hello.',confidence=99)]),[])

    def test_menu_is_not_replaced_by_supplemental_receipt(self):
        base=dict(source_timestamp_ms=10,screen='lesson_selection',effects=[],stats={},evidence='menu.png')
        extra=dict(base,screen='event_outcome',effects=[dict(kind='energy_change',amount=-18)])
        self.assertEqual(merge([base],[extra]),[base])

    def test_conflicting_amounts_survive_and_frame_is_not_duplicated(self):
        base=dict(source_timestamp_ms=10,screen='event_outcome',effects=[dict(kind='energy_change',amount=-8)],stats={'speed':100},evidence='base.png')
        extra=dict(base,effects=[dict(kind='energy_change',amount=-18)],stats={},evidence='extra.png')
        result=merge([base],[extra,extra])
        self.assertEqual(len(result),1)
        self.assertEqual([e['amount'] for e in result[0]['effects']],[-8,-18])
        self.assertEqual(result[0]['stats'],{'speed':100})

    def test_crop_consensus_rejects_disagreement_and_weak_views(self):
        views=[dict(text='Energy went down by18.',confidence=x) for x in (95.4,97.4,95.7)]
        self.assertIsNotNone(consensus(views))
        self.assertIsNone(consensus(views[:2]))
        self.assertIsNone(consensus([*views[:2],dict(text='Energy went down by8.',confidence=99)]))
        self.assertIsNone(consensus([*views[:2],dict(text=views[0]['text'],confidence=94)]))

    def test_refinement_retains_original_and_rejects_different_raw(self):
        raw=dict(lines=[dict(text='Energy went down by8.',confidence=98,box=[1,2,3,4])])
        extra=dict(raw_sha256=fingerprint(raw),lines=[dict(index=0,views=[dict(text='Energy went down by18.',confidence=98)]*3)])
        result=apply(raw,extra)
        self.assertEqual(result['lines'][0]['original_text'],raw['lines'][0]['text'])
        self.assertEqual(raw['lines'][0]['text'],'Energy went down by8.')
        with self.assertRaises(ValueError):apply(dict(lines=[]),extra)

    def test_disagreement_abstains_instead_of_keeping_confident_wrong_digit(self):
        raw=dict(lines=[dict(text='Energy went down by8.',confidence=98)])
        views=[dict(text='Energy went down by18.',confidence=97.5),
               dict(text='Energy went down by18.',confidence=94.2),
               dict(text='Energy went down by]8.',confidence=96.8)]
        extra=dict(raw_sha256=fingerprint(raw),lines=[dict(index=0,views=views)])
        result=apply(raw,extra)['lines'][0]
        self.assertTrue(result['receipt_crop_conflict'])
        self.assertEqual(result['confidence'],0)
        self.assertEqual(result['text'],raw['lines'][0]['text'])
        views[1]['confidence']=97.2
        self.assertEqual(consensus(views)['text'],'Energy went down by18.')

    def test_candidate_review_rejects_wrong_amount_without_inventing_recall(self):
        reference=dict(source_sha256='source',scope='candidate-selected',items=[dict(index=1,source_timestamp_ms=1000,
            expected_effect=dict(kind='energy_change',amount=-18))])
        report=dict(source=dict(sha256='source'),gameplay_tracking=dict(auxiliary_log_used=False,events=[dict(id='e',
            first_seen_ms=1000,last_seen_ms=1500,effects=[dict(kind='energy_change',amount=-8)])]))
        result=evaluate(reference,report)
        self.assertFalse(result['passed'])
        self.assertFalse(result['complete_effect_recall_measured'])
        report['gameplay_tracking']['events'][0]['effects'][0]['amount']=-18
        self.assertTrue(evaluate(reference,report)['passed'])
        report['gameplay_tracking']['events'][0]['conflicting_readings']=[dict(field='energy_change||')]
        self.assertFalse(evaluate(reference,report)['passed'])

    def test_spacing_repairs_preserve_digits_and_names(self):
        from tracen_replay.vision import parse
        def read(text):
            return parse(dict(lines=[dict(text=text,box=[306,785,750,814],confidence=99)],
                regions={},header='',current_grid=False,result_grid=False))
        self.assertEqual(read('Energy went downby14.')['effects'][0]['amount'],-14)
        self.assertEqual(read('Energy went down by18.')['effects'][0]['amount'],-18)
        self.assertEqual(read("Friendship with Sirius Symbolididn't go up.")['effects'][0]['name'],'Sirius Symboli')
        self.assertEqual(read('Friendship with Light Hello ismaxed out.')['effects'][0]['value'],'maximum')
        self.assertEqual(read('Energy went down by]8.')['effects'],[])
