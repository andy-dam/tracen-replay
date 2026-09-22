"""The stat bar's fixed value rectangles and the receipt line that reports a change.

The geometry here is shared: ``vision`` opens these crops, and the modules
that refine or recover a value measure against them. Deciding that a frame
shows the bar is not done here. ``current_state_layout`` proves it from the
frame's own labels, values and caps, whatever colour the strip is painted.
"""

import re

BOXES = [(309,721,366,747), (406,721,463,747), (503,721,560,747),
         (598,721,655,747), (692,721,747,747), (755,721,836,760)]
DELTA = re.compile(r'^(Speed|Stamina|Power|Guts|Wit|Skill\s*(?:Pts|Points))\s+went\s+(up|down)\s+by\s+(\d+)\s*[.!]?$', re.I)


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
