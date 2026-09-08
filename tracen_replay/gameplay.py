"""Gameplay-pane-only observations. No auxiliary log is an input to this module.

Screen facts, confirmation requests, receipts, and arithmetic are separate records.
Unknown effects never become invented transactions to close a residual.
"""
import json
import re
from pathlib import Path
from .stats import Reader
from .reconcile import FIELDS, stable_checkpoints, preview_segments, account

PANE = (148, 0, 958, 1080)
CURRENCIES = ('dance', 'passion', 'vocal', 'visual', 'composure')
CHANGE = re.compile(r'^(Speed|Stamina|Power|Guts|Wit|Skill (?:Pts|Points)) went (up|down) by (\d+)(?: to new heights)?[.!]?$', re.I)


def text_lines(reader, image, psm=6):
    groups = {}
    for w in reader.ocr(image, psm=psm):
        groups.setdefault((w['block_num'], w['par_num'], w['line_num']), []).append(w)
    result = []
    for words in groups.values():
        # Drop non-word edge artifacts, never words or digits inside a statement.
        while words and not re.search(r'\w', words[0]['text']):
            words = words[1:]
        while words and not re.search(r'\w', words[-1]['text']):
            words = words[:-1]
        if words:
            result.append(dict(text=' '.join(w['text'] for w in words),
                               confidence=min(float(w['conf']) for w in words)))
    return result


