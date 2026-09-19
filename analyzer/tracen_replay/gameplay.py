"""Gameplay-pane-only observations. No auxiliary log is an input to this module.

Screen facts, confirmation requests, receipts, and arithmetic are separate records.
Unknown effects never become invented transactions to close a residual.
"""
import json
import re
from pathlib import Path
from .stats import Reader
from .reconcile import FIELDS, stable_checkpoints, preview_segments, account
from .stat_receipt_grammar import normalize_fixed_stat_receipt
from .ocr_confidence import confidence_percent

PANE = (148, 0, 958, 1080)
CURRENCIES = ('dance', 'passion', 'vocal', 'visual', 'composure')
_PHRASE_SUBJECT = r'(Speed|Stamina|Power|Guts|Wit|Skill (?:Pts|Points)|Dance|Passion|Vocals?|Visuals?|Composure|Energy)'
_CORRUPT_VERB = re.compile(_PHRASE_SUBJECT + r' went (u[a-z]{0,3}|d[a-z]{0,4})\s*(?:b[a-z]?)?\s*(\d+)(?: to new heights)?[.!]?', re.I)
_CORRUPT_MAXED = re.compile(r'Friendship with (.+?) is ([A-Za-z]{3,7}) out[.!]?', re.I)


def _edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def repair_receipt_phrase(text):
    """Restore a fixed receipt phrase the cursor or a glyph merge corrupted.

    "Speed went uply 30." and "Wit went upby 10." keep their subject and
    amount; only the fixed "went up by" / "went down by" wording is restored.
    "is naxed out" is "is maxed out" within two edits. A missing amount or an
    unknown subject is not repaired.
    """
    if m := _CORRUPT_VERB.fullmatch(text):
        direction = 'up' if m[2].lower().startswith('u') else 'down'
        canonical = f"{m[1]} went {direction} by {m[3]}."
        if canonical.lower() != text.lower().rstrip('!').rstrip('.') + '.' and canonical.lower() != text.lower():
            return dict(text=canonical, text_normalization='verb_phrase_repair')
        return None
    if m := _CORRUPT_MAXED.fullmatch(text):
        word = m[2].lower()
        if word != 'maxed' and _edit_distance(word, 'maxed') <= 2:
            return dict(text=f"Friendship with {m[1]} is maxed out.", text_normalization='fixed_word_repair')
    return None


CHANGE = re.compile(r'^(Speed|Stamina|Power|Guts|Wit|Skill (?:Pts|Points)) went (up|down) by (\d+)(?: to new heights)?[.!]?$', re.I)
ITEM_DELIVERY = re.compile(r"Here(?:'s|’s) your (.+?)[.!]$", re.I)
AWARD_CONTEXT = re.compile(
    r'(?:^|[.!?]\s+)'
    r'(?!(?:[^.!?]*\b(?:if|unless|when|whether|had|would|could|should|might|may)\b))'
    r'(?!(?:[^.!?]*\b(?:never|not|haven(?:\'t|’t)|hasn(?:\'t|’t)|didn(?:\'t|’t))\b))'
    r'[^.!?]*\b(?:won|received|earned)\b.{0,80}\b(?:prize|reward)\b[.!?]$', re.I)


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


def _same_receipt_block(previous, current):
    """Check that two OCR lines are adjacent rows in one receipt panel."""
    previous_box, current_box = previous.get('box'), current.get('box')
    if not (isinstance(previous_box, (list, tuple)) and len(previous_box) == 4
            and isinstance(current_box, (list, tuple)) and len(current_box) == 4):
        return False
    previous_left, previous_top, previous_right, previous_bottom = previous_box
    current_left, current_top, current_right, current_bottom = current_box
    previous_center_y = (previous_top + previous_bottom) / 2
    current_center_y = (current_top + current_bottom) / 2
    if not 770 <= previous_center_y <= 1000 or not 770 <= current_center_y <= 1000:
        return False
    if not 0 < current_center_y - previous_center_y <= 45:
        return False
    # Wrapped receipt lines share a left edge even when their widths differ.
    return abs(current_left - previous_left) <= 15


def _has_item_delivery_context(lines, index):
    """Require a contiguous, high-confidence award sentence before an item.

    A generic ``Here's your ...`` line is common in narrative dialogue. The
    supported source receipt places it directly after an explicit award
    sentence, so only that local relationship can authorize the item effect.
    No item or character names are consulted.
    """
    if index <= 0:
        return False
    previous = lines[index - 1]
    current = lines[index]
    previous_confidence = confidence_percent(previous.get('confidence'))
    return (previous_confidence is not None and previous_confidence >= 95
            and AWARD_CONTEXT.search(previous.get('text', '').strip())
            and _same_receipt_block(previous, current))


