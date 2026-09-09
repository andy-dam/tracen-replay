"""A receipt spelling variant must not count as a second hint award."""
import unittest
from tracen_replay.receipt_names import collapse_punctuated_hint_variants


def sample():
    weak=dict(kind='skill_hint_change',name='An Invented Skill',amount=2,
              raw_text='Gained 2 hint level(s) for An Invented Skill!')
    strong=dict(weak,name=weak['name']+'!',raw_text=weak['raw_text']+'.')
    rows={}
    evidence={}
    for i,effect in enumerate([weak,weak,strong,strong]):
        proof=f'frame-{i}'
        lines=[dict(text=effect['raw_text'],confidence=99,box=[100,890,700,920])]
        if i<2:lines.append(dict(text=strong['name'],confidence=99,box=[200,670,600,700]))
        rows[proof]=dict(source_timestamp_ms=i*250,ocr=dict(neural=lines))
        evidence.setdefault('skill_hint_change||'+effect['name'],[]).append(proof)
    return dict(effects=[weak,strong],field_evidence=evidence,conflicting_readings=[]),rows


class HintReceiptIdentityTests(unittest.TestCase):
    def test_repeated_label_resolves_one_award_and_retains_alternates(self):
        event,rows=sample()
        collapse_punctuated_hint_variants(event,rows)
        self.assertEqual(len(event['effects']),1)
        effect=event['effects'][0]
        self.assertEqual((effect['name'],effect['amount']),('An Invented Skill!',2))
        self.assertEqual(effect['name_label_evidence'],['frame-0','frame-1'])
        self.assertEqual(effect['alternate_name_evidence'][0]['evidence'],['frame-0','frame-1'])
        self.assertEqual(event['field_evidence']['skill_hint_change||An Invented Skill!'],['frame-2','frame-3'])

    def test_insufficient_or_conflicting_evidence_does_not_merge(self):
        for case in ('one_label','low_confidence','wrong_label','wrong_location',
                     'gap','one_complete','different_amount','conflict','simultaneous'):
            with self.subTest(case=case):
                event,rows=sample()
                if case=='one_label':rows['frame-0']['ocr']['neural'].pop()
                if case=='low_confidence':rows['frame-0']['ocr']['neural'][1]['confidence']=94
                if case=='wrong_label':rows['frame-0']['ocr']['neural'][1]['text']='Other Skill!'
                if case=='wrong_location':rows['frame-0']['ocr']['neural'][1]['box']=[200,800,600,830]
                if case=='gap':rows['frame-3']['source_timestamp_ms']=1250
                if case=='one_complete':del rows['frame-3']
                if case=='different_amount':event['effects'][1]['amount']=3
                if case=='conflict':event['conflicting_readings']=[dict(field='skill_hint_change||An Invented Skill!')]
                if case=='simultaneous':rows['frame-2']['source_timestamp_ms']=250
                collapse_punctuated_hint_variants(event,rows)
                self.assertEqual(len(event['effects']),2)


if __name__=='__main__':unittest.main()