def effects_from_lines(lines):
    """Only explicit past-tense receipts; plus signs in previews do not qualify."""
    effects = []
    for line in lines:
        if line['confidence'] < 60:
            continue
        text = line['text'].strip()
        effect = None
        if m := CHANGE.fullmatch(text):
            field = 'skill_points' if m[1].lower().startswith('skill') else m[1].lower()
            effect = dict(kind='stat_change', field=field, amount=int(m[3]) * (1 if m[2].lower() == 'up' else -1))
        elif m := re.fullmatch(r'(Speed|Stamina|Power|Guts|Wit|Dance|Passion|Vocals?|Visuals?|Composure) cap went up by (\d+)[.!]?',text,re.I):
            field={'vocals':'vocal','visuals':'visual'}.get(m[1].lower(),m[1].lower())
            effect=dict(kind='stat_cap_change' if field in FIELDS else 'performance_cap_change',field=field,amount=int(m[2]))
        elif m := re.fullmatch(r'Gained ([\d,]+) fans[.!]?',text,re.I):
            effect=dict(kind='fan_change',amount=int(m[1].replace(',','')))
        elif m := re.fullmatch(r'(Speed|Stamina|Power|Guts|Wit|Skill Pts) Bonus went up by (\d+)[.!]?',text,re.I):
            field='skill_points' if m[1].lower().startswith('skill') else m[1].lower()
            effect=dict(kind='training_modifier_change',field=field,amount=int(m[2]))
        elif m := re.fullmatch(r'Energy recovered by (\d+)[.!]?', text, re.I):
            effect = dict(kind='energy_change', amount=int(m[1]))
        elif m := re.fullmatch(r'Max Energy increased by (\d+)[.!]?',text,re.I):
            effect=dict(kind='max_energy_change',amount=int(m[1]))
        elif m := re.fullmatch(r'(Turf|Dirt|Sprint|Mile|Medium|Long|Front Runner|Pace Chaser|Late Surger|End Closer) Aptitude went up[.!]?',text,re.I):
            effect=dict(kind='aptitude_change',name=m[1],direction='up',amount=None,rank=None)
        elif m := re.fullmatch(r'Acquired (.+?)\s*[.!]',text,re.I):
            effect=dict(kind='condition_acquired',name=m[1].strip(),mechanical_effect=None)
        elif m := re.fullmatch(r'Unlocked recreation with (.+?)[.!]',text,re.I):
            effect=dict(kind='recreation_unlocked',name=m[1],amount=None)
        elif m := re.fullmatch(r'Inspired by (.+?)[!]',text,re.I):
            effect=dict(kind='inheritance_inspiration',name=m[1],amount=None)
        elif m := re.fullmatch(r'(.+?) spark activated[!]',text,re.I):
            effect=dict(kind='inheritance_spark',name=m[1],amount=None,awarded_effects_unknown=True)
        elif m := re.fullmatch(r'Energy went (up|down) by (\d+)[.!]?', text, re.I):
            effect = dict(kind='energy_change', amount=int(m[2])*(1 if m[1].lower()=='up' else -1))
        elif re.fullmatch(r'Energy is full[.!]?',text,re.I):
            effect=dict(kind='energy_status',value='full',amount=None)
        elif m := re.fullmatch(r'Mood went (up|down)[.!]?', text, re.I):
            effect = dict(kind='mood_change', direction=m[1].lower(), amount=None)
        elif m := re.fullmatch(r'Mood remains (Great|Good|Normal|Bad|Awful)[.!]?',text,re.I):
            effect=dict(kind='mood_status',value=m[1].lower(),amount=None)
        elif m := re.fullmatch(r'(Speed|Stamina|Power|Guts|Wit) training leveled up[.!]?',text,re.I):
            effect=dict(kind='training_level_change',field=m[1].lower(),direction='up',amount=None)
        elif m := re.fullmatch(r'(.+?) leveled up[.!]',text,re.I):
            effect=dict(kind='skill_level_change',name=m[1],direction='up',amount=None,levels_observed=False)
        elif m := re.fullmatch(r'Friendship with (.+?) is maxed out[.!]?',text,re.I):
            effect=dict(kind='friendship_status',name=m[1],value='maximum',amount=None)
        elif m := re.fullmatch(r"Friendship with (.+?) didn't go up[.!]?",text,re.I):
            effect=dict(kind='friendship_status',name=m[1],value='unchanged',amount=0)
        elif m := re.fullmatch(r'Friendship with (.+?) went up by (\d+)[.!]?', text, re.I):
            effect = dict(kind='friendship_change', name=m[1], amount=int(m[2]))
        elif m := re.fullmatch(r'Gained (\d+) hint level\(s\) for (.+?)[.!]?', text, re.I):
            effect = dict(kind='skill_hint_change', name=m[2].rstrip('.!').strip(), amount=int(m[1]))
        elif m := re.fullmatch(r'(Dance|Passion|Vocals?|Visuals?|Composure) went (up|down) by (\d+)[.!]?', text, re.I):
            field = {'vocals':'vocal','visuals':'visual'}.get(m[1].lower(),m[1].lower())
            effect = dict(kind='performance_change', field=field, amount=int(m[3])*(1 if m[2].lower()=='up' else -1))
        elif m := re.fullmatch(r'Learned the song ["“](.+?)["”][.!]?', text, re.I):
            effect = dict(kind='song_learned', name=m[1].strip(), acquisition='unknown', cost=None)
        elif m := re.fullmatch(r'Learned (.+?)[.!]', text, re.I):
            effect = dict(kind='named_acquisition', name=m[1], acquisition='unknown', cost=None)
        elif m := re.fullmatch(r'(.+?) joined your cause[.!]?', text, re.I):
            effect = dict(kind='supporter_joined', name=m[1])
        elif re.fullmatch(r'New supporters joined[!]',text,re.I):
            effect=dict(kind='supporters_joined_announcement',amount=None)
        elif m := re.fullmatch(r'(.+?) will now appear in training[.!]',text,re.I):
            effect=dict(kind='training_appearance_unlocked',name=m[1],amount=None)
        elif re.fullmatch(r'Hype Level went up[.!]?', text, re.I):
            effect = dict(kind='hype_increased', amount=None)
        elif re.fullmatch(r'Hype Level is maxed out[.!]?', text, re.I):
            effect = dict(kind='hype_status', value='maximum', amount=None)
        elif m := re.fullmatch(r'(.+?) hint (?:level|Lv\.?) (?:went up by|increased by) (\d+)[.!]?', text, re.I):
            effect = dict(kind='skill_hint_change', name=m[1], amount=int(m[2]))
        if effect:
            effects.append(dict(effect, raw_text=text, confidence=line['confidence']))
    return effects


