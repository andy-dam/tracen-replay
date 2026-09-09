"""Persist explicit triage decisions without converting them into source-recall claims."""
import argparse
import hashlib
import json
from pathlib import Path


STATUSES={'recovered','confirmed_issue','unobservable'}


def finding_hash(finding):
    value={k:finding[k] for k in ('reason','start_ms','end_ms','evidence','details')}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def proof(root,relative):
    root=Path(root).resolve();path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('Review proof missing or outside run directory.')
    return dict(evidence=path.relative_to(root).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def decision(queue,finding_id,status,rationale,evidence,root):
    if status not in STATUSES or not rationale.strip():raise ValueError('Explicit status and rationale required.')
    if not evidence:raise ValueError('Supporting source proof required.')
    matches=[f for f in queue['findings'] if f['id']==finding_id]
    if len(matches)!=1:raise ValueError('Finding missing or ambiguous.')
    f=matches[0]
    return dict(source_sha256=queue['source_sha256'],report_sha256=queue['report_sha256'],
        finding_sha256=finding_hash(f),finding_id=finding_id,status=status,rationale=rationale,
        finding_proofs=[proof(root,p) for p in dict.fromkeys(f['evidence'])],
        supporting_proofs=[proof(root,p) for p in dict.fromkeys(evidence)],
        basis='explicit_review',semantic_correctness_automatically_verified=False)


def apply(queue,decisions,root):
    by_hash={}
    for item in decisions:by_hash.setdefault(item['finding_sha256'],[]).append(item)
    for f in queue['findings']:
        f['disposition']='unreviewed';f.pop('review_decision',None);f.pop('review_error',None)
        entries=by_hash.get(finding_hash(f),[])
        if not entries:continue
        if len(entries)!=1:
            f['disposition']='stale';f['review_error']='Ambiguous saved decisions';continue
        d=entries[0];errors=[]
        if d.get('source_sha256')!=queue['source_sha256'] or d.get('report_sha256')!=queue['report_sha256']:
            errors.append('Report/source changed')
        if d.get('status') not in STATUSES or not d.get('rationale','').strip() or not d.get('supporting_proofs'):
            errors.append('Incomplete decision')
        if {x['evidence'] for x in d.get('finding_proofs',[])}!={Path(p).as_posix() for p in f['evidence']}:
            errors.append('Finding proof list changed')
        for p in d.get('finding_proofs',[])+d.get('supporting_proofs',[]):
            try:
                if proof(root,p['evidence'])['sha256']!=p['sha256']:errors.append('Proof changed')
            except ValueError:errors.append('Proof missing or outside run directory')
        if errors:f['disposition']='stale';f['review_error']='; '.join(sorted(set(errors)))
        else:f['disposition']=d['status'];f['review_decision']=d
    # Recompute the union from individual pending findings. A recovered bridge must not
    # retain the entire old merged window or swallow an adjacent unresolved finding.
    pending=[];pad=queue.get('context_ms',1500)
    for f in sorted(queue['findings'],key=lambda f:(f['start_ms'],f['end_ms'])):
        if f['disposition']=='recovered':continue
        start=max(0,f['start_ms']-pad);end=min(queue['source_duration_ms'],f['end_ms']+pad)
        if pending and start<=pending[-1]['end_ms']:
            pending[-1]['end_ms']=max(end,pending[-1]['end_ms']);pending[-1]['finding_ids'].append(f['id'])
        else:pending.append(dict(start_ms=start,end_ms=end,finding_ids=[f['id']]))
    queue['pending_triage_windows']=pending
    queue['summary'].update(recovered_findings=sum(f['disposition']=='recovered' for f in queue['findings']),
        stale_decisions=sum(f['disposition']=='stale' for f in queue['findings']),
        pending_findings=sum(f['disposition']!='recovered' for f in queue['findings']),
        pending_windows=len(pending),pending_footage_ms=sum(w['end_ms']-w['start_ms'] for w in pending))
    return queue


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    p.add_argument('--finding',required=True);p.add_argument('--status',choices=sorted(STATUSES),required=True)
    p.add_argument('--rationale',required=True);p.add_argument('--evidence',nargs='+',required=True)
    args=p.parse_args();root=args.root;q=json.loads((root/'review-queue.json').read_text(encoding='utf-8'))
    if hashlib.sha256((root/'report.json').read_bytes()).hexdigest()!=q['report_sha256']:
        raise ValueError('Regenerate queue for current report before reviewing.')
    d=decision(q,args.finding,args.status,args.rationale,args.evidence,root)
    path=root/'review-decisions.json';items=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if any(x['finding_sha256']==d['finding_sha256'] for x in items):
        raise ValueError('Decision already exists; preserve history and explicitly adjudicate replacements.')
    path.write_text(json.dumps(items+[d],indent=2),encoding='utf-8')
    print(json.dumps(dict(saved=args.finding,status=args.status,refresh_queue_required=True)))


if __name__=='__main__':main()