def _malformed_song_receipt(text):
    """Reject clipped song headings from the generic ``Learned`` grammar.

    A missing word in ``Learned the song ...`` must not turn the whole quoted
    sentence into a generic named acquisition.  The exact song grammar above
    remains the only path that emits ``song_learned``; this helper only closes
    the fallback path when the source still visibly contains a quoted song
    phrase.
    """
    if not isinstance(text, str) or not re.match(r'^Learned\b', text, re.I):
        return False
    # A named acquisition never carries a quoted title; any quoted phrase after
    # 'Learned' is a song receipt whose fixed words were misread.
    if not re.search(r'["“].+?["”]', text):
        return False
    return re.fullmatch(r'Learned the song ["“].+?["”][.!]?', text, re.I) is None


_SONG_PHRASE = 'learned the song'
_SONG_PHRASE_DISTANCE = 5


def garbled_song_receipt(text):
    """The quoted title of a song receipt whose fixed phrase was misread.

    The quoted title anchors the line; the words before the opening quote are
    accepted when they lie within a bounded edit distance of ``Learned the
    song`` ('Learnd te ong', 'Learned the' with the word dropped). Other
    quoted receipts ('Acquired "..."') are far from the phrase and untouched.
    Returns the title text, or None.
    """
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r'\s*([A-Za-z][A-Za-z ]{5,24}?)\s*["“](.+?)["”][.!]?\s*', text)
    if not match:
        return None
    prefix = ' '.join(match[1].casefold().split())
    if prefix == _SONG_PHRASE or _edit_distance(prefix, _SONG_PHRASE) > _SONG_PHRASE_DISTANCE:
        return None
    title = match[2].strip()
    return title or None


# The same receipt with its number missing or garbled: the number is drawn in
# a colour the recognizer drops, or comes back as letters ("T0").
CUT_CHANGE = re.compile(r'^(Speed|Stamina|Power|Guts|Wit|Skill (?:Pts|Points)) went (up|down) by\b(.*)$', re.I)
_GAIN_POPUP = re.compile(r'^([+-])(\d{1,3})[!.]?$')
_POPUP_LABELS = {'skill_points': ('skill pts', 'skill points')}


def gain_popup(lines, field, direction):
    """The one tall signed popup on this frame whose label names ``field``.

    While a receipt shows, the game floats the gain over the scene as a tall
    "+N" with the stat's name in a line just under it. The popup counts only
    when exactly one on the frame names this stat and its sign agrees with
    the receipt; the amount is then read, not worked out.
    """
    labels = _POPUP_LABELS.get(field, (field,))
    sign = '+' if direction == 'up' else '-'
    found = []
    for line in lines:
        box = line.get('box')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        confidence = confidence_percent(line.get('confidence'))
        if confidence is None or confidence < 90:
            continue
        match = _GAIN_POPUP.fullmatch(re.sub(r'\s+', '', str(line.get('text', ''))))
        if not match or match[1] != sign or box[3] - box[1] < 60:
            continue
        for label in lines:
            label_box = label.get('box')
            if label is line or not isinstance(label_box, (list, tuple)) or len(label_box) != 4:
                continue
            label_confidence = confidence_percent(label.get('confidence'))
            if (label_confidence is None or label_confidence < 90
                    or str(label.get('text', '')).strip().casefold() not in labels):
                continue
            # The name sits just under the number and overlaps it sideways.
            if not (box[3] - 10 <= label_box[1] <= box[3] + 40):
                continue
            if label_box[2] < box[0] - 40 or label_box[0] > box[2] + 40:
                continue
            found.append(dict(amount=int(match[2]),
                              popup=dict(text=line.get('text'), confidence=line.get('confidence'), box=list(box)),
                              label=dict(text=label.get('text'), confidence=label.get('confidence'), box=list(label_box))))
            break
    return found[0] if len(found) == 1 else None


def cut_receipt_lines(lines, band, low=90, high=95):
    """Receipt-band lines under the band's confidence gate that a gain popup vouches for.

    A receipt that lost its number tends to read a little under the gate.
    When a popup on the same frame names its stat with the sign it states,
    the line is a receipt and belongs in the band.
    """
    extra = []
    for line in lines:
        confidence = confidence_percent(line.get('confidence'))
        box = line.get('box')
        if (confidence is None or not (low <= confidence < high)
                or not isinstance(box, (list, tuple)) or len(box) != 4):
            continue
        centre_x, centre_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if not (band[0] <= centre_x <= band[2] and band[1] <= centre_y <= band[3]):
            continue
        m = CUT_CHANGE.fullmatch(str(line.get('text', '')).strip())
        # A line that read a clean number is not cut; the gate stands for it.
        if not m or re.fullmatch(r'\s*\d+(?: to new heights)?[.!]?\s*', m[3]):
            continue
        if gain_popup(lines, 'skill_points' if m[1].lower().startswith('skill') else m[1].lower(), m[2].lower()):
            extra.append(line)
    return extra