def preview_effects(lines):
    result = []
    seen = set()
    for line in lines:
        text = line['text']
        if line['confidence']<60 or text in seen:
            continue
        seen.add(text)
        kind = None
        if re.search(r'\bTraining (Speed|Stamina|Power|Guts|Wit) Gain\b',text,re.I):
            kind = 'future_training_modifier'
        elif re.search(r'Friendship Training Effectiveness|Support Chain Event Frequency|Specialty Priority',text,re.I):
            kind = 'queued_concert_bonus'
        elif re.fullmatch(r'(Speed|Stamina|Power|Guts|Wit|Skill Pts)\s*\+\s*\d+',text,re.I):
            kind = 'immediate_on_purchase'
        if kind:
            result.append(dict(kind=kind, raw_text=text, awarded=False))
    return result


def classify(text, header, result_grid=False, preview=False):
    """Priority prevents underlying dimmed screens overriding modal semantics."""
    lower = text.lower()
    if 'take the day off to let your trainee recover energy?' in lower and 'entire turn' in lower:
        return 'rest_confirmation'
    if re.search(r'\blearn\s+the\s+above\s+skills?\b',lower):
        return 'skill_confirmation'
    if 'skills learned' in lower or 'trainee learned new skills' in lower:
        return 'skill_receipt'
    if 'spend performance points to learn' in lower:
        return 'lesson_confirmation'
    if 'confirm playback' in lower or ('landscape' in lower and 'portrait' in lower):
        return 'playback_confirmation'
    if 'ready to start the concert' in lower:
        return 'concert_confirmation'
    if 'great success' in lower and not result_grid:
        return 'concert_result_candidate'
    if 'complete a career playthrough' in lower:
        return 'career_summary'
    if 'finish this career playthrough' in lower:
        return 'career_finish_confirmation'
    if re.search(r'Fans\s+[\d,]+\s*\(\+[\d,]+\)', text, re.I):
        return 'race_result'
    if header.lower().startswith('lessons') and 'performance' in lower:
        return 'lesson_selection'
    if header.lower().startswith('learn'):
        return 'skill_selection'
    if header.lower().startswith('training') and result_grid:
        return 'training_result'
    if preview:
        return 'training_preview'
    if CHANGE.search(text) or 'went up by' in lower or 'recovered by' in lower:
        return 'event_outcome'
    return 'unknown'


