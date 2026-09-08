"""Experimental OCR for the English 1920x1080 landscape recording layout.

No manual annotations or expected totals are inputs to recognition.
"""

import csv
import hashlib
import io
import os
import re
import shutil
import subprocess
from pathlib import Path

from .reconcile import FIELDS, account, stable_checkpoints, distinct_changes, reconcile_changes, preview_segments

BOXES = [(309,721,366,747), (406,721,463,747), (503,721,560,747),
         (598,721,655,747), (692,721,747,747), (755,721,836,760)]
DELTA = re.compile(r'^(Speed|Stamina|Power|Guts|Wit|Skill\s*(?:Pts|Points))\s+went\s+(up|down)\s+by\s+(\d+)\s*[.!]?$', re.I)


class Reader:
    def __init__(self, executable=None):
        from PIL import Image, ImageOps
        self.Image, self.ImageOps = Image, ImageOps
        self.executable = executable or shutil.which('tesseract')
        windows = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Tesseract-OCR/tesseract.exe'
        if not self.executable and windows.exists():
            self.executable = str(windows)
        if not self.executable:
            raise ValueError('Stat tracking requires Tesseract on PATH (or --tesseract PATH).')
        self.version = subprocess.run([self.executable, '--version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        self.cache = {}

    def ocr(self, image, numeric=False, psm=6):
        data = io.BytesIO()
        image.save(data, format='PNG')
        payload = data.getvalue()
        key = (hashlib.sha256(payload).hexdigest(), numeric, psm)
        if key in self.cache:
            return self.cache[key]
        args = [self.executable, 'stdin', 'stdout', '--psm', str(psm), '-l', 'eng']
        if numeric:
            args += ['-c', 'tessedit_char_whitelist=0123456789']
        args += ['tsv']
        result = subprocess.run(args, input=payload, capture_output=True, timeout=20, check=True)
        # TSV text is literal: a quote in dialogue must not swallow later rows.
        words = list(csv.DictReader(io.StringIO(result.stdout.decode('utf-8')), delimiter='\t', quoting=csv.QUOTE_NONE))
        words = [w for w in words if w.get('text', '').strip() and float(w['conf']) >= 0]
        self.cache[key] = words
        return words

    def stats(self, path):
        with self.Image.open(path) as source:
            im = source.convert('RGB')
        return self.stats_image(im)

    def stats_image(self, im):
        if im.size != (1920,1080):
            return {'values': None, 'rejection': 'unsupported_dimensions'}
        # Require the undimmed current-stat header, not result animation panels,
        # lesson previews, profile stats or modal-blurred background values.
        for x in (310,410,510,610,710):
            colors = list(im.crop((x,700,x+35,719)).getdata())
            blue = sum(b > r+40 and g > r+30 and b > 150 for r,g,b in colors)
            if blue / len(colors) < .35:
                return {'values': None, 'rejection': 'stat_header_not_visible'}
        sheet = self.Image.new('RGB', (300,720), 'white')
        for i, box in enumerate(BOXES):
            crop = im.crop(box)
            mask = self.Image.new('L', crop.size)
            mask.putdata([0 if r > g+20 and g > b+10 and r < 220 and b < 140 else 255 for r,g,b in crop.getdata()])
            sheet.paste(mask.resize((crop.width*3,crop.height*3)), (20,i*120+10))
        turn_crop = self.ImageOps.grayscale(im.crop((264,57,325,101)))
        turn_image = self.ImageOps.expand(turn_crop.resize((244,176)), border=20, fill=255)
        words = self.ocr(sheet, numeric=True)
        values, scores = {}, {}
        for i, field in enumerate(FIELDS):
            matches = [w for w in words if i*120 <= int(w['top']) < (i+1)*120]
            if len(matches) == 1 and re.fullmatch(r'\d{1,4}', matches[0]['text']) and float(matches[0]['conf']) >= 65:
                values[field] = int(matches[0]['text'])
                scores[field] = float(matches[0]['conf'])
            else:
                values[field] = None
                scores[field] = None
                # A separate single-line grayscale pass avoids losing thin
                # strokes in the color mask. Uncertain alternatives still abstain.
                crop = im.crop(BOXES[i])
                crop.putdata([(255,255,255) if b>r else (r,g,b) for r,g,b in crop.getdata()])
                crop = self.ImageOps.grayscale(crop)
                crop = self.ImageOps.expand(crop.resize((crop.width*4,crop.height*4)), border=20, fill=255)
                fallback = self.ocr(crop, numeric=True, psm=7)
                if len(fallback)==1 and re.fullmatch(r'\d{1,4}',fallback[0]['text']) and float(fallback[0]['conf'])>=80:
                    values[field] = int(fallback[0]['text'])
                    scores[field] = float(fallback[0]['conf'])
        option = None
        # Browsing is recorded separately and never contributes event deltas.
        failure = ' '.join(w['text'] for w in self.ocr(self.ImageOps.invert(self.ImageOps.grayscale(im.crop((270,772,410,813)))).resize((420,123))))
        if 'failure' in failure.lower():
            heading = im.crop((230,168,360,194)).getchannel('B').point(lambda v: 0 if v>160 else 255)
            heading = self.ImageOps.expand(heading.resize((520,104)),border=20,fill=255)
            options = [w['text'].lower() for w in self.ocr(heading)
                       if w['text'].lower() in FIELDS[:5] and float(w['conf'])>=80]
            option = options[0] if len(options)==1 else None
        turn_words = self.ocr(turn_image, numeric=True, psm=7)
        turns = int(turn_words[0]['text']) if len(turn_words)==1 and re.fullmatch(r'\d{1,2}',turn_words[0]['text']) and float(turn_words[0]['conf'])>=80 else None
        return {'values': values, 'ocr_scores': scores, 'turns_remaining_to_goal': turns, 'training_preview': 'failure' in failure.lower(),
                'preview_option': option, 'completed_action': None,
                'rejection': None if all(v is not None for v in values.values()) else 'uncertain_digits'}

    def log(self, path):
        with self.Image.open(path) as source:
            # Main-pane previews never enter this reader.
            crop = source.convert('RGB').crop((1060,70,1660,1050))
        return self.lines(crop)

    def outcome(self, path):
        with self.Image.open(path) as source:
            crop = source.convert('RGB').crop((310,795,815,940))
        return self.lines(crop)

    def lines(self, crop):
        words = self.ocr(crop)
        lines = {}
        for w in words:
            key = (w['block_num'], w['par_num'], w['line_num'])
            lines.setdefault(key, []).append(w)
        output = []
        for words in lines.values():
            text = ' '.join(w['text'] for w in words)
            heading_words = next((words[i:] for i,w in enumerate(words) if w['text'].lower() == 'training'), [])
            training = re.search(r'\bTraining\s+(Speed|Stamina|Power|Guts|Wit)\s+Lv[l1]', text, re.I)
            option = training[1].lower() if training and len(heading_words)>=2 and min(float(w['conf']) for w in heading_words[:2]) >= 80 else None
            output.append({'text': text, 'confidence': min(float(w['conf']) for w in words), 'training_heading': option,
                           'top': min(int(w['top']) for w in words),
                           'bottom': max(int(w['top'])+int(w['height']) for w in words)})
        return output


def parse_log(lines):
    """Parse explicit changes with raw text and line positions for identity."""
    blocks, current, text = [], {}, []
    line_indices = []
    training_option = None

    def flush():
        if current:
            blocks.append({'deltas': dict(current), 'text': '\n'.join(text),
                           'training_option': training_option,
                           'line_indices': list(line_indices),
                           'event_type': 'logged_training_result' if training_option else 'logged_change'})
        current.clear()
        text.clear()
        line_indices.clear()

    for index, line in enumerate(lines):
        if line.get('training_heading'):
            flush()
            training_option = line['training_heading']
            continue
        # A standalone trailing pipe is a common OCR artifact from card edges.
        # Keep the raw text, and do not strip prefixes or arbitrary suffix words.
        normalized = re.sub(r'\s+\|\s*$', '', line['text'].strip())
        match = DELTA.fullmatch(normalized) if line['confidence'] >= 60 else None
        if not match:
            flush()
            training_option = None
            continue
        if line_indices and 'top' in line and 'bottom' in lines[line_indices[-1]] and line['top']-lines[line_indices[-1]]['bottom']>30:
            # Separate cards even when OCR misses the heading between them.
            flush()
            training_option = None
        field = match[1].lower()
        if field.startswith('skill'):
            field = 'skill_points'
        if field in current:
            flush()
        current[field] = int(match[3]) * (1 if match[2].lower() == 'up' else -1)
        text.append(line['text'])
        line_indices.append(index)
    flush()
    return blocks


def track(report, directory, tesseract=None):
    reader = Reader(tesseract)
    readings = []
    for frame in report['frames']:
        reading = reader.stats(directory / frame['evidence'])
        readings.append(dict(reading, source_timestamp_ms=frame['source_timestamp_ms'], evidence=frame['evidence']))
    checkpoints = stable_checkpoints(readings)
    intervals = []
    log_cache = {}
    outcome_readings = []

    def blocks(frame):
        key = frame['evidence']
        if key not in log_cache:
            lines = reader.log(directory / key)
            log_cache[key] = {'source_timestamp_ms': frame['source_timestamp_ms'], 'evidence': key, 'lines': lines}
        return parse_log(log_cache[key]['lines'])

    def signature(block):
        return tuple(sorted(block['deltas'].items()))

    for before, after in zip(checkpoints, checkpoints[1:]):
        candidates = [f for f in report['frames'] if before['last_seen_ms'] <= f['source_timestamp_ms'] <= after['first_seen_ms']]
        baseline_blocks = blocks(candidates[0]) if candidates else []
        baseline = {signature(b) for b in baseline_blocks}
        seen, events = set(baseline), []
        raw_observations = []
        # Keep a provisional 1 fps arithmetic baseline for comparison, then use
        # ordered sampled context for final identity. Never infer from residuals.
        coarse, dense = [], []
        previous = -100000
        for frame in candidates[1:]:
            if frame['source_timestamp_ms']-previous >= 1000:
                coarse.append(frame)
                previous = frame['source_timestamp_ms']
            else:
                dense.append(frame)

        def inspect(frames):
            for frame in frames:
                for block in blocks(frame):
                    raw_observations.append(dict(block, evidence=frame['evidence'], observed_at_ms=frame['source_timestamp_ms'], origin='ocr_log_change'))
                    key = signature(block)
                    if key in seen:
                        if block['training_option']:
                            for event in events:
                                if signature(event)==key and not event['training_option']:
                                    event.update(training_option=block['training_option'], event_type=block['event_type'],
                                                 evidence=frame['evidence'], observed_at_ms=frame['source_timestamp_ms'])
                        continue
                    seen.add(key)
                    events.append(dict(block, evidence=frame['evidence'], observed_at_ms=frame['source_timestamp_ms'],
                                       origin='ocr_log_change', event_time_ms=None))

        inspect(coarse)
        initial = account(before, after, distinct_changes(events, baseline_blocks))
        # Ordered context and visible outcome spans need continuous samples even
        # when a provisional sum happens to balance on the coarse pass.
        searched_densely = True
        if searched_densely:
            inspect(dense)
            for frame in candidates[1:]:
                lines = reader.outcome(directory / frame['evidence'])
                outcome_readings.append(dict(source_timestamp_ms=frame['source_timestamp_ms'], evidence=frame['evidence'], lines=lines))
                for block in parse_log(lines):
                    raw_observations.append(dict(block, evidence=frame['evidence'], observed_at_ms=frame['source_timestamp_ms'], origin='ocr_main_outcome'))
                    if signature(block) not in seen:
                        seen.add(signature(block))
                        events.append(dict(block, evidence=frame['evidence'], observed_at_ms=frame['source_timestamp_ms'],
                                           origin='ocr_main_outcome', event_time_ms=None))
        events.sort(key=lambda e:e['observed_at_ms'])
        reconciliation = reconcile_changes(events, baseline_blocks)
        interval = account(before, after, reconciliation['events'])
        interval.update(initial_unexplained_change=initial['unexplained_change'],
                        dense_pass_performed=searched_densely, review_required=True,
                        raw_change_candidates=events,
                        baseline_blocks=baseline_blocks, raw_observations=raw_observations,
                        deduplication_decisions=reconciliation['decisions'],
                        investigation=dict(coarse_frames=len(coarse), dense_frames=len(dense) if searched_densely else 0,
                                           main_outcome_frames=len(candidates)-1 if searched_densely else 0,
                                           reason='context_identity_and_outcome_observation',
                                           additional_source_frames_decoded=0),
                        log_identity_warning='Identical delta blocks count once per interval; scroll identity and historical entries are not fully resolved.')
        intervals.append(interval)
    from .identity import refine_intervals
    intervals, ledger, episodes = refine_intervals(checkpoints,list(log_cache.values()),outcome_readings,intervals,parse_log)
    return {'enabled': True, 'method': 'tesseract_context_occurrence_accounting_v2', 'ocr_version': reader.version,
            'log_identity': ledger, 'outcome_episodes': episodes,
            'layout': 'english-landscape-1920x1080-v1', 'fields': list(FIELDS), 'readings': readings,
            'checkpoints': checkpoints, 'intervals': intervals, 'previews': preview_segments(readings),
            'coverage': dict(sampled_frames=len(readings), complete_readings=sum(all(type((r.get('values') or {}).get(f)) is int for f in FIELDS) for r in readings),
                             checkpoint_count=len(checkpoints), unverified_intervals=len(intervals)),
            'log_readings': list(log_cache.values()), 'outcome_readings': outcome_readings,
            'limitations': ['OCR consensus is not human verification.', 'Checkpoints are stable visible totals, not guaranteed turn boundaries.',
                            'Only explicit English log/main-outcome gains/losses are parsed; purchases, caps and scenario mechanics are not modeled.',
                            'Ordered context gives provisional log identity; identical histories after a gap can still be ambiguous.',
                            'Outcome observation spans are sampled visibility, not action or click timestamps. Cross-pane matches remain unverified.',
                            'Dense retry inspects existing sampled frames; it cannot recover screens absent from those samples.']}
