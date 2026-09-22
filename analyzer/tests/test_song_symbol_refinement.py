from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image,ImageDraw

from tests.test_gameplay import workspace_temp
from tracen_replay.song_symbol_refinement import prepare,apply
from tracen_replay.vision import parse


class SongSymbolRefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp=workspace_temp();self.root=self.temp.__enter__()
        fixture=Path('analyzer/tests/fixtures/song-note-letter.png')
        self.meta=json.loads(fixture.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),self.meta['crop_png_sha256'])
        self.pane=Image.new('RGB',(810,1080),'white')
        with Image.open(fixture) as cropped:self.pane.paste(cropped,self.meta['crop_box'][:2])
        self.proof=self.root/'proof.png';self.pane.save(self.proof)
        self.raw=dict(lines=[self.meta['line']],regions={},header='',current_grid=False,result_grid=False,
            source_timestamp_ms=1524500,evidence='proof.png',source_frame_sha256='a'*64,
            gameplay_sha256=hashlib.sha256(self.pane.tobytes()).hexdigest())

    def tearDown(self):
        self.temp.__exit__(None,None,None)

    def reader(self,text='Hoppity Sunny Days',confidence=.99912):
        return SimpleNamespace(models={'test':'b'*64},fingerprint='c'*64,TextRecInput=lambda **kwargs:kwargs,
            engine=SimpleNamespace(text_rec=lambda request:SimpleNamespace(txts=[text],scores=[confidence])))

    def test_recovery_requires_pixel_note_and_title_witness_and_preserves_raw(self):
        before=deepcopy(self.raw)
        extra=prepare(self.raw,self.proof,self.reader())
        self.assertEqual(len(extra['observations']),1)
        self.assertEqual(extra['observations'][0]['layout']['symbol']['box'],[680,791,690,807])
        with patch('tracen_replay.vision.NeuralReader',side_effect=AssertionError('OCR during replay')):
            fixed=apply(self.raw,extra,self.proof)
        effect=parse(fixed)['effects'][0]
        self.assertEqual(effect['name'],'Hoppity Sunny Days ♪')
        self.assertEqual(effect['original_text'],self.raw['lines'][0]['text'])
        self.assertEqual(self.raw,before)

    def test_real_letter_before_note_and_unreadable_title_abstain(self):
        for text,confidence in [('Hoppity Sunny Days D',.999),('Hoppity Sunny Day',.999),
                                ('Hoppity Sunny Days',.949),('Hoppity Sunny Days',1.1),
                                ('Hoppity Sunny Days',float('nan')),
                                ('Hoppity Sunny Days','99'),('Hoppity Sunny Days',10**1000)]:
            self.assertEqual(prepare(self.raw,self.proof,self.reader(text,confidence))['observations'],[])

    def test_extra_real_letter_is_rejected_even_when_title_ocr_omits_it(self):
        pane=self.pane.copy()
        suffix=pane.crop((530,788,565,813))
        pane.paste('white',(530,788,600,813))
        pane.paste(suffix,(552,788))
        # Copy the real D in Days, preserving this source font and antialiasing.
        pane.paste(self.pane.crop((483,788,496,811)),(535,788))
        pane.save(self.proof)
        raw=deepcopy(self.raw);raw['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        raw['lines'][0]['box'][2]+=22
        from tracen_replay.song_symbol_refinement import _layout
        self.assertIsNotNone(_layout(pane,raw['lines'][0]))
        # The model confidently drops the real trailing D. Pixel glyph coverage
        # must veto the otherwise matching text and valid note/quote geometry.
        self.assertEqual(prepare(raw,self.proof,self.reader('Hoppity Sunny Days',.9999))['observations'],[])

    def test_letter_pixels_missing_quotes_and_faint_note_abstain(self):
        for mutation in ('letter','quote','faint'):
            pane=self.pane.copy()
            if mutation=='letter':
                pane.paste('white',(532,788,543,811))
                draw=ImageDraw.Draw(pane)
                draw.line([(533,792),(533,806)],fill='black',width=2)
                draw.arc((533,792,542,806),-90,90,fill='black',width=2)
            elif mutation=='quote':pane.paste('white',(546,789,557,800))
            else:
                import numpy as np
                array=np.array(pane);region=array[788:811,532:543];region[region<160]=160
                pane=Image.fromarray(array)
            pane.save(self.proof);raw=dict(self.raw,gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())
            self.assertEqual(prepare(raw,self.proof,self.reader())['observations'],[],mutation)

    def test_tampered_cache_source_crop_and_confidence_rejected(self):
        extra=prepare(self.raw,self.proof,self.reader())
        for mutation in ('raw','proof','line','glyph','crop','title','confidence','index','duplicate'):
            raw=deepcopy(self.raw);cache=deepcopy(extra);obs=cache['observations'][0]
            if mutation=='raw':cache['raw_sha256']='z'*64
            elif mutation=='proof':cache['evidence_sha256']='z'*64
            elif mutation=='line':obs['line']['text']='Different title'
            elif mutation=='glyph':obs['layout']['symbol']['symbol']='X'
            elif mutation=='crop':obs['layout']['title_crop_box'][0]+=1
            elif mutation=='title':obs['title']='Different title'
            elif mutation=='confidence':obs['title_confidence']=999
            elif mutation=='index':obs['line_index']=True
            else:cache['observations'].append(deepcopy(obs))
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):apply(raw,cache,self.proof)
        for key,value in [('version',True),('model_sha256','not models'),
                          ('model_sha256',{'test':'not a hash'}),('engine_fingerprint',5),
                          ('observations',['not an observation'])]:
            bad=deepcopy(extra);bad[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):apply(self.raw,bad,self.proof)

    def test_malformed_receipt_geometry_abstains(self):
        for box in (None,[],[304,781,712],[304,781,'712',819],[True,781,712,819],
                    [304,781,float('nan'),819],[304,781,1200,819],[712,781,304,819]):
            raw=deepcopy(self.raw);raw['lines'][0]['box']=box
            self.assertEqual(prepare(raw,self.proof,self.reader())['observations'],[])


if __name__=='__main__':unittest.main()
