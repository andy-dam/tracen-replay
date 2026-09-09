import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import cached_readings
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.song_symbols import _eligible, apply, music_note_suffix
from tracen_replay.vision import parse


class SongSymbolsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads(Path('tests/fixtures/song-note-pixels.json').read_text(encoding='utf-8'))
        self.pane=Image.new('RGB',(810,1080),'white')
        self.pane.paste(Image.fromarray(np.array(self.fixture['gray_rows'],dtype='uint8')).convert('RGB'),(452,788))
        self.line=self.fixture['line']

    def test_source_note_requires_both_closing_quotes(self):
        observed=music_note_suffix(self.pane,self.line['box'])
        self.assertEqual(observed['symbol'],'\u266a')
        self.assertEqual(observed['box'],[624,792,634,807])
        self.assertFalse(observed['independent_observations'])
        self.pane.paste('white',(639-148,788,647-148,810))
        self.assertIsNone(music_note_suffix(self.pane,self.line['box']))

    def test_letter_suffixes_and_outside_receipts_abstain(self):
        for suffix in ('D','J','h','f','\u266a'):
            self.assertFalse(_eligible(dict(self.line,text=f'Learned the song "Example {suffix}".')))
        self.assertFalse(_eligible(dict(self.line,confidence=94.9)))
        self.assertIsNone(music_note_suffix(self.pane,[307,700,653,730]))
        # A visible h cannot substitute for the central-stem note, even with quotes.
        self.pane.paste('white',(624-148,788,634-148,810))
        from PIL import ImageDraw
        draw=ImageDraw.Draw(self.pane)
        draw.line([(476,792),(476,807)],fill='black',width=2)
        draw.line([(476,800),(483,800),(483,807)],fill='black',width=2)
        self.assertIsNone(music_note_suffix(self.pane,self.line['box']))

    def test_faint_or_missing_note_abstains(self):
        self.assertIsNone(music_note_suffix(Image.new('RGB',(810,1080),'white'),self.line['box']))
        # A glyph visible only in permissive threshold variants is insufficient.
        values=np.array(self.pane)
        values[values<160]=160
        self.assertIsNone(music_note_suffix(Image.fromarray(values),self.line['box']))

    def test_cached_parser_preserves_raw_text_and_rejects_changed_proof(self):
        with workspace_temp() as root:
            (root/'neural').mkdir();(root/'song-symbols').mkdir()
            self.pane.save(root/'proof.png')
            source=Image.new('RGB',(1920,1080),'black');source.paste(self.pane,(148,0));source.save(root/'source.png')
            raw=dict(lines=[self.line],regions={},header='',current_grid=False,result_grid=False,
                     source_timestamp_ms=1222250,evidence='proof.png',model_sha256={'model':'test'},engine_fingerprint='test',
                     source_frame_sha256=hashlib.sha256((root/'source.png').read_bytes()).hexdigest())
            before=copy.deepcopy(raw)
            extra=dict(version=1,raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256((root/'proof.png').read_bytes()).hexdigest(),
                       observations=[dict(line_index=0,line_box=self.line['box'],symbol=music_note_suffix(self.pane,self.line['box']))])
            corrected=apply(raw,extra,root/'proof.png')
            effect=parse(corrected)['effects'][0]
            self.assertEqual(effect['name'],'Present March\u266a')
            self.assertEqual(effect['original_text'],self.line['text'])
            self.assertEqual(raw,before)
            (root/'neural/one.json').write_text(json.dumps(raw),encoding='utf-8')
            (root/'song-symbols/one.json').write_text(json.dumps(extra),encoding='utf-8')
            report=dict(frames=[dict(id='one',evidence='source.png',source_timestamp_ms=1222250)])
            with patch('tracen_replay.receipt_occlusion.annotate_path',side_effect=lambda raw,*args,**kwargs:raw):
                self.assertEqual(cached_readings(report,root)[0]['effects'][0]['name'],'Present March\u266a')
            with self.assertRaisesRegex(ValueError,'provenance'):
                apply(dict(raw,header='changed'),extra,root/'proof.png')
            (root/'proof.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'provenance'):apply(raw,extra,root/'proof.png')


if __name__=='__main__':unittest.main()