def effects_from_lines(lines, popups=None):
    """Only explicit past-tense receipts; plus signs in previews do not qualify.

    ``popups`` is the frame's whole line list when ``lines`` is only the
    receipt band, so a receipt that lost its number can find the gain popup
    floating above the band.
    """
    effects = []
    for index, line in enumerate(lines):
        confidence = confidence_percent(line.get('confidence'))
        if confidence is None or confidence < 60:
            continue
        text_value = line.get('text')
        if not isinstance(text_value, str):
            continue
        original_text = text_value.strip()
        fixed_receipt = (normalize_fixed_stat_receipt(original_text)
                         if confidence >= 90 else None)
        text = fixed_receipt['text'] if fixed_receipt else original_text
        # Missing whitespace at an explicit award verb/amount boundary is
        # formatting, not evidence for changing digits or recipient names.
        text = re.sub(r'\b((?:went (?:up|down)|recovered|increased) by)(?=\d)',r'\1 ',text)
        repaired = repair_receipt_phrase(text) if confidence >= 90 and not fixed_receipt else None
        if repaired:
            text = repaired['text']
        effect = None
        if m := CHANGE.fullmatch(text):
            field = 'skill_points' if m[1].lower().startswith('skill') else m[1].lower()
            effect = dict(kind='stat_change', field=field, amount=int(m[3]) * (1 if m[2].lower() == 'up' else -1))
        elif m := CUT_CHANGE.fullmatch(text):
            # The line's number is missing or not a number ("T0"); the popup
            # over the scene shows it.
            field = 'skill_points' if m[1].lower().startswith('skill') else m[1].lower()
            popup = gain_popup(popups if popups is not None else lines, field, m[2].lower())
            if popup:
                effect = dict(kind='stat_change', field=field,
                              amount=popup['amount'] * (1 if m[2].lower() == 'up' else -1),
                              amount_basis='gain_popup_on_same_frame',
                              gain_popup=popup['popup'], gain_popup_label=popup['label'])
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
        elif m := re.fullmatch(r'Recovered from ([^.!?]+\S)[.!]',text,re.I):
            # The complete receipt supplies the condition name. A clipped
            # prefix must not be completed from the action or a name catalog.
            effect=dict(kind='condition_removed',name=m[1].strip(),mechanical_effect=None)
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
        # RapidOCR can omit the terminal punctuation from an otherwise
        # complete level-up receipt.  The phrase itself is the receipt; keep
        # the name unconstrained and require the complete ``leveled up`` verb.
        elif m := re.fullmatch(r'(.+?) leveled up[.!]?',text,re.I):
            effect=dict(kind='skill_level_change',name=m[1],direction='up',amount=None,levels_observed=False)
        elif m := re.fullmatch(r'Friendship with (.+?) is maxed out[.!]?',text,re.I):
            effect=dict(kind='friendship_status',name=m[1],value='maximum',amount=None)
        elif m := re.fullmatch(r"Friendship with (.+?) didn't go up[.!]?",text,re.I):
            effect=dict(kind='friendship_status',name=m[1],value='unchanged',amount=0)
        elif m := re.fullmatch(r'Friendship with (.+?) went up by (\d+)[.!]?', text, re.I):
            effect = dict(kind='friendship_change', name=m[1], amount=int(m[2]))
        elif m := re.fullmatch(r'Gained (\d+) hint level\(s\) for (.+?)[.!]?', text, re.I):
            # The pattern already consumes one sentence terminator. Keep
            # preceding punctuation that belongs to the skill name itself.
            name = m[2].strip()
            effect = dict(kind='skill_hint_change', name=name, amount=int(m[1]))
        elif m := re.fullmatch(r'(Dance|Passion|Vocals?|Visuals?|Composure) went (up|down) by (\d+)[.!]?', text, re.I):
            field = {'vocals':'vocal','visuals':'visual'}.get(m[1].lower(),m[1].lower())
            effect = dict(kind='performance_change', field=field, amount=int(m[3])*(1 if m[2].lower()=='up' else -1))
        elif m := re.fullmatch(r'Learned the song ["“](.+?)["”][.!]?', text, re.I):
            effect = dict(kind='song_learned', name=m[1].strip(), acquisition='unknown', cost=None)
        elif confidence >= 90 and (title := garbled_song_receipt(text)):
            # The fixed phrase was misread ('Learnd te ong "Title".'); the quoted
            # title is the receipt and the phrase is repaired by bounded distance.
            effect = dict(kind='song_learned', name=title, acquisition='unknown', cost=None,
                          text_normalization='fixed_phrase_repair')
        elif m := re.fullmatch(r'Learned (.+?)[.!]', text, re.I):
            if _malformed_song_receipt(text):
                # The line is visibly song-shaped but its fixed receipt
                # keyword is incomplete.  Leave it unresolved instead of
                # manufacturing a named acquisition from the clipped text.
                m = None
            else:
                effect = dict(kind='named_acquisition', name=m[1], acquisition='unknown', cost=None)
        elif _has_item_delivery_context(lines, index) and (m := ITEM_DELIVERY.fullmatch(text)):
            # Generic named item delivery.  The receipt exposes the item
            # wording but does not establish a quantity or inventory delta.
            effect = dict(kind='item_reward', name=m[1].strip(), quantity=None,
                          quantity_observed=False)
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
            # Keep the recognizer's actual text in raw_text even when a
            # bounded fixed-label repair enabled parsing.  Consumers that
            # need the canonical spelling can use normalized_text; raw_text
            # remains the source observation for provenance and exact-match
            # consumers.
            parsed = dict(effect, raw_text=original_text,
                          confidence=line['confidence'])
            if fixed_receipt:
                parsed.update(normalized_text=text,
                              original_text=original_text,
                              text_normalization=fixed_receipt['text_normalization'])
            elif repaired:
                parsed.update(normalized_text=text,
                              original_text=original_text,
                              text_normalization=repaired['text_normalization'])
            effects.append(parsed)
    return effects


