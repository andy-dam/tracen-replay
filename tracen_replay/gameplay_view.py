"""HTML presentation of gameplay-only observations and unresolved accounting."""
from html import escape
import json
from .reconcile import FIELDS


def render_gameplay(report):
    data = report.get('gameplay_tracking')
    if not data:
        return ''
    def proof(path, label='Gameplay screenshot'):
        if isinstance(path,list):return ' · '.join(proof(p,label) for p in path)
        return f'<a href="{escape(path,quote=True)}">{escape(label)}</a>'
    screens = []
    for row in data['screens']:
        if row['screen']=='unknown':
            continue
        seconds=row['first_seen_ms']/1000
        screens.append(f'<tr><td>{seconds:.3f}s</td><td>{escape(row["screen"].replace("_", " "))}</td><td>{escape(row.get("training_option") or "—")}</td><td>{proof(row["evidence"])}</td></tr>')
    intervals = []
    for row in data['intervals']:
        residual = {k:v for k,v in row['unexplained_change'].items() if v}
        detail = dict(observed=row['observed_change'],supported=row['supported_change'],unexplained=row['unexplained_change'])
        evidence = ' · '.join(proof(e['evidence']) for e in row['events'])
        intervals.append(f'<details><summary>{row["start_ms"]/1000:.3f}–{row["end_ms"]/1000:.3f}s: {escape(row["status"])}; unexplained {escape(str(residual))}</summary><pre>{escape(json.dumps(detail,indent=2))}</pre>{evidence}</details>')
    checkpoints = []
    for row in data['checkpoints']:
        values = ''.join(f'<td>{row["values"][field]}</td>' for field in FIELDS)
        checkpoints.append(f'<tr><td>{row["first_seen_ms"]/1000:.3f}s</td>{values}<td>{proof(row["evidence"],"View")}</td></tr>')
    facts = []
    for row in data['readings']:
        if row['effects'] or row['facts']:
            facts.append(f'<details><summary>{row["source_timestamp_ms"]/1000:.3f}s · {escape(row["screen"])} · observed fields</summary>{proof(row["evidence"])}<pre>{escape(json.dumps(dict(effects=row["effects"],facts=row["facts"]),indent=2))}</pre></details>')
    purchases = []
    for row in data.get('lesson_purchases',[]):
        cost = ', '.join(f'{value} {name}' for name,value in (row['performance_cost'] or {}).items() if value) or 'Unresolved'
        evidence = ' · '.join(proof(path,f'Evidence {i+1}') for i,path in enumerate(row['evidence']))
        purchases.append(f'<li>{escape(row["name"])} · cost: {escape(cost)} ({escape(row.get("cost_basis","observed debit"))}) · {evidence}. Observed awards: {escape(str(row.get("awarded_stats")))}.</li>')
    heading = '<h2>Gameplay-only analysis</h2><p>Only the gameplay crop is read. A confirmation is a request; a preview is not an award. Unknown fields remain unknown. Screen spans are observations, not action counts.</p>'
    accounting = '<h3>Observed stat checkpoints</h3><table><tr><th>Source time</th><th>Speed</th><th>Stamina</th><th>Power</th><th>Guts</th><th>Wit</th><th>Skill points</th><th>Evidence</th></tr>'+''.join(checkpoints)+'</table><h3>Changes between checkpoints</h3><p>Even a balanced interval requires review of event identity and coverage.</p>'+(''.join(intervals) or '<p>No complete checkpoint pair was observed in this clip.</p>')
    screen_table = '<h3>Observed screens</h3><table><tr><th>Source time</th><th>Screen</th><th>Training result option</th><th>Evidence</th></tr>'+''.join(screens)+'</table>'
    verification=report.get('verification',{})
    coverage=''
    if verification:
        calendar=verification.get('calendar_action_coverage',{})
        counts=calendar.get('action_counts',{})
        counts_text=', '.join(f'{n} {kind}' for kind,n in counts.items())
        duration=verification['source_duration_ms']/1000
        coverage=(f'<h3>Full recording audit · {int(duration//60):02d}:{duration%60:06.3f}</h3>'
            f'<p>{verification["base_frames_processed"]:,} / {verification["base_frames_expected"]:,} base frames processed. '
            f'Actions: {escape(counts_text)}. {calendar.get("date_action_statuses",{}).get("one_action",0)} / {calendar.get("observed_dates",0)} dated turns have one action receipt.</p>'
            f'<p>Stat intervals: {escape(str(verification["stat_intervals"]))}; performance intervals: {escape(str(verification["performance_intervals"]))}; '
            f'fan intervals: {escape(str(verification.get("fan_intervals",{})))}.</p>'
            f'<p><strong>Full source processed does not mean every effect is independently verified.</strong> '
            f'{verification.get("state_supported_training_fields",0)} training gain fields use surrounding-state constraints to select a visible candidate. '
            'Complete effect recall, inventory and bonus values remain under review; this is one development recording.</p>'
            '<details><summary>Verification data and remaining gates</summary><pre>'+escape(json.dumps(verification,indent=2))+'</pre></details>')
        calendar_rows=[]
        for window in calendar.get('windows',[]):
            labels=', '.join(a['kind']+(' · '+a['training_option'] if a.get('training_option') else '') for a in window['actions'])
            calendar_rows.append(f'<tr><td>{escape(window["date"])}</td><td>{window["start_ms"]/1000:.3f}s</td><td>{escape(labels or "Missing")}</td><td>{escape(window["status"])}</td><td>{proof(window["evidence"],"Date")}</td></tr>')
        coverage+='<details><summary>Check actions against all observed career dates</summary><table><tr><th>Date</th><th>Source time</th><th>Action</th><th>Status</th><th>Evidence</th></tr>'+''.join(calendar_rows)+'</table></details>'
        integrity=report.get('evidence_integrity_snapshot')
        if integrity:
            state='passed' if integrity['evidence_integrity_verified'] else 'failed'
            coverage+=f'<p>Evidence integrity snapshot: <strong>{state}</strong> · {integrity["verified_observations"]:,} observations and {integrity["verified_refinements"]:,} refinements. <a href="evidence-audit.json">Read the source and pixel audit</a>. Re-run after changing evidence files.</p>'
    action_rows=[]
    for action in data.get('turn_action_receipts',[]):
        time=action['source_timestamp_ms']/1000
        label=action['kind']+(' · '+action['training_option'] if action.get('training_option') else '')
        action_rows.append(f'<tr><td>{int(time//60):02d}:{time%60:06.3f}</td><td>{escape(label)}</td><td>{proof(action["evidence"],"Receipt")}</td></tr>')
    coverage+='<h3>Completed turn actions</h3><p>Times identify visible receipts, not inferred mouse clicks. Browsing training options is listed separately.</p><table><tr><th>Source time</th><th>Action</th><th>Evidence</th></tr>'+''.join(action_rows)+'</table>'
    skills='<h3>Skill purchase batches</h3><pre>'+escape(json.dumps(data.get('skill_purchases',[]),indent=2))+'</pre>'
    pending=data.get('unparsed_receipt_candidates',[])
    review='<details><summary>Unparsed receipt candidates ('+str(len(pending))+')</summary><p>These may be partial OCR or missed effects. Their numbers are not added to any ledger.</p><ul>'+''.join(
        f'<li>{p["first_seen_ms"]/1000:.3f}s · {escape(p["raw_text"])} · {proof(p["evidence"],"View")}</li>' for p in pending)+'</ul></details>'
    identities=[]
    for event in data.get('events',[]):
        candidates=event.get('ambiguous_effect_candidates',[])
        if not candidates:continue
        alternatives=[]
        for candidate in candidates:
            effect=candidate['effect']
            label=effect.get('raw_text') or effect.get('name') or effect['kind']
            alternatives.append(f'<li>{escape(label)} · {proof(candidate.get("evidence",[]),"Source")}</li>')
        identities.append(f'<details><summary>{event["first_seen_ms"]/1000:.3f}s · unresolved receipt identity</summary>'
                          '<p>These are conflicting readings, not separate confirmed awards. No recipient has been selected.</p><ul>'
                          +''.join(alternatives)+'</ul></details>')
    if identities:
        review+='<h3>Unresolved receipt identities</h3>'+''.join(identities)
    return heading+coverage+review+accounting+'<h3>Supported lesson transitions</h3><ul>'+''.join(purchases)+'</ul>'+skills+screen_table+'<h3>Mechanics observations</h3>'+''.join(facts)
