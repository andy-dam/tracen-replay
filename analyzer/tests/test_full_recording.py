import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import call, patch
from PIL import Image
from tests import localdata
from tests.test_gameplay import workspace_temp
from tracen_replay import full_recording
from tracen_replay.full_recording import analyze_frames, cached_readings
from tracen_replay.pipeline import PipelineError
from tracen_replay.recording_verification import audit
from tracen_replay.action_evaluate import evaluate


class FakeReader:
    Image=Image
    fingerprint='test-engine'
    inputs=[]
    def __init__(self,*args):pass
    def read(self,pane):
        if pane.size!=(810,1080):raise AssertionError('Auxiliary pixels reached recognition.')
        self.inputs.append(hashlib.sha256(pane.tobytes()).hexdigest())
        return dict(lines=[],regions={},header='',current_grid=False,result_grid=False,
                    model_sha256={'test':'model'},engine_fingerprint=self.fingerprint,gameplay_sha256=self.inputs[-1])


class FullRecordingTests(unittest.TestCase):
    def test_causal_accounting_consumes_canonical_ledger_without_mutation(self):
        from tracen_replay.full_recording import assemble
        from tests.test_turn_ledger import report as ledger_fixture
        from tests.test_boundary_state_recovery import row, STATS, missing_states, report_for
        source=ledger_fixture()
        reading=row(160,stats=STATS)
        reading.update(effects=[],ocr={'neural':[]})
        turn=dict(id='turn-029',phase='dated',calendar_value=28,
                  start_ms=100,end_ms=400,states=missing_states())
        turn['states']['stats']['opening']=dict(values=STATS,
            values_ref='/gameplay_tracking/readings/0/stats/values')
        ledger=report_for(turn)['turn_ledger']
        reconstructed={k:v for k,v in source['gameplay_tracking'].items()
                       if k not in ('readings','auxiliary_log_used')}
        observed=[]
        def causal(report):
            observed.append(report['turn_ledger']['turns'][0]['states']['stats']['opening'])
            return {}
        with patch('tracen_replay.full_recording.reconstruct',return_value=reconstructed), \
             patch('tracen_replay.full_recording.audit',return_value={}), \
             patch('tracen_replay.turn_ledger.build',return_value=ledger), \
             patch('tracen_replay.causal_accounting.build',side_effect=causal):
            result=assemble(source,[reading])
        self.assertEqual(observed[0]['values'],STATS)
        self.assertEqual(observed[0]['values_ref'],'/gameplay_tracking/readings/0/stats/values')
        self.assertIs(result['turn_ledger'],ledger)
        self.assertIsNone(result['turn_ledger']['turns'][0]['states']['stats'].get('closing'))

    def test_state_only_probes_do_not_reach_event_reconstruction(self):
        from tracen_replay.full_recording import assemble
        from tests.test_turn_ledger import report as ledger_fixture
        source=ledger_fixture()
        ordinary=dict(source_timestamp_ms=100,evidence='ordinary.png',screen='unknown',
                      stats={},facts={},effects=[],ocr={'neural':[]})
        probe=dict(ordinary,source_timestamp_ms=150,evidence='probe.png',screen='boundary_state_recovery',
                   stats={'values':{'speed':120}})
        reconstructed={k:v for k,v in source['gameplay_tracking'].items()
                       if k not in ('readings','auxiliary_log_used')}
        with patch('tracen_replay.full_recording.reconstruct',return_value=reconstructed) as reconstruction, \
             patch('tracen_replay.full_recording.audit',return_value={}):
            result=assemble(source,[ordinary,probe])
        self.assertEqual(reconstruction.call_args.args[0],[ordinary])
        self.assertEqual(result['gameplay_tracking']['readings'],[ordinary,probe])
        self.assertFalse(any(s['screen']=='boundary_state_recovery' for s in result['gameplay_tracking']['screens']))

    def test_assemble_persists_final_source_observation_projections(self):
        from tracen_replay.full_recording import assemble
        from tests.test_turn_ledger import report as ledger_fixture
        source=ledger_fixture()
        reading=dict(source_timestamp_ms=100,evidence='ordinary.png',screen='unknown',
                     stats={},facts={},effects=[],ocr={'neural':[]})
        reconstructed={k:v for k,v in source['gameplay_tracking'].items()
                       if k not in ('readings','auxiliary_log_used')}
        previews={'schema_version':'test-preview','observations':[]}
        statuses=[{'id':'/gameplay_tracking/status_observations/0','phase':'observed'}]
        states=[{'id':'/gameplay_tracking/state_observations/0','phase':'observed'}]
        with patch('tracen_replay.full_recording.reconstruct',return_value=reconstructed), \
             patch('tracen_replay.full_recording.audit',return_value={}), \
             patch('tracen_replay.preview_observations.build_preview_observations',return_value=previews) as preview_builder, \
             patch('tracen_replay.status_badges.build_observations',return_value=statuses) as status_builder, \
             patch('tracen_replay.source_state_observations.build_observations',return_value=states) as state_builder:
            result=assemble(source,[reading])
        preview_builder.assert_called_once_with(result['gameplay_tracking']['readings'])
        status_builder.assert_called_once_with(result['gameplay_tracking']['readings'])
        state_builder.assert_called_once_with(result['gameplay_tracking']['readings'])
        self.assertIs(result['gameplay_tracking']['preview_observations'],previews)
        self.assertIs(result['gameplay_tracking']['status_observations'],statuses)
        self.assertIs(result['gameplay_tracking']['state_observations'],states)

    def test_event_choice_projection_stays_separate_from_transaction_ledger(self):
        from tracen_replay.full_recording import assemble
        from tests.test_turn_ledger import report as ledger_fixture
        report = ledger_fixture()
        readings = [dict(source_timestamp_ms=100, evidence='frame.png', screen='unknown',
                         stats={}, facts={}, effects=[], ocr={'neural': []})]
        envelope = {'schema_version': 'test-choice', 'observations': [],
                    'committed_choices': []}
        reconstructed = {k: v for k, v in report['gameplay_tracking'].items()
                         if k not in ('readings', 'auxiliary_log_used')}
        reconstructed['dialogue_choices'] = [{'kind': 'legacy'}]
        with patch('tracen_replay.full_recording.reconstruct', return_value=reconstructed), \
                patch('tracen_replay.full_recording.audit', return_value={}):
            result = assemble(report, readings, committed_choices=[{'kind': 'dialogue_choice',
                'options': ['First', 'Second'], 'selected_index': 1, 'selected_text': 'Second',
                'first_seen_ms': 0, 'selection_observed_ms': 100,
                'selection_state': 'committed', 'evidence': ['choice.png']}],
                event_choice_observations=envelope)
        self.assertEqual([choice['selected_text'] for choice in result['gameplay_tracking']['dialogue_choices']],
                         ['Second'])
        self.assertEqual(result['gameplay_tracking']['event_choice_observations'], envelope)

    def test_strict_choice_abstention_cannot_be_resurrected_by_legacy_reconstruction(self):
        from tracen_replay.full_recording import assemble
        from tests.test_turn_ledger import report as ledger_fixture
        for strict_selection in (False, True):
            with self.subTest(strict_selection=strict_selection):
                source = ledger_fixture()
                readings = [dict(source_timestamp_ms=100, evidence='frame.png', screen='unknown',
                                 stats={}, facts={}, effects=[], ocr={'neural': []})]
                legacy = dict(kind='dialogue_choice', options=['First', 'Second'], selected_index=0,
                              selected_text='First', first_seen_ms=0, selection_observed_ms=100,
                              selection_state='committed', evidence=['legacy.png'])
                strict = [dict(legacy, selected_index=1, selected_text='Second',
                               evidence=['strict.png'])] if strict_selection else []
                envelope = dict(observations=[{'observation_conflict': True}],
                                committed_choices=strict)
                reconstructed = {k: v for k, v in source['gameplay_tracking'].items()
                                 if k not in ('readings', 'auxiliary_log_used')}
                reconstructed['dialogue_choices'] = [legacy]
                ledger_inputs = []
                def build_ledger(report):
                    ledger_inputs.append(report['gameplay_tracking']['dialogue_choices'])
                    return {'turns': []}
                with patch('tracen_replay.full_recording.reconstruct', return_value=reconstructed), \
                        patch('tracen_replay.full_recording.audit', return_value={}), \
                        patch('tracen_replay.turn_ledger.build', side_effect=build_ledger), \
                        patch('tracen_replay.causal_accounting.build', return_value={}):
                    result = assemble(source, readings, committed_choices=strict,
                                      event_choice_observations=envelope)
                expected = ['Second'] if strict_selection else []
                self.assertEqual([item['selected_text'] for item in result['gameplay_tracking']['dialogue_choices']], expected)
                self.assertEqual([item['selected_text'] for item in ledger_inputs[0]], expected)
                self.assertEqual(result['gameplay_tracking']['event_choice_observations'], envelope)

    def test_cli_loads_hint_cache_and_does_not_publish_invalid_evidence(self):
        from contextlib import ExitStack
        from tracen_replay.full_recording import main
        for invalid in (False, True):
            with self.subTest(invalid=invalid), workspace_temp() as root, ExitStack() as stack:
                report={'source':{'sha256':'a'*64,'duration_ms':2000},
                        'frames':[{'id':'frame-000001','source_timestamp_ms':1000,
                                   'evidence':'source.png'}]}
                rows=[{'evidence':'source.png','source_timestamp_ms':1000,'screen':'unknown'}]
                report.update(gameplay_tracking={'readings':rows},turn_ledger={'turns':[]})
                candidates=[{'identity':'prepared source evidence'}]
                stack.enter_context(patch('sys.argv',['full_recording','source.mp4','--output',str(root),'--reparse-only']))
                stack.enter_context(patch('tracen_replay.full_recording.capture',return_value=report))
                stack.enter_context(patch('tracen_replay.full_recording.cached_readings',return_value=rows))
                stack.enter_context(patch('tracen_replay.full_recording.analyze_frames',side_effect=AssertionError('OCR during replay')))
                stack.enter_context(patch('tracen_replay.inspect_choices.load',return_value=({},[])))
                stack.enter_context(patch('tracen_replay.race_reward_inspection.load',return_value=({},[])))
                choice_source={'schema_version':'test-choice','observations':[],
                               'committed_choices':[]}
                stack.enter_context(patch('tracen_replay.full_recording.build_event_choice_observations',
                                          return_value=choice_source))
                loader=stack.enter_context(patch('tracen_replay.hint_card_cache.load',return_value=candidates,
                    side_effect=ValueError('stale source proof') if invalid else None))
                assemble=stack.enter_context(patch('tracen_replay.full_recording.assemble',return_value=report))
                stack.enter_context(patch('tracen_replay.full_recording.validate_output'))
                save=stack.enter_context(patch('tracen_replay.full_recording.save_json'))
                stack.enter_context(patch('tracen_replay.full_recording.render',return_value='verified report'))
                stack.enter_context(patch('builtins.print'))
                if invalid:
                    # An invalid hint cache is an enrichment failure: the run
                    # completes without hint observations and records why.
                    main()
                    assemble.assert_called_once_with(
                        report,rows,[],[],None,
                        source_root=root,
                        committed_choices=[],event_choice_observations=choice_source)
                    self.assertEqual([f['stage'] for f in report['stage_failures']],['hint_card_cache'])
                    self.assertIn('stale source proof',report['stage_failures'][0]['error'])
                    self.assertEqual(save.call_args_list[-1], call(root/'report.json',report))
                else:
                    main()
                    assemble.assert_called_once_with(
                        report,rows,[],[],candidates,
                        source_root=root,
                        committed_choices=[],event_choice_observations=choice_source)
                    self.assertEqual(save.call_args_list[-1], call(root/'report.json',report))
                    self.assertEqual(save.call_args_list[0].args[0], root/'automatic-refinement.json')
                loader.assert_called_once_with(rows,root,'a'*64)

    def test_fresh_cli_runs_refinement_and_choice_source_before_assembly(self):
        from contextlib import ExitStack
        from tracen_replay.full_recording import main
        with workspace_temp() as root, ExitStack() as stack:
            source = root / 'source.mp4'
            source.write_bytes(b'source')
            report = {
                'source': {'sha256': 'a' * 64, 'duration_ms': 2000},
                'frames': [],
            }
            rows = []
            assembled = {'gameplay_tracking': {'readings': rows}}
            choice_source = {'schema_version': 'test-choice', 'observations': [],
                             'committed_choices': []}
            stack.enter_context(patch('sys.argv', ['full_recording', str(source),
                                                   '--output', str(root)]))
            stack.enter_context(patch('tracen_replay.full_recording.capture', return_value=report))
            analyze = stack.enter_context(patch('tracen_replay.full_recording.analyze_frames',
                                                return_value=rows))
            cached = stack.enter_context(patch('tracen_replay.full_recording.cached_readings',
                                               return_value=rows))
            stack.enter_context(patch('tracen_replay.receipt_recovery.recover',
                                      return_value=(rows, {})))
            stack.enter_context(patch('tracen_replay.training_gain_recovery.recover',
                                      return_value=(rows, {})))
            occluded = stack.enter_context(patch(
                'tracen_replay.occluded_receipt_recovery.recover',
                return_value=(rows, {'schema': 'occluded'})))
            stack.enter_context(patch('tracen_replay.inspect_choices.load', return_value=(None, [])))
            stack.enter_context(patch('tracen_replay.race_reward_inspection.load',
                                      return_value=(None, [])))
            stack.enter_context(patch('tracen_replay.hint_card_cache.load', return_value=[]))
            automatic = stack.enter_context(patch('tracen_replay.automatic_refinement.run',
                                                  return_value={'schema_version': 'test-auto',
                                                                'generated': {}}))
            race_quantities = stack.enter_context(patch(
                'tracen_replay.full_recording._generate_race_quantity_refinement',
                return_value={'source_sha256': 'a' * 64, 'artifact_count': 2},
            ))
            builder = stack.enter_context(patch(
                'tracen_replay.full_recording.build_event_choice_observations',
                return_value=choice_source))
            prepare_hints = stack.enter_context(patch(
                'tracen_replay.full_recording._prepare_fresh_hint_cards'))
            assemble = stack.enter_context(patch('tracen_replay.full_recording.assemble',
                                                 return_value=assembled))
            stack.enter_context(patch('tracen_replay.boundary_state_recovery.recover',
                                      return_value=(rows, {})))
            stack.enter_context(patch('tracen_replay.full_recording.validate_output'))
            stack.enter_context(patch('tracen_replay.full_recording.save_json'))
            stack.enter_context(patch('tracen_replay.full_recording.render', return_value='report'))
            stack.enter_context(patch('builtins.print'))

            main()

            # The CLI's default model directory, whatever this checkout's local evidence base is.
            analyze.assert_called_once_with(report, root, 4, Path('.local/models/rapidocr'), pool=full_recording.ocr_pool_for_device())
            automatic.assert_called_once_with(
                report, root, allow_ocr=True, model_dir=Path('.local/models/rapidocr'),
                max_panel_frames=512, max_status_frames=512,
            )
            race_quantities.assert_called_once_with(
                root,
                model_dir=Path('.local/models/rapidocr'),
            )
            cached.assert_called_once_with(report, root, workers=4)
            prepare_hints.assert_called_once_with(
                rows, root, 'a' * 64, model_dir=Path('.local/models/rapidocr'))
            builder.assert_called_once_with(rows, root, [])
            assemble.assert_called_once_with(
                report, rows, [], [], [], committed_choices=[],
                source_root=root,
                event_choice_observations=choice_source,
            )
            occluded.assert_called_once()
            self.assertIs(occluded.call_args.kwargs['allow_ocr'], True)

    def test_reparse_cli_keeps_generic_receipt_promotions_after_cached_parse(self):
        from contextlib import ExitStack
        from tracen_replay.full_recording import main
        with workspace_temp() as root, ExitStack() as stack:
            source = root / 'source.mp4'
            source.write_bytes(b'source')
            report = {
                'source': {'sha256': 'a' * 64, 'duration_ms': 2000},
                'frames': [],
            }
            base_rows = [{'source_timestamp_ms': 10, 'evidence': 'frame.png',
                          'screen': 'event_outcome', 'facts': {}, 'effects': []}]
            promoted_rows = [dict(base_rows[0], promoted=True)]
            assembled = {'gameplay_tracking': {'readings': promoted_rows}}
            choice_source = {'schema_version': 'test-choice', 'observations': [],
                             'committed_choices': []}
            stack.enter_context(patch('sys.argv', ['full_recording', str(source),
                                                   '--output', str(root), '--reparse-only']))
            stack.enter_context(patch('tracen_replay.full_recording.capture', return_value=report))
            stack.enter_context(patch('tracen_replay.full_recording.cached_readings',
                                      return_value=base_rows))
            stack.enter_context(patch('tracen_replay.full_recording.analyze_frames',
                                      side_effect=AssertionError('OCR during replay')))
            stack.enter_context(patch('tracen_replay.receipt_recovery.recover',
                                      return_value=(base_rows, {})))
            stack.enter_context(patch('tracen_replay.training_gain_recovery.recover',
                                      return_value=(base_rows, {})))
            occluded = stack.enter_context(patch(
                'tracen_replay.occluded_receipt_recovery.recover',
                return_value=(promoted_rows, {'schema': 'occluded'})))
            stack.enter_context(patch('tracen_replay.inspect_choices.load', return_value=(None, [])))
            stack.enter_context(patch('tracen_replay.race_reward_inspection.load',
                                      return_value=(None, [])))
            stack.enter_context(patch('tracen_replay.hint_card_cache.load', return_value=[]))
            prepare_hints = stack.enter_context(patch(
                'tracen_replay.full_recording._prepare_fresh_hint_cards'))
            stack.enter_context(patch('tracen_replay.automatic_refinement.run',
                                      return_value={'schema_version': 'test-auto'}))
            stack.enter_context(patch('tracen_replay.full_recording.build_event_choice_observations',
                                      return_value=choice_source))
            assemble = stack.enter_context(patch('tracen_replay.full_recording.assemble',
                                                 return_value=assembled))
            stack.enter_context(patch('tracen_replay.boundary_state_recovery.recover',
                                      return_value=(promoted_rows, {})))
            stack.enter_context(patch('tracen_replay.full_recording.validate_output'))
            stack.enter_context(patch('tracen_replay.full_recording.save_json'))
            stack.enter_context(patch('tracen_replay.full_recording.render', return_value='report'))
            stack.enter_context(patch('builtins.print'))

            main()

            occluded.assert_called_once()
            self.assertIs(occluded.call_args.kwargs['allow_ocr'], False)
            prepare_hints.assert_not_called()
            self.assertIs(assemble.call_args.args[1], promoted_rows)

    def test_fresh_cli_preserves_numeric_and_training_recovery_after_cache_reload(self):
        from contextlib import ExitStack
        from tracen_replay.full_recording import main
        with workspace_temp() as root, ExitStack() as stack:
            report = {'source': {'sha256': 'a' * 64, 'duration_ms': 2000}, 'frames': []}
            base = [{'source_timestamp_ms': 10, 'evidence': 'base.png',
                     'screen': 'unknown', 'facts': {}, 'effects': []}]
            refined = [dict(base[0], facts={'refinement_loaded': True})]
            inspected = {'source_timestamp_ms': 15, 'evidence': 'inspection.png',
                         'screen': 'training_result', 'stats': {},
                         'facts': {'training_gains': {'wit': 7}}, 'effects': []}
            (root / 'training-inspection.json').write_text(json.dumps({
                'source_sha256': 'a' * 64, 'windows': []}), encoding='utf-8')
            numeric = {'source_timestamp_ms': 20, 'evidence': 'receipt.png',
                       'screen': 'event_outcome', 'facts': {},
                       'effects': [{'kind': 'energy_change', 'amount': -10}]}
            training = {'source_timestamp_ms': 30, 'evidence': 'training.png',
                        'screen': 'training_result', 'facts': {'training_gains': {'speed': 35}},
                        'effects': []}
            stack.enter_context(patch('sys.argv', ['full_recording', 'source.mp4', '--output', str(root)]))
            stack.enter_context(patch('tracen_replay.full_recording.capture', return_value=report))
            stack.enter_context(patch('tracen_replay.full_recording.analyze_frames', return_value=base))
            stack.enter_context(patch('tracen_replay.full_recording.cached_readings', return_value=refined))
            stack.enter_context(patch('tracen_replay.inspect_training.reparse_inspection',
                                      return_value=[inspected]))
            receipts = stack.enter_context(patch('tracen_replay.receipt_recovery.recover',
                side_effect=lambda source, root, info, rows, events, **kw: (rows + [numeric], {})))
            gains = stack.enter_context(patch('tracen_replay.training_gain_recovery.recover',
                side_effect=lambda source, root, info, rows, events, **kw: (rows + [training], {})))
            stack.enter_context(patch('tracen_replay.transactions.outcome_events', return_value=[]))
            stack.enter_context(patch('tracen_replay.transactions.training_events', return_value=[]))
            stack.enter_context(patch('tracen_replay.occluded_receipt_recovery.recover',
                side_effect=lambda source, root, info, rows, events, **kw: (rows, {})))
            for target in ('inspect_choices.load', 'race_reward_inspection.load'):
                stack.enter_context(patch('tracen_replay.' + target, return_value=({}, [])))
            hints = stack.enter_context(patch('tracen_replay.hint_card_cache.load', return_value=[]))
            stack.enter_context(patch('tracen_replay.automatic_refinement.run', return_value={}))
            choices = {'committed_choices': [], 'observations': []}
            stack.enter_context(patch('tracen_replay.full_recording.build_event_choice_observations', return_value=choices))
            assembler = stack.enter_context(patch('tracen_replay.full_recording.assemble',
                side_effect=lambda report, rows, *args, **kw: dict(report, gameplay_tracking={'readings': rows})))
            stack.enter_context(patch('tracen_replay.boundary_state_recovery.recover',
                side_effect=lambda source, root, report, rows, **kw: (rows, {})))
            stack.enter_context(patch('tracen_replay.full_recording.validate_output'))
            stack.enter_context(patch('tracen_replay.full_recording.save_json'))
            stack.enter_context(patch('tracen_replay.full_recording.render', return_value='report'))
            stack.enter_context(patch('builtins.print'))
            main()
            result = assembler.call_args.args[1]
            self.assertEqual([(r['source_timestamp_ms'], r['evidence']) for r in result],
                             [(10, 'base.png'), (15, 'inspection.png'),
                              (20, 'receipt.png'), (30, 'training.png')])
            self.assertTrue(result[0]['facts']['refinement_loaded'])
            self.assertEqual(result[1]['facts']['training_gains'], {'wit': 7})
            self.assertEqual(result[2:], [numeric, training])
            self.assertEqual(receipts.call_args.args[3], result[:2])
            self.assertEqual(gains.call_args.args[3], result[:3])
            self.assertEqual(hints.call_args.args[0], result)

    def test_neural_input_is_independent_of_auxiliary_pixels_and_cache_rejects_tampering(self):
        with workspace_temp() as root:
            frame=dict(id='one',evidence='frame.png',source_timestamp_ms=0)
            report=dict(frames=[frame]);FakeReader.inputs=[]
            with patch('tracen_replay.full_recording.NeuralReader',FakeReader):
                for background in ('black','red'):
                    image=Image.new('RGB',(1920,1080),background)
                    image.paste(Image.new('RGB',(810,1080),'white'),(148,0));image.save(root/'frame.png')
                    analyze_frames(report,root,workers=1)
            self.assertEqual(len(FakeReader.inputs),2)
            self.assertEqual(FakeReader.inputs[0],FakeReader.inputs[1])
            self.assertEqual(len(cached_readings(report,root)),1)
            (root/'frame.png').write_bytes(b'changed')
            with self.assertRaisesRegex(PipelineError,'source evidence mismatch'):cached_readings(report,root)

    def test_missing_ocr_is_not_complete(self):
        with workspace_temp() as root:
            report=dict(frames=[dict(id='absent',evidence='absent.png',source_timestamp_ms=0)])
            self.assertEqual(cached_readings(report,root,allow_partial=True),[])
            with self.assertRaisesRegex(PipelineError,'Missing OCR'):cached_readings(report,root)

    def test_source_coverage_checks_pts_and_missing_samples(self):
        frames=[dict(source_timestamp_ms=t,source_pts=t*60//1000,time_base='1/60') for t in (0,250,500,750)]
        report=dict(source=dict(sha256='test',duration_ms=1000),sampling=dict(requested_fps=4),frames=frames,
            gameplay_tracking=dict(readings=[dict(source_timestamp_ms=f['source_timestamp_ms'],screen='unknown') for f in frames],intervals=[]))
        result=audit(report)
        self.assertTrue(result['full_source_processed']);self.assertFalse(result['fully_verified']);self.assertFalse(result['go_ready'])
        report['frames'][1]['source_pts']=0
        self.assertIn('source_pts_timestamp_mismatch',audit(report)['source_coverage_errors'])
        report['gameplay_tracking']['readings'].pop()
        self.assertIn('unprocessed_base_frames',audit(report)['source_coverage_errors'])

    def test_reviewed_negative_action_scope_detects_false_completions(self):
        reference=dict(source_sha256='one',start_ms=100,end_ms=200,kinds=['race'],scope='race entry only',
            actions=[],no_completed_actions=True,independently_reviewed=True)
        report=dict(source=dict(sha256='one'),gameplay_tracking=dict(auxiliary_log_used=False,
            turn_action_receipts=[dict(kind='race',source_timestamp_ms=200)]))
        result=evaluate(reference,report)
        self.assertTrue(result['passed']);self.assertIsNone(result['recall']);self.assertIsNone(result['precision'])
        report['gameplay_tracking']['turn_action_receipts'].append(dict(kind='race',source_timestamp_ms=150))
        result=evaluate(reference,report)
        self.assertFalse(result['passed']);self.assertEqual(len(result['extra']),1)
        self.assertEqual(result['precision'],0);self.assertIsNone(result['recall'])

    def test_empty_action_labels_require_explicit_review(self):
        reference=dict(source_sha256='one',start_ms=100,end_ms=200,kinds=['race'],scope='unknown',actions=[])
        report=dict(source=dict(sha256='one'),gameplay_tracking=dict(auxiliary_log_used=False,turn_action_receipts=[]))
        for changes in ({},{'no_completed_actions':True},{'independently_reviewed':True},
                        {'no_completed_actions':True,'independently_reviewed':True,'end_ms':100},
                        {'no_completed_actions':True,'independently_reviewed':True,'actions':[dict(kind='race',start_ms=110,end_ms=150)]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):evaluate(dict(reference,**changes),report)

    def test_duplicate_action_receipts_reduce_precision(self):
        reference=dict(source_sha256='one',start_ms=0,end_ms=2000,kinds=['training'],scope='training only',
            actions=[dict(kind='training',training_option='speed',start_ms=500,end_ms=1000)])
        action=dict(kind='training',training_option='speed',source_timestamp_ms=750)
        report=dict(source=dict(sha256='one'),gameplay_tracking=dict(auxiliary_log_used=False,turn_action_receipts=[action,dict(action,source_timestamp_ms=800)]))
        result=evaluate(reference,report)
        self.assertEqual(result['recall'],1);self.assertEqual(result['precision'],.5);self.assertFalse(result['passed'])
        report['source']['sha256']='two'
        with self.assertRaises(ValueError):evaluate(reference,report)


class FreshHintPreparationTests(unittest.TestCase):
    def setUp(self):
        fixture_class = __import__(
            "tests.test_hint_card_identity",
            fromlist=["HintCardIdentityTests"],
        ).HintCardIdentityTests
        self.fixture = fixture_class(
            "test_valid_source_chain_recovers_card_and_preserves_raw_names"
        )
        self.fixture.setUp()
        self.root = self.fixture.root
        self.rows = self.fixture.rows
        for path in (self.root / "neural").glob("*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["engine_fingerprint"] = "e" * 64
            path.write_text(json.dumps(raw), encoding="utf-8")
        self.candidate = self.fixture._patched_recover()[0]
        self.model_dir = self.root / "models"

    def tearDown(self):
        self.fixture.tearDown()

    def _source_pixels(self):
        # Reuse the cache suite's deterministic source-pixel patch.  The
        # identity fixture deliberately keeps this detector plumbing local to
        # the cache tests, so import it lazily to avoid a test-module cycle.
        from tests.test_hint_card_cache import HintCardCacheTests

        def overlays(image):
            index = int(image.getpixel((0, 0))[0]) - 1
            return [
                [
                    604 - index * 5,
                    854 - (index == 2) * 5,
                    617 - index * 5,
                    871 - (index == 2) * 4,
                ]
            ]

        return HintCardCacheTests._patches(
            "tracen_replay.inventory_suffix.detect",
            side_effect=["single_circle"] * 6,
            extra=("tracen_replay.receipt_occlusion.overlay_boxes", overlays),
        )

    def _prepare(self):
        from tracen_replay.full_recording import _prepare_fresh_hint_cards

        return _prepare_fresh_hint_cards(
            self.rows,
            self.root,
            "a" * 64,
            model_dir=self.model_dir,
        )

    def _cached_readings_shape(self):
        """Add the source envelope emitted by ``cached_readings``.

        The normal parser keeps the capture frame id and digest on a row but
        intentionally omits the manifest-derived source path.  This fixture
        mirrors that shape without changing the source or neural artifacts.
        """
        capture = json.loads((self.root / "capture.json").read_text(encoding="utf-8"))
        rows = []
        for row, frame in zip(self.rows, capture["frames"]):
            raw = json.loads(
                (self.root / "neural" / f"{frame['id']}.json").read_text(encoding="utf-8")
            )
            rows.append(
                dict(
                    row,
                    source_sha256="a" * 64,
                    source_frame_id=frame["id"],
                    source_frame_sha256=raw["source_frame_sha256"],
                    gameplay_sha256=raw["gameplay_sha256"],
                    engine_fingerprint=raw["engine_fingerprint"],
                    model_sha256=raw["model_sha256"],
                )
            )
        return rows

    def test_cached_readings_shape_derives_manifest_path_before_recovery(self):
        from tracen_replay.full_recording import _prepare_fresh_hint_cards

        cached_rows = self._cached_readings_shape()
        observed = {}

        def recover_with_actual_source(rows, evidence_root, *, source_sha256, model_dir):
            observed["rows"] = rows
            observed["root"] = evidence_root
            observed["source_sha256"] = source_sha256
            observed["model_dir"] = model_dir
            # The fixture helper calls the real recover implementation while
            # supplying deterministic OCR/suffix seams.
            return self.fixture._patched_recover(rows=rows)

        with self._source_pixels(), patch(
            "tracen_replay.hint_card_identity.recover",
            side_effect=recover_with_actual_source,
        ):
            loaded = _prepare_fresh_hint_cards(
                cached_rows,
                self.root,
                "a" * 64,
                model_dir=self.model_dir,
            )

        self.assertEqual([(item["name"], item["amount"]) for item in loaded],
                         [("Winter Runner", 2)])
        self.assertTrue(
            all(isinstance(row.get("source_frame_path"), str) for row in observed["rows"])
        )
        self.assertTrue(all("source_frame_path" not in row for row in cached_rows))
        self.assertEqual(observed["root"], self.root)
        self.assertEqual(observed["source_sha256"], "a" * 64)
        self.assertEqual(observed["model_dir"], self.model_dir)

    def test_fresh_no_cache_recovers_and_persists_source_bound_candidates(self):
        from tracen_replay.full_recording import _prepare_fresh_hint_cards

        with self._source_pixels(), patch(
            "tracen_replay.hint_card_identity.recover",
            return_value=[self.candidate],
        ) as recover:
            loaded = _prepare_fresh_hint_cards(
                self.rows,
                self.root,
                "a" * 64,
                model_dir=self.model_dir,
            )

        cache_path = self.root / "hint-card-recovery.json"
        self.assertTrue(cache_path.is_file())
        self.assertEqual(
            [(item["name"], item["amount"]) for item in loaded],
            [("Winter Runner", 2)],
        )
        recover.assert_called_once_with(
            self.rows,
            self.root,
            source_sha256="a" * 64,
            model_dir=self.model_dir,
        )
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(payload["candidates"][0]["source_sha256"], "a" * 64)

    def test_existing_valid_cache_replays_without_recovery_or_ocr(self):
        from tracen_replay.full_recording import _prepare_fresh_hint_cards

        with self._source_pixels(), patch(
            "tracen_replay.hint_card_identity.recover",
            return_value=[self.candidate],
        ):
            self._prepare()
        with self._source_pixels(), patch(
            "tracen_replay.hint_card_identity.recover",
            side_effect=AssertionError("existing cache must not recover"),
        ), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("existing cache must not construct OCR"),
        ):
            loaded = _prepare_fresh_hint_cards(
                self.rows,
                self.root,
                "a" * 64,
                model_dir=self.model_dir,
            )
        self.assertEqual(loaded[0]["name"], "Winter Runner")

    def test_existing_invalid_cache_fails_before_recovery(self):
        from tracen_replay.full_recording import _prepare_fresh_hint_cards

        cache_path = self.root / "hint-card-recovery.json"
        cache_path.write_text("{}", encoding="utf-8")
        with patch(
            "tracen_replay.hint_card_identity.recover",
            side_effect=AssertionError("invalid cache must not recover"),
        ) as recover, self.assertRaisesRegex(
            PipelineError, "Hint-card cache evidence invalid"
        ):
            _prepare_fresh_hint_cards(
                self.rows,
                self.root,
                "a" * 64,
                model_dir=self.model_dir,
            )
        recover.assert_not_called()

    def test_source_mismatch_and_duplicate_candidate_are_rejected(self):
        from tracen_replay.full_recording import _prepare_fresh_hint_cards

        for bad_candidate in (
            dict(self.candidate, source_sha256="b" * 64),
            dict(
                self.candidate,
                observations=[
                    self.candidate["observations"][0],
                    self.candidate["observations"][0],
                    self.candidate["observations"][2],
                ],
                source_timestamps_ms=[1000, 1000, 1500],
            ),
        ):
            with self.subTest(candidate=bad_candidate.get("source_sha256")):
                with patch(
                    "tracen_replay.hint_card_identity.recover",
                    return_value=[bad_candidate],
                ), self._source_pixels(), self.assertRaisesRegex(
                    PipelineError, "Fresh hint-card preparation failed"
                ):
                    _prepare_fresh_hint_cards(
                        self.rows,
                        self.root,
                        "a" * 64,
                        model_dir=self.model_dir,
                    )
                cache_path = self.root / "hint-card-recovery.json"
                if cache_path.exists():
                    cache_path.unlink()


if __name__=='__main__':unittest.main()