class GameplayReader:
    def __init__(self, executable=None):
        self.reader = Reader(executable)

    def read_pane(self, pane):
        """Accept exactly the cropped gameplay image, never the full recording."""
        if pane.size != (810, 1080):
            raise ValueError('Gameplay OCR requires an 810x1080 gameplay crop.')
        r = self.reader
        def crop(box):
            return pane.crop((box[0]-148, box[1], box[2]-148, box[3]))
        # Legacy stat layout code sees only a blank canvas and gameplay pixels.
        isolated = r.Image.new('RGB', (1920, 1080), 'white')
        isolated.paste(pane, (148, 0))
        stats = r.stats_image(isolated)
        regions = {
            'header': text_lines(r, crop((155, 0, 250, 29)).resize((380, 116)), 7),
            'sparse': text_lines(r, pane, 11),
            'body': text_lines(r, crop((270, 200, 850, 780))),
            'outcome': text_lines(r, crop((310, 795, 815, 940))),
            'modal_title': text_lines(r, crop((300, 87, 650, 123)).resize((1050,108)), 7),
        }
        text = '\n'.join(x['text'] for rows in regions.values() for x in rows if x['confidence'] >= 55)
        header = ' '.join(x['text'] for x in regions['header'] if x['confidence'] >= 60)
        blue = []
        for x in (270, 470):
            pixels = list(crop((x, 911, x+35, 934)).convert('RGB').getdata())
            blue.append(sum(b > rr+25 and g > rr+15 and b > 100 for rr,g,b in pixels)/len(pixels))
        grid = min(blue) > .45
        screen = classify(text, header, grid, stats.get('training_preview', False))
        effects = effects_from_lines(regions['outcome'])
        if effects and screen == 'unknown':
            screen = 'event_outcome'
        # Receipts in ordinary gameplay only; projected dialogs and menus are not awards.
        if screen not in ('unknown', 'event_outcome'):
            effects = []
        option = None
        if screen == 'training_result':
            words = r.ocr(crop((230,165,365,194)).resize((540,116)), psm=7)
            options = [w['text'].lower() for w in words if w['text'].lower() in FIELDS[:5] and float(w['conf']) >= 80]
            option = options[0] if len(options)==1 else None
        facts = {}
        if screen == 'training_result':
            values, caps = {}, {}
            for i, field in enumerate(FIELDS):
                x = (322,518,714)[i%3]
                y = 834 if i<3 else 952
                im = crop((x,y,x+126,y+42))
                ws = r.ocr(im.resize((378,126)),psm=7)
                token = ''.join(w['text'] for w in ws)
                match = re.fullmatch(r'(\d{1,4})/(\d{3,4})',token)
                if match and min(float(w['conf']) for w in ws)>=70 and int(match[1])<=int(match[2]):
                    values[field],caps[field] = int(match[1]),int(match[2])
                else:
                    values[field] = None
            # Skill points have no cap. Require a plain number, not an arrow gain.
            im = crop((708,952,810,990))
            ws = r.ocr(im.resize((408,152)),psm=7)
            if len(ws)==1 and re.fullmatch(r'\d{1,4}',ws[0]['text']) and float(ws[0]['conf'])>=90:
                values['skill_points'] = int(ws[0]['text'])
            facts['result_values'] = values
            facts['stat_caps'] = caps
        if screen == 'race_result':
            m = re.search(r'Fans\s+([\d,]+)\s*\(\+([\d,]+)\)', text, re.I)
            facts['fans'] = int(m[1].replace(',', ''))
            facts['fans_gained'] = int(m[2].replace(',', ''))
            facts['race_description'] = [x['text'] for x in regions['sparse'] if x['confidence']>=60 and re.search(r'\b(?:Turf|Dirt|\d+m)\b',x['text'])]
            facts['placing'] = None
            names = text_lines(r,crop((280,425,810,456)).resize((1590,93)),7)
            facts['race_name'] = names[0]['text'] if len(names)==1 and names[0]['confidence']>=80 else None
            description = ' '.join(facts['race_description'])
            course = re.search(r'^(.+?)\s+(Turf|Dirt)\s+(\d+)m\s+\(([^)]+)\)\s+(Right|Left|Straight)(?:\s*/\s*(Outer|Inner))?',description,re.I)
            facts['course'] = dict(venue=course[1],surface=course[2].lower(),distance_m=int(course[3]),
                                   distance_category=course[4].lower(),direction=course[5].lower(),
                                   variant=course[6].lower() if course[6] else None) if course else None
        if screen == 'lesson_confirmation':
            facts['name_candidates'] = [x['text'] for x in regions['modal_title'] if x['confidence']>=80]
            facts['projected_performance_points'] = self.numbers(crop, [(392+83*i,846,425+83*i,876) for i in range(5)], CURRENCIES)
            facts['current_stats'] = self.numbers(crop, [(308,376,365,404),(403,376,455,404),(497,376,549,404),(591,376,644,404),(683,376,735,404),(756,376,817,404)], FIELDS)
            facts['projected_effects'] = preview_effects([x for rows in regions.values() for x in rows])
            facts['awarded_effects'] = []
        if screen == 'lesson_selection':
            facts['performance_points'] = self.numbers(crop, [(345,91,389,119)]+[(344+104*i,88,394+104*i,123) for i in range(1,5)], CURRENCIES)
            facts['available_effects'] = preview_effects(regions['sparse'])
        if screen == 'skill_selection':
            # This counter changes when checking/unchecking skills: it can be a projection.
            facts['points_semantics'] = 'possibly_projected_remaining_points'
        if screen in ('skill_confirmation', 'skill_receipt'):
            facts['item_list_complete'] = False
            facts['spent_skill_points'] = None
        return dict(screen=screen, stats=stats, training_option=option, effects=effects,
                    facts=facts, ocr=regions, completed_action='training' if screen=='training_result' else None)

    def numbers(self, crop, boxes, names):
        values = {}
        for name, box in zip(names, boxes):
            im = crop(box)
            ws = self.reader.ocr(im.resize((im.width*4,im.height*4)), numeric=True, psm=7)
            values[name] = int(ws[0]['text']) if len(ws)==1 and re.fullmatch(r'\d{1,4}',ws[0]['text']) and float(ws[0]['conf'])>=80 else None
        return values


