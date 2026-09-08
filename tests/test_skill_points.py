import hashlib
import json
import unittest
from unittest.mock import patch
from PIL import Image
from tests.test_gameplay import workspace_temp
from tests.test_full_recording import FakeReader
from tests.test_neural_transactions import raw,line
from tracen_replay.full_recording import analyze_frames,cached_readings
from tracen_replay.pipeline import PipelineError
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.refine_skill_points import counter_reading
from tracen_replay.vision import parse


class SkillPointRefinementTests(unittest.TestCase):
    def test_finish_confirmation_reports_balances_not_spending(self):
        lines=[line('Finish this Career playthrough?',(406,511,706,543)),
               line('Remaining Skill Points',(417,563,581,587)),line('8 pt(s)',(595,560,669,593)),
               line('Remaining Performance Points',(421,604,684,629)),
               line('9',(363,640,388,671)),line('18',(450,638,495,674)),line('2',(569,639,599,672)),
               line('18',(657,639,701,672)),line('118',(738,639,804,672))]
        result=parse(raw(lines))
        self.assertEqual(result['facts']['current_skill_points'],8)
        self.assertEqual(result['facts']['remaining_performance_points'],
                         {'dance':9,'passion':18,'vocal':2,'visual':18,'composure':118})
        self.assertEqual(result['effects'],[])
        self.assertIsNone(result.get('completed_action'))
        self.assertNotIn('current_skill_points',parse(raw([lines[0]]+lines[2:]))['facts'])
        lines.append(line('9 pt(s)',(595,560,669,593)))
        self.assertIsNone(parse(raw(lines))['facts']['current_skill_points'])

    def test_small_counter_and_zero_need_matching_views(self):
        for value in ('8','0','1242'):
            self.assertEqual(counter_reading([line(value),line(value)]),(int(value),[]))
        self.assertEqual(counter_reading([line('8')]),(None,[]))
        self.assertEqual(counter_reading([line('8'),line('8',confidence=90)]),(None,[]))
        self.assertEqual(counter_reading([line('8?'),line('8?')]),(None,[]))

    def test_disagreement_does_not_choose_a_convenient_balance(self):
        self.assertEqual(counter_reading([line('8'),line('3')]),(None,[3,8]))
        self.assertEqual(counter_reading([line('8'),line('8')],3),(None,[3,8]))

    def test_counter_remains_projected_and_does_not_override_receipt(self):
        sample=raw([line('Skill Points',(525,339,620,366))],
                   skill_point_refinement=[line('8'),line('8')])
        sample['header']='Learn'
        facts=parse(sample)['facts']
        self.assertEqual(facts['displayed_skill_points'],8)
        self.assertIsNone(facts['spent_skill_points'])
        sample['lines'].append(line('Skills Learned'))
        self.assertNotIn('displayed_skill_points',parse(sample)['facts'])

    def test_parser_exposes_conflicting_counter_values(self):
        sample=raw([line('Skill Points',(525,339,620,366)),line('3',(750,338,770,366))],
                   skill_point_refinement=[line('8'),line('8')])
        sample['header']='Learn'
        facts=parse(sample)['facts']
        self.assertNotIn('displayed_skill_points',facts)
        self.assertEqual(facts['skill_point_conflict'],[3,8])

    def test_cache_rejects_refinement_for_another_frame_or_observation(self):
        with workspace_temp() as root:
            Image.new('RGB',(1920,1080),'white').save(root/'frame.png')
            report={'frames':[dict(id='one',evidence='frame.png',source_timestamp_ms=0)]}
            with patch('tracen_replay.full_recording.NeuralReader',FakeReader):
                analyze_frames(report,root,workers=1)
            original=json.loads((root/'neural/one.json').read_text(encoding='utf-8'))
            extra=dict(raw_sha256=fingerprint(original),evidence_sha256=hashlib.sha256((root/'gameplay/one.png').read_bytes()).hexdigest(),
                       views=[line('8'),line('8')])
            dest=root/'skill-points-refinement';dest.mkdir()
            (dest/'one.json').write_text(json.dumps(extra),encoding='utf-8')
            self.assertEqual(len(cached_readings(report,root)),1)
            for key in ('raw_sha256','evidence_sha256'):
                with self.subTest(key=key):
                    (dest/'one.json').write_text(json.dumps(dict(extra,**{key:'changed'})),encoding='utf-8')
                    with self.assertRaisesRegex(PipelineError,'Skill-point refinement evidence changed'):
                        cached_readings(report,root)
