import copy
import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_preview_recovery import _raw, _FakeReader
from tracen_replay.preview_recovery import recover_in_memory
from tracen_replay.preview_observations import build_preview_observations, parse_preview_overlay
from tracen_replay.vision import parse, training_preview
from tracen_replay.transactions import training_events


class PreviewRecoveryPipelineTests(unittest.TestCase):
    def refined(self, *, marker=False):
        raw = _raw(header='', marker=marker, failure=False)
        return recover_in_memory(raw, Image.new('RGB',(810,1080)),
            reader=_FakeReader({'speed':'+5','stamina':'+7'}, {'speed':'+2'}))

    def test_normal_parser_emits_recovered_previews_without_applied_gains(self):
        raw=self.refined(marker=True)
        row=parse(raw)
        row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence'])
        observations=build_preview_observations([row])['observations']
        payloads=[o['payload'] for o in observations]
        self.assertIn(dict(kind='stat_change',field='speed',amount=5),payloads)
        self.assertIn(dict(kind='stat_change',field='stamina',amount=7),payloads)
        self.assertIn(dict(kind='training_modifier_change',field='speed',amount=2,
                           modifier='song',timing='training preview'),payloads)
        self.assertTrue(all(o['phase']=='preview' for o in observations))
        self.assertEqual(training_events([row]),[])

    def test_tampered_recovery_amount_is_not_accepted(self):
        raw=self.refined()
        raw['preview_recovery']['effects'][0]['amount']=999
        result=parse_preview_overlay(raw)
        self.assertFalse(any(e['amount']==999 for e in result['preview_overlay_effects']))

    def test_unverified_phase_cannot_turn_result_into_preview(self):
        raw=_raw(header='Training',failure=False)
        raw['current_grid']=False
        raw['result_grid']=True
        raw['preview_recovery']=dict(phase=dict(menu_proven=True,result_proven=False))
        self.assertFalse(training_preview(raw))

    def test_conflicting_ordinary_and_recovered_field_remains_unknown(self):
        raw=self.refined()
        existing=dict(preview_option='speed',preview_overlay_effects=[
            dict(kind='stat_change',field='speed',amount=9)],
            preview_modifier_effects=[],rejected_counts={})
        with patch('tracen_replay.preview_observations._parse_preview_overlay',return_value=copy.deepcopy(existing)):
            result=parse_preview_overlay(raw)
        self.assertFalse(any(e['field']=='speed' for e in result['preview_overlay_effects']))
        self.assertTrue(any(e['field']=='stamina' for e in result['preview_overlay_effects']))
        self.assertEqual(result['rejected_counts']['recovery_field_conflict'],1)


if __name__=='__main__':
    unittest.main()