def episodes(readings):
    """Consecutive screen observations, never count sampled frames as actions."""
    result = []
    current = None
    for row in readings:
        key = (row['screen'], row.get('training_option'))
        if current is None or current['_key'] != key or row['source_timestamp_ms']-current['last_seen_ms']>500:
            current = dict(id=f'screen-{len(result)+1:04d}', _key=key, screen=row['screen'],
                           training_option=row.get('training_option'), first_seen_ms=row['source_timestamp_ms'],
                           last_seen_ms=row['source_timestamp_ms'], evidence=row['evidence'], supporting_frames=[],
                           completed_action=row.get('completed_action'), identity_verified=False)
            result.append(current)
        current['last_seen_ms'] = row['source_timestamp_ms']
        current['supporting_frames'].append(row['evidence'])
    for row in result:
        row.pop('_key')
    return result


def screen_summary(readings):
    spans = episodes(readings)
    for span in spans:
        if span['screen'] == 'concert_result_candidate':
            prior = [s for s in spans if s['screen']=='concert_confirmation' and 0 < span['first_seen_ms']-s['last_seen_ms']<=30000]
            if prior:
                span.update(screen='concert_result', completed_action='concert', confirmation_evidence=prior[-1]['evidence'])
        elif span['screen'] == 'skill_receipt':
            prior = [s for s in spans if s['screen']=='skill_confirmation' and 0 < span['first_seen_ms']-s['last_seen_ms']<=15000]
            if prior:
                span['confirmation_evidence'] = prior[-1]['evidence']
                span['completed_action'] = 'skill_purchase_batch'
                span['purchased_names'] = None
                span['spent_skill_points'] = None
    return spans


def ledger(readings, checkpoints):
    intervals = []
    for before, after in zip(checkpoints, checkpoints[1:]):
        start, end = before['last_seen_ms'], after['first_seen_ms']
        candidates = [r for r in readings if start < r['source_timestamp_ms'] <= end and r['effects']]
        events, active, last_seen = [], None, None
        result_rows = [r for r in readings if start<r['source_timestamp_ms']<=end and r['screen']=='training_result']
        # Only one observed option in a short transition; never use the preview
        # as the selected option. Repeated result readings support partial gains.
        options = {r.get('training_option') for r in result_rows if r.get('training_option')}
        preceding_awards = result_rows and any(r['source_timestamp_ms']<result_rows[0]['source_timestamp_ms'] and any(e['kind']=='stat_change' for e in r['effects']) for r in candidates)
        if result_rows and not preceding_awards and len(options)==1 and result_rows[-1]['source_timestamp_ms']-start<=5000:
            deltas, proofs = {}, []
            for field in FIELDS:
                seen = [(r,r['facts'].get('result_values',{}).get(field)) for r in result_rows if r.get('training_option') in options]
                seen = [(r,v) for r,v in seen if type(v) is int]
                if len(seen)>=2 and len({v for _,v in seen})==1 and 50<=seen[-1][0]['source_timestamp_ms']-seen[0][0]['source_timestamp_ms']<=750:
                    delta = seen[0][1]-before['values'][field]
                    if delta>0:
                        deltas[field]=delta
                        proofs.extend(r['evidence'] for r,_ in seen)
            if deltas:
                events.append(dict(kind='training_result_state_change',training_option=next(iter(options)),
                                   deltas=deltas,evidence=proofs[0],supporting_frames=list(dict.fromkeys(proofs)),
                                   source_timestamp_ms=result_rows[0]['source_timestamp_ms'],identity_verified=False))
        for row in candidates:
            deltas = {e['field']: e['amount'] for e in row['effects'] if e['kind']=='stat_change'}
            if not deltas:
                continue
            # Only continuous overlapping compatible text is one visible receipt.
            compatible = active and last_seen is not None and row['source_timestamp_ms']-last_seen <= 500 and set(active['deltas']) & set(deltas) and all(active['deltas'].get(k,v)==v for k,v in deltas.items())
            if not compatible:
                active = dict(deltas={}, source_timestamp_ms=row['source_timestamp_ms'], evidence=row['evidence'], supporting_frames=[], identity_verified=False)
                events.append(active)
            active['deltas'].update(deltas)
            active['supporting_frames'].append(row['evidence'])
            last_seen = row['source_timestamp_ms']
        entry = account(before, after, events)
        entry['review_required'] = True
        entry['reason'] = 'Arithmetic agreement does not establish receipt identity or complete coverage.'
        intervals.append(entry)
    return intervals


