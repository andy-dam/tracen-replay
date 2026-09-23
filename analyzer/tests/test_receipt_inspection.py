import unittest
from copy import deepcopy
from tracen_replay.inspect_receipts import merge
from tracen_replay.refine_receipts import apply, consensus, fingerprint


def bound(row, *, source='s' * 64, frame='f' * 64, frame_id='frame-000001',
          engine='e' * 64, models=None):
    return dict(
        row,
        source_sha256=source,
        source_frame_sha256=frame,
        source_frame_id=frame_id,
        engine_fingerprint=engine,
        model_sha256=models or {'model.onnx': 'm' * 64},
    )


class ReceiptInspectionTests(unittest.TestCase):
    def test_supplement_preserves_context_and_exposes_exact_field_proof(self):
        from tracen_replay.transactions import outcome_events
        effect=dict(kind='stat_change',field='speed',amount=5)
        base=bound(dict(source_timestamp_ms=1000,screen='event_outcome',effects=[],stats={'speed':100},
                  evidence='primary.png',context_title='Original event',facts={'identity':'original'},ocr={'neural':[]}))
        extra=dict(base,evidence='alternate.png',effects=[effect],context_title='Damaged title',
                   facts={'identity':'damaged'},ocr={'neural':[{'text':'Speed went up by 5.'}]})
        original=deepcopy((base,extra))
        merged=merge([base],[extra,extra])
        for key in ('context_title','stats','evidence','ocr'):
            self.assertEqual(merged[0][key],base[key])
        self.assertEqual(merged[0]['facts']['identity'],'original')
        self.assertEqual(len(merged[0]['supplemental_receipt_observations']),1)
        events=outcome_events(merged)
        self.assertEqual(events[0]['context_title'],'Original event')
        self.assertEqual(events[0]['deltas'],{'speed':5})
        self.assertIn('alternate.png',events[0]['field_evidence']['stat_change|speed|'])
        self.assertEqual((base,extra),original)

    def test_alternate_views_do_not_supply_temporal_corroboration_or_other_amount_proof(self):
        from tracen_replay.transactions import outcome_events,attach_supplemental_receipt_evidence
        def effect(n):return dict(kind='stat_change',field='speed',amount=n)
        base=bound(dict(source_timestamp_ms=1000,screen='event_outcome',effects=[effect(5)],stats={},
                  evidence='primary.png',ocr={'neural':[]},facts={}))
        extras=[dict(base,evidence=f'alternate-{i}.png',effects=[effect(15)]) for i in range(3)]
        events=outcome_events(merge([base],extras))
        self.assertTrue(events[0]['conflicting_readings'])
        self.assertEqual(events[0]['deltas'],{})
        event=dict(first_seen_ms=900,last_seen_ms=1100,effects=[effect(5)],field_evidence={})
        attach_supplemental_receipt_evidence([event],merge([base],extras))
        self.assertEqual(event['field_evidence'],{})

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
        base=bound(dict(source_timestamp_ms=10,screen='event_outcome',effects=[dict(kind='energy_change',amount=-8)],stats={'speed':100},evidence='base.png'))
        extra=dict(base,effects=[dict(kind='energy_change',amount=-18)],stats={},evidence='extra.png')
        result=merge([base],[extra,extra])
        self.assertEqual(len(result),1)
        self.assertEqual([e['amount'] for e in result[0]['effects']],[-8,-18])
        self.assertEqual(result[0]['stats'],{'speed':100})

    def test_same_source_frame_allows_an_alternate_crop(self):
        base = bound(dict(
            source_timestamp_ms=100,
            screen='event_outcome',
            effects=[],
            evidence='base.png',
            facts={},
            ocr={'neural': []},
        ))
        extra = dict(base, evidence='inspection/frame-000001.png',
                     effects=[dict(kind='stat_change', field='speed', amount=5)])
        merged = merge([base], [extra])
        self.assertEqual([effect['amount'] for effect in merged[0]['effects']], [5])
        self.assertEqual(merged[0]['supplemental_receipt_observations'][0]['evidence'],
                         extra['evidence'])

    def test_same_timestamp_without_source_binding_stays_unresolved(self):
        base = dict(
            source_timestamp_ms=100,
            screen='event_outcome',
            effects=[],
            evidence='base.png',
            facts={},
            ocr={'neural': []},
        )
        extra = dict(base, evidence='unrelated.png',
                     effects=[dict(kind='stat_change', field='speed', amount=5)])
        merged = merge([base], [extra])
        self.assertEqual(merged[0]['effects'], [])
        self.assertEqual(
            merged[0]['unresolved_supplemental_receipt_observations'][0]['effects'],
            extra['effects'],
        )
        self.assertEqual(
            merged[0]['unresolved_supplemental_receipt_observations'][0]['merge_rejection_reason'],
            'base_missing_source_namespace',
        )

    def test_mismatched_source_or_frame_binding_stays_unresolved(self):
        base = bound(dict(
            source_timestamp_ms=100, screen='event_outcome', effects=[],
            evidence='base.png', facts={}, ocr={'neural': []},
        ))
        cases = [
            dict(base, evidence='other-source.png', source_sha256='x' * 64),
            dict(base, evidence='other-frame.png', source_frame_sha256='y' * 64),
            dict(base, evidence='missing-engine.png', engine_fingerprint=''),
            dict(base, evidence='missing-model.png', model_sha256={}),
        ]
        for extra in cases:
            extra['effects'] = [dict(kind='stat_change', field='speed', amount=5)]
            with self.subTest(evidence=extra['evidence']):
                merged = merge([base], [extra])
                self.assertEqual(merged[0]['effects'], [])
                self.assertEqual(len(merged[0]['unresolved_supplemental_receipt_observations']), 1)

    def test_same_source_frame_keeps_reader_variant_as_supplemental_provenance(self):
        base = bound(dict(
            source_timestamp_ms=100, screen='event_outcome', effects=[],
            evidence='base.png', facts={}, ocr={'neural': []},
        ))
        extra = dict(
            base,
            evidence='reader-v2.png',
            engine_fingerprint='different-reader',
            model_sha256={'other-model.onnx': 'q' * 64},
            effects=[dict(kind='stat_change', field='speed', amount=5)],
        )
        merged = merge([base], [extra])
        self.assertEqual(merged[0]['effects'][0]['amount'], 5)
        self.assertEqual(
            merged[0]['supplemental_receipt_observations'][0]['engine_fingerprint'],
            'different-reader',
        )

    def test_crop_consensus_rejects_disagreement_and_weak_views(self):
        views=[dict(text='Energy went down by18.',confidence=x) for x in (95.4,97.4,95.7)]
        self.assertIsNotNone(consensus(views))
        self.assertIsNone(consensus(views[:2]))
        self.assertIsNone(consensus([*views[:2],dict(text='Energy went down by8.',confidence=99)]))
        self.assertIsNone(consensus([*views[:2],dict(text=views[0]['text'],confidence=94)]))

    def test_identical_confident_nonreceipts_are_not_corrections(self):
        for text in ('Skill Pts wet up by 3.', 'Friendshil with Someone is maxed out.',
                     'Frendship with Someone went up by 7.', 'Speed will go up by 5.',
                     'Training Speed Gain +5', 'An ordinary dialogue sentence.'):
            with self.subTest(text=text):
                views=[dict(text=text,confidence=99) for _ in range(3)]
                self.assertIsNone(consensus(views))
                raw=dict(lines=[dict(text='unreadable original',confidence=80)])
                extra=dict(raw_sha256=fingerprint(raw),lines=[dict(index=0,views=views)])
                self.assertEqual(apply(raw,extra),raw)

    def test_valid_receipt_families_remain_supported(self):
        for text in ('Energy recovered by 10.', 'Friendship with Uncatalogued Name went up by 7.',
                     'Gained 1 hint level(s) for Uncatalogued Skill.', 'Dance went up by 10.',
                     'New supporters joined!', 'Mood remains Great.'):
            with self.subTest(text=text):
                views=[dict(text=text,confidence=99) for _ in range(3)]
                self.assertEqual(consensus(views)['text'],text)

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

    def test_window_readings_keep_only_the_frames_of_the_named_windows(self):
        # A frame of an overlapping window, or of the same span read at
        # another rate, is not one of the window's own frames.
        from tracen_replay.inspect_receipts import window_readings
        inspection=dict(source_sha256='s'*64,windows=[],readings=[
            dict(source_timestamp_ms=1000,evidence='receipt-inspection/900-1400-30/frame-000001.png'),
            dict(source_timestamp_ms=1100,evidence='receipt-inspection/1000-1500-30/frame-000001.png'),
            dict(source_timestamp_ms=1200,evidence='receipt-inspection/900-1400-60/frame-000001.png')])
        kept=window_readings(inspection,[dict(start_ms=900,end_ms=1400)],30)
        self.assertEqual([r['source_timestamp_ms'] for r in kept['readings']],[1000])
        self.assertEqual(kept['source_sha256'],'s'*64)
        self.assertEqual(len(inspection['readings']),3)
