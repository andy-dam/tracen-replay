"""Locate sparse source-label intervals for review without reading predictions.

Silence can be legitimate (dialogue, races, menus); a flagged bin is a review
prompt, not proof of missing recognition or incomplete visual inspection.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from baseline_status import SECTIONS


def inspect(root):
    rows=[]
    for name,start,end in SECTIONS:
        path=root/name/'labels.json'
        if not path.exists():continue
        source=json.loads(path.read_text(encoding='utf-8'))
        for left in range(start,end,30000):
            right=min(left+30000,end)
            hits=[x for x in source['labels'] if left<=x['first_seen_ms']<right]
            mechanical=[x for x in hits if x['category'] in ['action','state','purchase'] or
                        x['category']=='effect' and x['expected'].get('kind') not in
                        ['other_observed_mechanical_outcome','scenario_outcome']]
            rows.append({'section':name,'start_ms':left,'end_ms':right,
                         'label_counts':dict(Counter(x['category'] for x in hits)),
                         'mechanical_parent_rows':len(mechanical),
                         'label_ids':[x['id'] for x in hits],
                         'review_sparse_interval':not mechanical})
    return {'purpose':'Source reference coverage review prompts, not analyzer accuracy or a completeness gate.',
            'rows':rows,'sparse_intervals':[x for x in rows if x['review_sparse_interval']]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('review_dir',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args();out=inspect(a.review_dir)
    if a.output:a.output.write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out['sparse_intervals']))


if __name__=='__main__':main()