def lesson_transitions(readings):
    """A unique dialog plus observed matching debit supports a lesson purchase.

    Do not promote preview bonuses into awarded stats. Equal resource balances
    without a debit are insufficient, including a canceled or free lesson.
    """
    before = None
    pending = None
    purchases = []
    for row in readings:
        t = row['source_timestamp_ms']
        facts = row['facts']
        if row['screen'] == 'lesson_confirmation':
            names = facts.get('name_candidates', [])
            projected = facts.get('projected_performance_points', {})
            if len(names)==1 and all(type(projected.get(k)) is int for k in CURRENCIES):
                if pending and pending['name'] != names[0]:
                    before = None  # Multiple unobserved purchases cannot be assigned.
                pending = dict(name=names[0], points=projected, evidence=row['evidence'], time=t)
        elif row['screen'] == 'lesson_selection':
            points = facts.get('performance_points', {})
            if not all(type(points.get(k)) is int for k in CURRENCIES):
                continue
            if before and pending and 0 < t-pending['time']<=5000 and t-before['time']<=10000:
                debit = {k:before['points'][k]-points[k] for k in CURRENCIES}
                if points == pending['points'] and all(v>=0 for v in debit.values()) and any(debit.values()):
                    purchases.append(dict(kind='lesson_purchase_supported', name=pending['name'],
                                          performance_cost=debit, source_timestamp_ms=t,
                                          evidence=[before['evidence'],pending['evidence'],row['evidence']],
                                          awarded_stats=None, click_timestamp_ms=None))
                    pending = None
                elif points != before['points']:
                    pending = None
                # The menu can reappear before its counter animation updates.
                # An unchanged balance neither confirms nor cancels the request.
            if pending is None:
                before = dict(points=points, evidence=row['evidence'], time=t)
        elif pending and t-pending['time']>5000:
            pending = None
            before = None
    return purchases


def investigation_windows(intervals, readings, clip, budget=2):
    windows = []
    if budget <= 0:
        return windows
    for interval in intervals:
        if interval['status'] != 'unresolved':
            continue
        candidates = [r for r in readings if interval['start_ms'] < r['source_timestamp_ms'] < interval['end_ms'] and r['screen'] in ('training_result','event_outcome')]
        focus = candidates[0]['source_timestamp_ms'] if candidates else interval['start_ms']
        start = max(clip['source_start_ms'], focus-500)
        end = min(clip['source_start_ms']+clip['duration_ms'], start+3000)
        if end>start and not any(start<w['end_ms'] and end>w['start_ms'] for w in windows):
            windows.append(dict(start_ms=start,end_ms=end,reason='unexplained_stat_change',
                                before_id=interval['before_id'], after_id=interval['after_id']))
        if len(windows)>=budget:
            break
    return windows


