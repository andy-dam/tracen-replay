from html import escape
from .reconcile import FIELDS


def clock(ms):
    return f'{ms//60000:02}:{ms%60000/1000:06.3f}'


def render_accounting(report):
    data = report.get('stat_tracking')
    if not data:
        return ''
    headers = ''.join(f'<th>{escape(f.replace("_", " ").title())}</th>' for f in FIELDS)
    rows = []
    for checkpoint in data['checkpoints']:
        cells = ''.join(f'<td>{checkpoint["values"][f]}</td>' for f in FIELDS)
        countdown = checkpoint.get('turns_remaining_to_goal')
        rows.append(f'<tr><td><a href="{escape(checkpoint["evidence"])}">{clock(checkpoint["first_seen_ms"])}–{clock(checkpoint["last_seen_ms"])}</a></td>{cells}<td>{countdown if countdown is not None else "Unknown"}</td><td>{len(checkpoint["supporting_frames"])}</td></tr>')
    previews = sum(bool(r.get('training_preview')) for r in data['readings'])
    result = f'<h2 id="checkpoints">Stat checkpoints</h2><p>Experimental OCR · {len(rows)} stable checkpoints · {previews} training-preview frames. Preview gains are never counted as completed actions. Repeated OCR agreement is not human verification.</p><div style="overflow-x:auto"><table><thead><tr><th>Observed span</th>{headers}<th>Turns to goal</th><th>Supporting frames</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    coverage = data.get('coverage', {})
    if coverage:
        result += f'<p>{coverage["complete_readings"]} of {coverage["sampled_frames"]} sampled frames have six readable totals. This is observation coverage, not an accuracy score. Gaps remain separate even when totals match.</p>'
    if not rows:
        result += '<p>No sufficiently stable complete readings were found. No missing values were filled in.</p>'
    result += '<h2>Training options inspected</h2><p>These spans show browsing. The final preview is not evidence of which option was chosen.</p>'
    for preview in data.get('previews', []):
        result += f'<p><a href="{escape(preview["evidence"])}">{clock(preview["first_seen_ms"])}–{clock(preview["last_seen_ms"])}</a> · {escape((preview["option"] or "Unknown option").title())} preview</p>'
    result += '<h2>Between-checkpoint accounting</h2><p>These are intervals between visible states, not guaranteed individual turns. A balanced interval only means the recognized changes add up for these six fields. Event assignment and action timing remain unverified.</p>'
    for interval in data['intervals']:
        status = 'Balanced — review evidence' if interval['status']=='balanced' else 'Unresolved discrepancy'
        result += f'<article><h3>{clock(interval["start_ms"])}–{clock(interval["end_ms"])} · {status}</h3><div style="overflow-x:auto"><table><thead><tr><th>Accounting</th>{headers}</tr></thead><tbody>'
        for key,label in [('observed_change','Observed change'),('supported_change','OCR-supported changes'),('initial_unexplained_change','Unexplained on first pass'),('unexplained_change','Unexplained after review pass')]:
            result += '<tr><th>'+label+'</th>'+''.join(f'<td>{interval[key][f]:+d}</td>' for f in FIELDS)+'</tr>'
        result += '</tbody></table></div><p>'+('A denser pass checked the intervening samples and main outcome text.' if interval['dense_pass_performed'] else 'The initial log pass balanced the totals.')+'</p>'
        for event in interval['events']:
            action = f'Log entry: {event["training_option"]} training; timing unverified' if event['training_option'] else 'Outcome text; action type unknown'
            result += f'<details><summary>{escape(action)} · evidence at {clock(event["observed_at_ms"])}</summary><p>{escape(event["origin"])}</p><pre>{escape(event["text"])}</pre><a href="{escape(event["evidence"])}">Open supporting screenshot</a></details>'
        investigation = interval.get('investigation')
        if investigation:
            result += f'<p>Inspected {investigation["coarse_frames"]} coarse log frames, {investigation["dense_frames"]} additional sampled log frames, and {investigation["main_outcome_frames"]} main-outcome frames. No additional source frames were decoded.</p>'
        raw = interval.get('raw_observations', [])
        decisions = interval.get('deduplication_decisions', [])
        result += f'<details><summary>Audit: {len(raw)} raw block observations, {len(decisions)} partial/baseline merge decisions</summary><p>Repeated equal vectors are consolidated provisionally. Matching deltas do not establish event identity.</p>'
        for decision in decisions:
            candidate = interval['raw_change_candidates'][decision['candidate_index']]
            result += f'<p>{escape(decision["decision"])} · <a href="{escape(candidate["evidence"])}">{clock(candidate["observed_at_ms"])}</a></p><pre>{escape(candidate["text"])}</pre>'
        result += '<details><summary>All raw observations before consolidation</summary>'
        for observation in raw:
            result += f'<p><a href="{escape(observation["evidence"])}">{clock(observation["observed_at_ms"])}</a> · {escape(observation["origin"])}</p><pre>{escape(observation["text"])}</pre>'
        result += '</details></details><p class="meta">Identical and partial delta blocks are merged conservatively; repeated events and historical log entries still need review. No event was invented to balance the totals.</p></article>'
    return result
