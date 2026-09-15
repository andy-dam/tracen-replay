import json
from pathlib import Path
import unittest

from scripts.freeze_final_reliability import RUNS, digest, freeze, validate_references, verify_manifest
from tests.test_gameplay import workspace_temp


class ReferenceFreezeTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(workspace_temp()).resolve()
        self.selection = self.root / 'selection.json'
        self.references = []
        self.images = []
        selected = []
        for run in RUNS:
            evidence = self.root / run
            evidence.mkdir()
            image = evidence / 'frame.png'
            image.write_bytes(b'fixed source image')
            self.images.append(image)
            cases = []
            for index in range(8):
                identity, turn = f'{run}-{index}', f'turn-{index}'
                selected.append(dict(id=identity, run=run, turn_id=turn, start_ms=index*100,
                    end_ms=(index+1)*100, source_sha256='a'*64, evidence_root=str(evidence)))
                cases.append(dict(case_id=identity, turn_id=turn, scope_ms=[index*100,(index+1)*100],
                    reference_complete=True, review_coverage=dict(categories=['effect'],fields=['speed'],
                        intervals_ms=[[index*100,(index+1)*100]]), unobservable_intervals=[], notes=[], observations=[dict(
                        id=f'effect-{index}', start_ms=index*100+10, end_ms=index*100+20,
                        category='effect',phase='applied',payload=dict(kind='stat_change',field='speed',amount=5),
                        expected_turn_id=turn, status='observed', evidence=['frame.png'])]))
            path = self.root / f'{run}.json'
            self.write(path, dict(schema_version='final-reliability-source-reference-v1', run=run,
                auxiliary_log_used=False, source_sha256='a'*64, cases=cases, image_sha256={'frame.png':digest(image)}))
            self.references.append(path)
        self.write(self.selection, {'cases':selected})
        checklist = self.root / 'checklist.md'
        checklist.write_text('Original required work',encoding='utf-8')
        self.baseline = self.root / 'baseline.json'
        self.write(self.baseline, dict(checklist='checklist.md',worktree_sha256={'checklist.md':digest(checklist)}))
        self.target = self.root / 'targeted.json'
        self.write(self.target, {'source_review':'Targeted numeric and ownership cases'})
        code = self.root / 'tracen_replay'
        code.mkdir()
        (code / 'reader.py').write_text('# before tuning',encoding='utf-8')
        self.output = self.root / 'freeze.json'
        self.anchors = dict(expected_selection_sha256=digest(self.selection),
                            expected_baseline_sha256=digest(self.baseline))

    def write(self, path, data):
        path.write_text(json.dumps(data),encoding='utf-8')

    def create(self, **options):
        return freeze(self.selection,self.references,[self.target],self.baseline,self.output,self.root,
                      **self.anchors, **options)

    def verify(self):
        return verify_manifest(self.output,**self.anchors)

    def test_ownership_from_a_ledger_accepts_an_observation_owned_by_the_later_turn(self):
        # A baseline case whose window the current ledger splits: the case keeps
        # the turn at its start, the later observation belongs to the next turn.
        reference = json.loads(self.references[0].read_text(encoding='utf-8'))
        reference['cases'][0]['observations'][0]['expected_turn_id'] = 'turn-1'
        self.write(self.references[0], reference)
        with self.assertRaisesRegex(ValueError, 'Unexpected labeled owner'):
            self.create()
        ledgers = {}
        for run in RUNS:
            turns = [dict(id=f'turn-{index}', start_ms=index*100, end_ms=(index+1)*100) for index in range(8)]
            if run == RUNS[0]:
                turns[0]['end_ms'] = 5
                turns[1]['start_ms'] = 5
            path = self.root / f'{run}-report.json'
            self.write(path, dict(turn_ledger=dict(turns=turns)))
            ledgers[run] = str(path)
        manifest = self.create(owner_ledger_paths=ledgers)
        self.assertEqual(manifest['ownership'], 'ledger_time_window')
        self.assertIn(str(self.root / f'{RUNS[0]}-report.json'), manifest['immutable_files_sha256'])
        self.verify()
        # The ledger is bound: a re-numbered ledger no longer verifies the freeze.
        path = self.root / f'{RUNS[0]}-report.json'
        moved = json.loads(path.read_text(encoding='utf-8'))
        moved['turn_ledger']['turns'][0]['end_ms'] = 50
        moved['turn_ledger']['turns'][1]['start_ms'] = 50
        self.write(path, moved)
        with self.assertRaisesRegex(ValueError, 'Frozen evidence changed'):
            self.verify()

    def test_freeze_accepts_code_changes_but_rejects_mutated_reference(self):
        self.create()
        (self.root/'tracen_replay'/'reader.py').write_text('# tuned implementation',encoding='utf-8')
        self.verify()
        self.references[0].write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Frozen evidence changed'):
            self.verify()

    def test_changed_image_fails_even_when_reference_bytes_do_not_change(self):
        self.create()
        self.images[0].write_bytes(b'other source pixels')
        with self.assertRaisesRegex(ValueError,'Frozen evidence changed'):
            self.verify()

    def test_recorded_checkpoint_rejects_manifest_metadata_changes(self):
        document = self.create()
        checkpoint = digest(self.output)
        verify_manifest(self.output, expected_manifest_sha256=checkpoint, **self.anchors)
        document['analyzer_code_at_freeze'] = {}
        self.write(self.output, document)
        with self.assertRaisesRegex(ValueError, 'freeze checkpoint changed'):
            verify_manifest(self.output, expected_manifest_sha256=checkpoint, **self.anchors)

    def make_dependency_bundle(self):
        sidecar = self.root / 'raw-ocr.json'
        self.write(sidecar, {'text': '+7', 'source_timestamp_ms': 25})
        bundle = self.root / 'source-bundle.json'
        self.write(bundle, {
            'schema_version': 'tracen-replay/final-source-review-bundle-v1',
            'review_documents': [{'path': str(self.target), 'sha256': digest(self.target)}],
            'supporting_documents': [],
            'immutable_files_sha256': {str(p): digest(p) for p in (self.target, sidecar)},
        })
        return bundle, sidecar

    def test_target_review_binds_raw_evidence_dependencies(self):
        bundle, sidecar = self.make_dependency_bundle()
        self.create(dependency_bundle_paths=[bundle])
        self.verify()
        self.write(sidecar, {'text': '+72', 'source_timestamp_ms': 25})
        with self.assertRaisesRegex(ValueError, 'Frozen evidence changed'):
            self.verify()

    def test_dependency_cannot_be_removed_from_frozen_hash_map(self):
        bundle, sidecar = self.make_dependency_bundle()
        manifest = self.create(dependency_bundle_paths=[bundle])
        manifest['immutable_files_sha256'].pop(str(sidecar))
        self.write(self.output, manifest)
        with self.assertRaisesRegex(ValueError, 'Source dependency omitted'):
            self.verify()

    def test_coherent_replacement_selection_cannot_redefine_the_benchmark(self):
        selection = json.loads(self.selection.read_text())
        selection['cases'][0]['start_ms'] = 1
        reference = json.loads(self.references[0].read_text())
        reference['cases'][0]['scope_ms'][0] = 1
        reference['cases'][0]['review_coverage']['intervals_ms'][0][0] = 1
        self.write(self.selection,selection)
        self.write(self.references[0],reference)
        with self.assertRaisesRegex(ValueError,'selected benchmark changed'):
            self.create()

    def test_missing_case_cannot_silently_shrink_evaluation(self):
        doc = json.loads(self.references[0].read_text())
        doc['cases'].pop()
        self.write(self.references[0],doc)
        with self.assertRaises(ValueError):
            self.create()
        self.assertFalse(self.output.exists())

    def test_scope_visibility_or_proof_errors_prevent_freeze(self):
        original = json.loads(self.references[0].read_text())
        for error in ('scope','incomplete','proof','time','owner','source','duplicate'):
            with self.subTest(error=error):
                doc = json.loads(json.dumps(original))
                case = doc['cases'][0]
                obs = case['observations'][0]
                if error=='scope': case['scope_ms'][1] += 1
                elif error=='incomplete': case['reference_complete'] = False
                elif error=='proof': obs['evidence'] = []
                elif error=='time': obs['start_ms'] = obs['end_ms'] = 100
                elif error=='owner': obs['expected_turn_id'] = 'wrong-turn'
                elif error=='source': doc['source_sha256'] = 'b'*64
                else: doc['cases'][1] = case
                self.write(self.references[0],doc)
                with self.assertRaises(ValueError):
                    validate_references(self.selection,self.references)
        self.write(self.references[0],original)

    def test_outside_evidence_is_rejected(self):
        doc = json.loads(self.references[0].read_text())
        doc['cases'][0]['observations'][0]['evidence'] = ['../targeted.json']
        doc['image_sha256']['../targeted.json'] = digest(self.target)
        self.write(self.references[0],doc)
        with self.assertRaisesRegex(ValueError,'relative within'):
            self.create()

    def test_auxiliary_log_independence_must_be_explicit(self):
        original = json.loads(self.references[0].read_text())
        for value in (None, True):
            doc = dict(original, auxiliary_log_used=value)
            self.write(self.references[0],doc)
            with self.assertRaisesRegex(ValueError,'auxiliary side-log'):
                self.create()

    def test_coverage_must_be_usable_by_the_semantic_evaluator(self):
        doc = json.loads(self.references[0].read_text())
        doc['cases'][0]['review_coverage'] = {'categories':['effect']}
        self.write(self.references[0],doc)
        with self.assertRaisesRegex(ValueError,'gradeable field/time coverage'):
            self.create()

    def test_observed_effect_cannot_be_outside_declared_review_fields(self):
        doc = json.loads(self.references[0].read_text())
        doc['cases'][0]['review_coverage']['fields'] = ['unrelated_kind']
        self.write(self.references[0], doc)
        with self.assertRaisesRegex(ValueError, 'outside declared review coverage'):
            self.create()

    def test_freeze_cannot_overwrite_or_accept_changed_checklist(self):
        self.create()
        with self.assertRaisesRegex(ValueError,'cannot be overwritten'):
            self.create()
        self.output = self.root/'other-freeze.json'
        (self.root/'checklist.md').write_text('Reduced scope',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'checklist changed'):
            self.create()


if __name__ == '__main__':
    unittest.main()
