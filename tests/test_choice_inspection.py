import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from tests.test_gameplay import workspace_temp
from tests.test_full_recording import FakeReader
from tests import test_choice_evidence as choice_tests
from tracen_replay.choice_evidence import collect,reconstruct
from tracen_replay.inspect_choices import inspect,load
from tracen_replay.pipeline import PipelineError
from tracen_replay.transactions import reconstruct as transactions
from tracen_replay.verify_evidence import verify


class ChoiceInspectionTests(unittest.TestCase):
    def test_source_dense_response_regression(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/choice-dense-response-v1.json').read_text(encoding='utf-8'))
        events=reconstruct(fixture['observations'])
        self.assertEqual([{k:e[k] for k in ('options','selected_index','selected_text','selection_observed_ms')} for e in events],fixture['expected'])
        self.assertEqual(events[0]['kind'],'dialogue_response')

    def test_duplicates_do_not_create_repeated_menu_evidence(self):
        row=choice_tests.ChoiceEvidenceTests().observations()[0]
        base=[dict(screen='unknown',source_timestamp_ms=0,evidence=row['evidence'],facts={'choice_observation':row})]
        self.assertEqual(len(collect(base,[row])),1)
        self.assertEqual(reconstruct(collect(base,[row,choice_tests.ChoiceEvidenceTests().observations()[-1]])),[])

    def test_known_screen_boundary_wins_in_both_directions(self):
        row=choice_tests.ChoiceEvidenceTests().observations()[0]
        base=[dict(screen='event_outcome',source_timestamp_ms=0)]
        self.assertTrue(collect(base,[row])[0]['screen_boundary'])
        base=[dict(screen='unknown',source_timestamp_ms=0,evidence='a.png',facts={'choice_observation':row})]
        self.assertTrue(collect(base,[dict(source_timestamp_ms=0,screen_boundary=True)])[0]['screen_boundary'])
        self.assertTrue(collect([dict(screen='event_outcome',source_timestamp_ms=0)]+base)[0]['screen_boundary'])

    def test_choice_only_inspection_cannot_change_accounting(self):
        before=transactions([]);after=transactions([],choice_tests.ChoiceEvidenceTests().observations())
        self.assertEqual(len(after.pop('dialogue_choices')),1)
        before.pop('dialogue_choices')
        self.assertEqual(before,after)

    def test_source_isolation_reload_and_tamper_detection(self):
        with workspace_temp() as root:
            source=root/'source.mp4';source.write_bytes(b'source')
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            capture=dict(source=dict(sha256=digest,duration_ms=200),frames=[])
            (root/'capture.json').write_text(json.dumps(capture),encoding='utf-8')
            def decode(source,dest,*args):
                image=Image.new('RGB',(1920,1080),'red')
                image.paste(Image.new('RGB',(810,1080),'black'),(148,0));image.save(dest/'frame-000001.png')
                return [dict(id='frame-000001',evidence='frames/frame-000001.png',source_timestamp_ms=0,source_pts=0,time_base='1/60')]
            with patch('tracen_replay.inspect_choices.NeuralReader',FakeReader),patch('tracen_replay.inspect_choices.decode_frames',decode):
                inspect(source,root,0,100)
                inspect(source,root,0,100)
            metadata,rows=load(root,digest)
            self.assertEqual(len(rows),1)
            self.assertEqual(len(metadata['windows']),1)
            audit=verify(root,source)
            self.assertTrue(audit['evidence_integrity_verified'])
            self.assertEqual(audit['verified_observations'],1)
            self.assertEqual(audit['verified_refinements'],1)
            self.assertIn('choice-inspection.json',audit['inspection_manifest_sha256'])
            with self.assertRaisesRegex(PipelineError,'another source'):load(root,'different')
            proof=root/rows[0]['evidence'];proof.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'provenance'):load(root,digest)

    def test_out_of_bounds_inspection_is_rejected_before_decoding(self):
        with workspace_temp() as root:
            (root/'capture.json').write_text(json.dumps(dict(source=dict(duration_ms=10000))),encoding='utf-8')
            with self.assertRaisesRegex(PipelineError,'five seconds'):inspect(root/'absent.mp4',root,0,6000)
            with self.assertRaisesRegex(PipelineError,'sampling'):inspect(root/'absent.mp4',root,0,100,120)