def track(report, root, executable=None, source=None, origin=0):
    reader = GameplayReader(executable)
    directory = Path(root)/'gameplay'
    directory.mkdir(exist_ok=True)
    readings = []
    def read_frame(frame):
        with reader.reader.Image.open(Path(root)/frame['evidence']) as source:
            if source.size != (1920,1080):
                raise ValueError('Gameplay analysis requires the English 1920x1080 layout.')
            pane = source.convert('RGB').crop(PANE)
        evidence = f'gameplay/{frame["id"]}.png'
        pane.save(Path(root)/evidence)
        row = reader.read_pane(pane)
        row.update(source_timestamp_ms=frame['source_timestamp_ms'], evidence=evidence)
        return row
    readings = [read_frame(frame) for frame in report['frames']]
    stat_rows = [dict(r['stats'], source_timestamp_ms=r['source_timestamp_ms'], evidence=r['evidence']) for r in readings]
    checkpoints = stable_checkpoints(stat_rows)
    initial_intervals = ledger(readings,checkpoints)
    investigation = dict(enabled=source is not None, requested_fps=8, max_windows=2, max_seconds_per_window=3,
                         windows=[], additional_frames=0)
    if source is not None and report['sampling']['requested_fps']<8:
        from .pipeline import decode_frames
        seen = {f['source_timestamp_ms'] for f in report['frames']}
        report['sampling']['base_frame_count'] = len(report['frames'])
        report['sampling']['supplemental_method'] = 'residual_triggered_resampling'
        for index, window in enumerate(investigation_windows(initial_intervals,readings,report['clip'])):
            relative = f'investigation-{index+1:02d}/frames'
            destination = Path(root)/relative
            destination.mkdir(parents=True)
            extra = decode_frames(source,destination,window['start_ms']/1000,(window['end_ms']-window['start_ms'])/1000,8,origin)
            for frame in extra:
                if frame['source_timestamp_ms'] in seen:
                    continue
                seen.add(frame['source_timestamp_ms'])
                frame['id'] = f'investigation-{index+1:02d}-{frame["id"]}'
                frame['evidence'] = f'{relative}/{Path(frame["evidence"]).name}'
                frame['clip_timestamp_ms'] = frame['source_timestamp_ms']-report['clip']['source_start_ms']
                frame['origin'] = 'residual_triggered_resampling'
                report['frames'].append(frame)
                readings.append(read_frame(frame))
                investigation['additional_frames'] += 1
            investigation['windows'].append(window)
        report['frames'].sort(key=lambda f:f['source_timestamp_ms'])
        report['sampling']['frame_count'] = len(report['frames'])
        readings.sort(key=lambda r:r['source_timestamp_ms'])
        stat_rows = [dict(r['stats'], source_timestamp_ms=r['source_timestamp_ms'], evidence=r['evidence']) for r in readings]
        checkpoints = stable_checkpoints(stat_rows)
    spans = screen_summary(readings)
    receipts = [s for s in spans if s['screen']=='skill_receipt']
    return dict(method='gameplay_only_v1', input_region=list(PANE), auxiliary_log_used=False,
                readings=readings, checkpoints=checkpoints, intervals=ledger(readings,checkpoints),
                initial_intervals=initial_intervals, investigation=investigation,
                screens=spans, training_previews=preview_segments(stat_rows), skill_receipts=receipts,
                lesson_purchases=lesson_transitions(readings),
                limitations=['One English 1080p layout; unknown screens abstain.',
                             'Stat checkpoints are observed states, not verified turn boundaries.',
                             'Screen episodes can fragment; their count is not an action count.',
                             'Lesson confirmations contain projections, not confirmed purchases.',
                             'Skill receipts establish a batch acquisition, not complete names or cost.',
                             'Race fans are separate from stats; placing and item identities are not read.',
                             'Fixed sampling can miss outcomes; no complete event history is claimed.'])
