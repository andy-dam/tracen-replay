"""HTML presentation of gameplay-only observations and unresolved accounting."""
from html import escape
import json
from .reconcile import FIELDS


def render_gameplay(report):
    data = report.get('gameplay_tracking')
    if not data:
        return ''
    def proof(path, label='Gameplay screenshot'):
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
        cost = ', '.join(f'{value} {name}' for name,value in row['performance_cost'].items() if value)
        evidence = ' · '.join(proof(path,label) for path,label in zip(row['evidence'],('Before','Confirmation','After')))
        purchases.append(f'<li>{escape(row["name"])} · cost: {escape(cost)} · {evidence}. Stat award remains unverified.</li>')
    heading = '<h2>Gameplay-only analysis</h2><p>Only the gameplay crop is read. A confirmation is a request; a preview is not an award. Unknown fields remain unknown. Screen spans are observations, not action counts.</p>'
    accounting = '<h3>Observed stat checkpoints</h3><table><tr><th>Source time</th><th>Speed</th><th>Stamina</th><th>Power</th><th>Guts</th><th>Wit</th><th>Skill points</th><th>Evidence</th></tr>'+''.join(checkpoints)+'</table><h3>Changes between checkpoints</h3><p>Even a balanced interval requires review of event identity and coverage.</p>'+(''.join(intervals) or '<p>No complete checkpoint pair was observed in this clip.</p>')
    screen_table = '<h3>Observed screens</h3><table><tr><th>Source time</th><th>Screen</th><th>Training result option</th><th>Evidence</th></tr>'+''.join(screens)+'</table>'
    return heading+accounting+'<h3>Supported lesson transitions</h3><ul>'+''.join(purchases)+'</ul>'+screen_table+'<h3>Mechanics observations</h3>'+''.join(facts)