def preview_effects(lines, *, typed=False):
    """Parse visible lesson effects, optionally retaining typed fields.

    The legacy collection is also used for a broad ``available_effects``
    summary on lesson menus.  Those entries are intentionally left as
    parser-owned kind/raw-text facts by default; the richer lesson-card
    adapter supplies the offer identity and typed fields for that screen.
    Confirmation parsing opts into ``typed`` because its same-frame projected
    row has no card envelope to provide the field mapping.
    """
    result = []
    seen = set()
    for line in lines:
        text = line.get('text')
        confidence = confidence_percent(line.get('confidence'))
        if not isinstance(text, str) or confidence is None or confidence < 60 or text in seen:
            continue
        seen.add(text)
        kind = None
        if re.search(r'\bTraining (Speed|Stamina|Power|Guts|Wit) Gain\b',text,re.I):
            kind = 'future_training_modifier'
        elif re.search(r'Friendship Training Effectiveness|Support Chain Event Frequency|Specialty Priority',text,re.I):
            kind = 'queued_concert_bonus'
        elif m := re.fullmatch(r'(Speed|Stamina|Power|Guts|Wit|Skill Pts|Energy)\s*\+\s*(\d+)',text,re.I):
            # This is a parser-owned, same-frame menu/confirmation label.
            # Keep the typed field and amount with the fact so preview
            # consumers never need to reinterpret raw text.  Energy is a
            # distinct effect kind; it is a preview projection until a later
            # committed receipt proves an applied energy change.
            label = m[1].casefold()
            field = {
                'skill pts': 'skill_points',
                'energy': 'energy',
            }.get(label, label)
            if field == 'energy' and not typed:
                # Broad lesson-menu summaries do not own an offer identity;
                # the card adapter will type an Energy row only after it has
                # associated the row with the card and source geometry.
                continue
            kind = 'energy_change' if field == 'energy' else 'immediate_on_purchase'
            typed_amount = int(m[2])
        if kind:
            effect = dict(kind=kind, raw_text=text, awarded=False)
            if typed and kind == 'energy_change':
                effect['field'] = 'energy'
                effect['amount'] = typed_amount
            elif typed and kind == 'immediate_on_purchase':
                effect['field'] = field
                effect['amount'] = typed_amount
            result.append(effect)
    return result


def classify(text, header, result_grid=False, preview=False):
    """Priority prevents underlying dimmed screens overriding modal semantics."""
    lower = text.lower()
    if 'take the day off to let your trainee recover energy?' in lower and 'entire turn' in lower:
        return 'rest_confirmation'
    if 'visit the infirmary?' in lower and 'entire turn' in lower:
        return 'infirmary_confirmation'
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
    if 'total fans' in lower and ('bond level' in lower or 'fans earned this month' in lower):
        return 'career_account_totals'
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
            facts['projected_effects'] = preview_effects(
                [x for rows in regions.values() for x in rows], typed=True
            )
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
