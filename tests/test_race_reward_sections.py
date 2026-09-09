import copy
import unittest
from tracen_replay.race_reward_sections import annotate
from tracen_replay.transactions import races


class RewardSectionTests(unittest.TestCase):
    def row(self, time=0):
        return dict(screen='race_result',source_timestamp_ms=time,evidence=str(time),
            facts=dict(fans=1000,fans_gained=500,visible_item_quantities=[
                dict(quantity=1,box=[320,695,350,727]),
                dict(quantity=600,box=[420,695,490,727]),
                dict(quantity=600,box=[420,860,490,890])]),
            ocr=dict(neural=[dict(text='Items',confidence=99,box=[278,590,330,612]),
                             dict(text='Bonus',confidence=99,box=[270,750,334,778])]))

    def test_repeated_quantity_retains_section_and_source_header(self):
        row=self.row(); original=copy.deepcopy(row)
        result=annotate(row)
        self.assertEqual([x['section'] for x in result],['items','items','bonus'])
        self.assertEqual(result[-1]['section_header']['text'],'Bonus')
        self.assertEqual(row,original)
        snapshot=races([row,self.row(250)])[0]['visible_item_reward_snapshots'][0]
        self.assertEqual([x['section'] for x in snapshot['items']],['items','items','bonus'])
        self.assertFalse(snapshot['identity_verified'])
        self.assertFalse(snapshot['list_complete'])

    def test_missing_bonus_header_does_not_make_bonus_an_item(self):
        row=self.row(); row['ocr']['neural'].pop()
        self.assertEqual([x['section'] for x in annotate(row)],['items','items',None])

    def test_shifted_layout_is_relative_to_visible_headers(self):
        row=self.row(); row['facts']['visible_item_quantities']=row['facts']['visible_item_quantities'][:2]
        row['ocr']['neural']=row['ocr']['neural'][:1]
        for entry in row['facts']['visible_item_quantities']+row['ocr']['neural']:
            entry['box'][1]+=130; entry['box'][3]+=130
        self.assertEqual([x['section'] for x in annotate(row)],['items','items'])

    def test_duplicate_weak_and_out_of_order_headers_abstain(self):
        for case in ('duplicate','weak','reversed'):
            with self.subTest(case=case):
                row=self.row()
                if case=='duplicate': row['ocr']['neural'].append(copy.deepcopy(row['ocr']['neural'][0]))
                if case=='weak':
                    for h in row['ocr']['neural']: h['confidence']=96
                if case=='reversed':
                    row['ocr']['neural'][0]['text']='Bonus'; row['ocr']['neural'][1]['text']='Items'
                self.assertTrue(all(x['section'] is None for x in annotate(row)))

    def test_changed_section_evidence_splits_stability_group(self):
        a,b=self.row(),self.row(250)
        b['ocr']['neural']=[]
        self.assertEqual(races([a,b])[0]['visible_item_reward_snapshots'],[])

    def test_multiple_quantity_rows_do_not_certify_one_section(self):
        row=self.row()
        row['facts']['visible_item_quantities'][1]['box']=[420,735,490,749]
        result=annotate(row)
        self.assertIsNone(result[0]['section'])
        self.assertIsNone(result[1]['section'])


if __name__=='__main__':
    unittest.main()
