"""Build whole-recording triage and a separate source-review sweep. Never certifies recall."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
from urllib.parse import quote


def reviewed_intervals_for_sweep(coverage,max_interval_ms=250):
    """Only sufficiently dense declared reviews retire source-sweep work."""
    if type(max_interval_ms) is not int or max_interval_ms<=0:
        raise ValueError('Invalid required review interval.')
    return [(r['start_ms'],r['end_ms']) for r in coverage['references']
            if r['sample_interval_ms']<=max_interval_ms]


def build(report, reviewed_intervals=(), context_ms=1500, sweep_ms=120000):
    data=report['gameplay_tracking'];duration=report['source']['duration_ms']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Gameplay-only report required.')
    if duration<=0 or context_ms<0 or sweep_ms<=0:raise ValueError('Invalid review dimensions.')
    findings=[]
    readings=sorted(data.get('readings',[]),key=lambda r:r['source_timestamp_ms'])
    def add(reason,row,details=None):
        start=row.get('start_ms',row.get('first_seen_ms',row.get('source_timestamp_ms',0)))
        end=row.get('end_ms',row.get('last_seen_ms',start))
        evidence=row.get('evidence',[])
        if isinstance(evidence,str):evidence=[evidence]
        if not evidence and readings:
            evidence=[min(readings,key=lambda r:abs(r['source_timestamp_ms']-t))['evidence'] for t in (start,end)]
        findings.append(dict(id=f'finding-{len(findings)+1:05d}',reason=reason,
            start_ms=max(0,min(start,duration)),end_ms=max(0,min(max(start,end),duration)),
            evidence=evidence,details=details,disposition='unreviewed'))
    for name,rows in [('stats',data.get('intervals',[])),
                      ('performance',data.get('performance_accounting',{}).get('intervals',[])),
                      ('fans',data.get('fan_accounting',{}).get('intervals',[]))]:
        for row in rows:
            residual=row.get('unexplained_change')
            nonzero=any(residual.values()) if isinstance(residual,dict) else bool(residual)
            if nonzero or row.get('status')=='unresolved':add(name+'_discrepancy',row,residual)
    calendar=report.get('verification',{}).get('calendar_action_coverage',{})
    for row in calendar.get('windows',[]):
        if row.get('status')!='one_action':add('calendar_action_coverage',row,row.get('status'))
    for row in data.get('events',[]):
        if row.get('conflicting_readings'):add('conflicting_readings',row,row['conflicting_readings'])
    for row in data.get('readings',[]):
        lines=row.get('facts',{}).get('occluded_receipt_lines',[])
        if lines:add('obscured_receipt',row,[dict(text=x['text'],recipient_name_occluded=x.get('recipient_name_occluded',False)) for x in lines])
    for row in data.get('unparsed_receipt_candidates',[]):add('unparsed_receipt',row,row.get('raw_text'))
    for row in data.get('lesson_purchases',[]):
        if row.get('performance_cost') is None:add('missing_lesson_cost',row)
    for row in data.get('skill_purchases',[]):
        if row.get('spent_skill_points') is None:add('missing_skill_charge',row)
        if not row.get('bundle_charge_assignment_complete',False):add('incomplete_skill_assignment',row)
    for row in data.get('races',[]):
        missing=[k for k in ('race_name','placing','fans','fans_gained','course') if row.get(k) is None]
        if missing:add('missing_race_fields',row,missing)
        if not row.get('item_rewards_complete',False):add('unverified_race_items',row,
            dict(visible_item_reward_snapshots=row.get('visible_item_reward_snapshots',[]),
                 item_identity_verified=False,list_complete=False))
    for row in data.get('concerts',[]):
        if not row.get('all_bonus_totals_verified',False):add('unverified_active_bonuses',row)
    for row in data.get('turn_action_receipts',[]):
        if not row.get('evidence'):add('action_without_evidence',row)
    verification=report.get('verification',{})
    for key in ('source_coverage_errors','missing_base_timestamps_ms'):
        for value in verification.get(key,[]):
            row=dict(source_timestamp_ms=value) if type(value) is int else dict(start_ms=0,end_ms=duration)
            add(key,row,value)
    inventory=data.get('owned_skill_inventory',{})
    frames=inventory.get('summary_frames',[])
    if frames and not inventory.get('complete',False):
        add('incomplete_owned_inventory',dict(
            start_ms=min(f['timestamp_ms'] for f in frames),
            end_ms=max(f['timestamp_ms'] for f in frames),
            evidence=list(dict.fromkeys(f['evidence'] for f in frames))),
            dict(scope=inventory.get('scope'),unresolved=inventory.get('unresolved',[]),
                 visible_cards=[dict(name=c['name_text'],level=c.get('level'),
                    level_verified=c.get('level_verified',False),variant=c.get('variant'),
                    variant_verified=c.get('variant_verified',False))
                    for c in inventory.get('observed_owned_cards',[])],
                 missing_detail_is_not_confirmed_absence=True))
    for row in readings:
        if conflicts:=row.get('facts',{}).get('owned_skill_panel_conflicts'):
            add('owned_skill_name_conflict',row,conflicts)
    for card in inventory.get('observed_owned_cards',[]):
        conflicts={f:card[f+'_conflicts'] for f in ('level','variant') if card.get(f+'_conflicts')}
        observations=card.get('observations',[])
        if conflicts and observations:
            add('owned_skill_detail_conflict',dict(
                start_ms=min(o['timestamp_ms'] for o in observations),
                end_ms=max(o['timestamp_ms'] for o in observations),
                evidence=list(dict.fromkeys(o['evidence'] for o in observations))),
                dict(name=card['name_text'],conflicts=conflicts))
    # Merge overlapping footage, not findings. Repeated OCR observations stay inspectable.
    windows=[]
    for f in sorted(findings,key=lambda f:(f['start_ms'],f['end_ms'],f['id'])):
        start=max(0,f['start_ms']-context_ms);end=min(duration,f['end_ms']+context_ms)
        if windows and start<=windows[-1]['end_ms']:
            windows[-1]['end_ms']=max(windows[-1]['end_ms'],end);windows[-1]['finding_ids'].append(f['id'])
        else:windows.append(dict(start_ms=start,end_ms=end,finding_ids=[f['id']]))
    # Source sweep depends solely on source duration and explicitly supplied reviewed intervals.
    merged=[]
    for start,end in sorted(reviewed_intervals):
        if not 0<=start<end<=duration:raise ValueError('Review interval outside source.')
        if merged and start<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],end)
        else:merged.append([start,end])
    gaps=[];cursor=0
    for start,end in merged+[[duration,duration]]:
        if cursor<start:gaps.append([cursor,start])
        cursor=end
    sweep=[]
    for start,end in gaps:
        for t in range(start,end,sweep_ms):sweep.append(dict(start_ms=t,end_ms=min(t+sweep_ms,end),reviewed=False))
    return dict(source_sha256=report['source']['sha256'],source_duration_ms=duration,context_ms=context_ms,
        findings=findings,triage_windows=windows,source_sweep=sweep,
        summary=dict(findings=len(findings),findings_by_reason=dict(Counter(f['reason'] for f in findings)),
            triage_windows=len(windows),triage_footage_ms=sum(w['end_ms']-w['start_ms'] for w in windows),
            source_sweep_windows=len(sweep),unreviewed_source_ms=sum(b-a for a,b in gaps)),
        global_gaps=['Full action/effect recall is unmeasured.',
                     'Complete owned inventory remains unverified.' if not data.get('owned_skill_inventory',{}).get('complete',False) else 'Inventory completeness needs source adjudication.'],
        scope='Triage candidates are not asserted errors: another observation may already recover a receipt. No findings are silently dismissed. Source sweep is independent of predictions. Footage duration is not human review time; overlap between sweep and triage is not additive.',
        fully_verified=False,go_ready=False)


def render(queue,root):
    root=Path(root).resolve();parts=['<!doctype html><meta charset="utf-8"><title>Recording review queue</title>',
        '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}article{border-top:1px solid #bbb;padding:16px 0}pre{white-space:pre-wrap}a{margin-right:12px}</style>',
        '<h1>Recording review queue</h1><p>'+html.escape(queue['scope'])+'</p>',
        '<pre>'+html.escape(json.dumps(queue['summary'],indent=2))+'</pre>',
        '<h2>Source-selected review sweep</h2><p>These larger contiguous blocks are unreviewed, not automatic passes.</p><ul>']
    stamp=lambda t:f'{t/1000:.3f}s'
    for w in queue['source_sweep']:parts.append(f'<li>{stamp(w["start_ms"])}–{stamp(w["end_ms"])}</li>')
    parts.append('</ul><h2>Targeted triage</h2>');by_id={f['id']:f for f in queue['findings']}
    for w in queue.get('pending_triage_windows',queue['triage_windows']):
        parts.append(f'<article><h3>{stamp(w["start_ms"])}–{stamp(w["end_ms"])}</h3>')
        for fid in w['finding_ids']:
            f=by_id[fid];parts.append('<details><summary>'+html.escape(fid+' '+f['reason']+' ['+f['disposition']+']')+'</summary><pre>'+html.escape(json.dumps(f['details'],ensure_ascii=False))+'</pre>')
            if f.get('review_error'):parts.append('<p>'+html.escape(f['review_error'])+'</p>')
            links=list(f['evidence'])
            if suggestion:=f.get('recovery_suggestion'):
                parts.append('<p>Possible alternate evidence; visual review required.</p><pre>'+html.escape(json.dumps(suggestion,ensure_ascii=False))+'</pre>')
                links.extend(proof['path'] for match in suggestion['matches'] for proof in match['evidence'])
            for relative in dict.fromkeys(links):
                path=(root/relative).resolve()
                if path.is_relative_to(root) and path.is_file():
                    parts.append('<a target="_blank" href="'+html.escape(quote(path.relative_to(root).as_posix(),safe='/'))+'">'+html.escape(relative)+'</a>')
            parts.append('</details>')
        parts.append('</article>')
    parts.append('<h2>Saved recoveries</h2><p>Explicit review decisions; not automatic semantic verification.</p>')
    for f in queue['findings']:
        if f['disposition']=='recovered':parts.append('<p>'+html.escape(f['id']+': '+f['review_decision']['rationale'])+'</p>')
    return '\n'.join(parts)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--coverage',type=Path);parser.add_argument('--sweep-seconds',type=int,default=120)
    args=parser.parse_args();root=args.root;raw=(root/'report.json').read_bytes();report=json.loads(raw)
    intervals=[]
    if args.coverage:
        from .review_coverage import coverage
        old=json.loads(args.coverage.read_text(encoding='utf-8'))
        if old['source_sha256']!=report['source']['sha256']:raise ValueError('Coverage source mismatch.')
        fresh=coverage(json.loads((root/'capture.json').read_text(encoding='utf-8')),[x['path'] for x in old['references']],root)
        intervals=reviewed_intervals_for_sweep(fresh)
    from .recording_verification import audit
    report['verification']=audit(report)
    queue=build(report,intervals,sweep_ms=args.sweep_seconds*1000)
    queue['source_sweep_review_policy']=dict(maximum_sample_interval_ms=250,
        scope='Coarser reviews remain useful evidence but do not retire dense source-review windows. Sampled review does not certify native-frame or full-mechanics recall.')
    queue['report_sha256']=hashlib.sha256(raw).hexdigest()
    from .review_decisions import apply
    path=root/'review-decisions.json'
    apply(queue,json.loads(path.read_text(encoding='utf-8')) if path.exists() else [],root)
    from .review_suggestions import attach
    attach(queue,report)
    (root/'review-queue.json').write_text(json.dumps(queue,indent=2),encoding='utf-8')
    (root/'review-queue.html').write_text(render(queue,root),encoding='utf-8')
    print(json.dumps(queue['summary']))


if __name__=='__main__':main()
