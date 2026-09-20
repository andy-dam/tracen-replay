"""Local neural OCR observations; visual evidence and semantic parsing stay separate."""
import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
from .gameplay import PANE,CURRENCIES,classify,effects_from_lines,preview_effects
from .crop_provenance import resolve_gain_regions,source_amounts
from .reconcile import FIELDS
from .stats import BOXES
from .preview_recovery import VERSION as PREVIEW_RECOVERY_VERSION
from .training_badge_localization import VERSION as TRAINING_BADGE_LOCALIZATION_VERSION
from .training_gain_source_refinement import VERSION as TRAINING_GAIN_SOURCE_REFINEMENT_VERSION
from .training_result_layout import VERSION as TRAINING_RESULT_LAYOUT_VERSION
from .training_result_layout import detect_training_result_layout
from .current_state_layout import VERSION as CURRENT_STATE_LAYOUT_VERSION
from .current_state_layout import detect_current_state_layout

def ocr_device():
    """Return the recognition device: ``dml`` (DirectML GPU), ``cuda`` or ``cpu``.

    ``TRACEN_REPLAY_OCR_DEVICE`` forces ``cpu``, ``dml`` or ``cuda``; ``auto``
    (the default) selects DirectML, then CUDA, whenever the installed
    onnxruntime offers it, and falls back to the CPU provider otherwise.
    DirectML sessions are not safe to run from several threads of one
    process, so ``full_recording.analyze_frames`` gives every reader its own
    process when this returns ``dml``.
    """
    requested = os.environ.get('TRACEN_REPLAY_OCR_DEVICE', 'auto').strip().lower()
    if requested not in ('auto', 'cpu', 'dml', 'cuda'):
        raise ValueError(f'unsupported TRACEN_REPLAY_OCR_DEVICE {requested!r}; use auto, cpu, dml or cuda')
    if requested == 'cpu':
        return 'cpu'
    try:
        import onnxruntime
        providers = set(onnxruntime.get_available_providers())
    except Exception:  # pragma: no cover - onnxruntime is optional at import time
        providers = set()
    available = {'dml': 'DmlExecutionProvider' in providers, 'cuda': 'CUDAExecutionProvider' in providers}
    if requested in ('dml', 'cuda'):
        if not available[requested]:
            raise ValueError(f'TRACEN_REPLAY_OCR_DEVICE={requested} but onnxruntime has no matching execution provider')
        return requested
    # auto: DirectML (Windows), then CUDA (Linux containers), then the CPU
    # provider, which runs the same models more slowly on any machine.
    for device in ('dml', 'cuda'):
        if available[device]:
            return device
    return 'cpu'


def engine_params(device):
    """RapidOCR engine parameters for ``device``.

    The parameters are part of every reader's cache fingerprint, so the
    DirectML and CPU parameter sets stay exactly what earlier runs wrote:
    the CUDA switch is only present when CUDA is the selected device.
    """
    params = {'Global.use_cls':False,'Det.limit_side_len':736,'Det.limit_type':'max',
              'EngineConfig.onnxruntime.intra_op_num_threads':2,
              'EngineConfig.onnxruntime.inter_op_num_threads':1,'Global.log_level':'warning',
              'EngineConfig.onnxruntime.use_dml':device=='dml'}
    if device == 'cuda':
        params['EngineConfig.onnxruntime.use_cuda'] = True
    return params


OCR_DEVICE = ocr_device()
PARAMS = engine_params(OCR_DEVICE)


ANIMATION_BADGE_ROWS = {'top': ('speed', 'stamina', 'power'), 'bottom': ('guts', 'wit', 'skill_points')}
_ANIMATION_BADGE_PATTERN = re.compile(r'\+(\d{1,3})[!.]?')


def animation_gain_badges(lines):
    """Large signed badges shown while a training result animates in.

    A player who skips the result animation leaves the ordinary small gain
    badges on screen for only a few frames; during the animation the same
    amounts are drawn as large "+N" badges that sweep across the stat cards.
    This helper only observes such badges: text ``+N`` (an exclamation mark
    is part of the settling animation), high confidence, and a box far taller
    than any resting badge, lying over the result card rows.  It does not
    assign a badge to a stat: the animation moves the badge across the row,
    so the owning card is resolved later against the result totals.
    """
    found = []
    for line in lines or ():
        if not isinstance(line, dict) or not isinstance(line.get('box'), (list, tuple)) or len(line['box']) != 4:
            continue
        text = re.sub(r'\s+', '', str(line.get('text', '')))
        match = _ANIMATION_BADGE_PATTERN.fullmatch(text)
        if not match or line.get('confidence', 0) < 95:
            continue
        left, top, right, bottom = (float(v) for v in line['box'])
        height, width = bottom - top, right - left
        if not (90 <= height <= 200 and 80 <= width <= 430):
            continue
        cy = (top + bottom) / 2
        cx = (left + right) / 2
        if not (760 <= cy <= 1040 and 200 <= cx <= 900):
            continue
        found.append(dict(amount=int(match[1]), text=line.get('text'), confidence=line.get('confidence'),
                          box=[left, top, right, bottom], row='top' if cy < 935 else 'bottom'))
    return found


def _word_box(quad):
    """One recognized word's corner points as a box in gameplay coordinates."""
    xs=[float(point[0]) for point in quad];ys=[float(point[1]) for point in quad]
    return [round(min(xs))+148,round(min(ys)),round(max(xs))+148,round(max(ys))]


def within(line,box):
    left,top,right,bottom=line['box']
    return box[0] <= (left+right)/2 <= box[2] and box[1] <= (top+bottom)/2 <= box[3]


def _normalize_crop_box(box):
    """Return one canonical integer crop box in full gameplay coordinates.

    OCR request geometry is expressed in the full gameplay pane while NumPy
    slices require integer bounds.  Preserve every source pixel touched by a
    valid fractional box by flooring the upper-left corner and ceiling the
    lower-right corner.  The canonical result is also retained in the region
    metadata, so downstream crop proofs and hashes address the exact same
    pixels that recognition received.  Out-of-bounds geometry is rejected
    rather than silently clamped to a different source region.
    """
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError('OCR crop box must contain four coordinates.')
    if any(type(value) not in (int, float) for value in box):
        raise ValueError('OCR crop box coordinates must be numeric.')
    coordinates = tuple(float(value) for value in box)
    if not all(math.isfinite(value) for value in coordinates):
        raise ValueError('OCR crop box coordinates must be finite.')
    left, top, right, bottom = coordinates
    if not left < right or not top < bottom:
        raise ValueError('OCR crop box must have positive width and height.')
    if (left < PANE[0] or top < PANE[1]
            or right > PANE[2] or bottom > PANE[3]):
        raise ValueError('OCR crop box lies outside the gameplay pane.')
    normalized = [math.floor(left), math.floor(top),
                  math.ceil(right), math.ceil(bottom)]
    if (normalized[0] < PANE[0] or normalized[1] < PANE[1]
            or normalized[2] > PANE[2] or normalized[3] > PANE[3]
            or normalized[0] >= normalized[2]
            or normalized[1] >= normalized[3]):
        raise ValueError('OCR crop box cannot be represented in the gameplay pane.')
    return normalized


def _training_gain_refinement_fields(regions):
    """Select only unresolved native gain fields for relative source OCR.

    A strong native gain or an already resolved source crop suppresses the
    supplemental pass.  Existing competing complete readings also stay in the
    ordinary resolver, where its conflict state remains visible; the bounded
    reread must not create a third vote that changes that decision.
    """

    if not isinstance(regions, dict):
        return []
    signed = re.compile(r"^\s*\+\s*\d{1,3}\s*$")
    selected = []
    for field in FIELDS:
        native = regions.get("gain." + field)
        if isinstance(native, dict):
            text = str(native.get("text", ""))
            try:
                confidence = float(native.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            # Preserve an established native amount, including when another
            # crop has made the field's ordinary resolution ambiguous.
            if signed.fullmatch(text) and confidence >= 98.0:
                continue
        resolution = resolve_gain_regions(regions, field)
        if type(resolution.get("canonical_amount")) is int:
            continue
        selected.append(field)
    return selected


_RACE_RESULT_HEADER_BOX = (280, 425, 810, 456)
_RACE_GRADE_RE = re.compile(r'(?:DEBUT|G[123]|OP|PRE[- ]?OP|EX)', re.I)


def _canonical_race_grade(value):
    """Normalize one exact race-result grade token, or keep it unknown."""

    if not isinstance(value, str):
        return None
    value = re.sub(r'\s+', ' ', value.strip()).upper()
    if value == 'PRE OP':
        value = 'PRE-OP'
    return value if _RACE_GRADE_RE.fullmatch(value) else None


# A header line that starts with a grade and a space is a grade and a title.
# A grade that ends in a digit can also arrive glued to the title, the reader
# having missed the gap ("G1Hopeful Stakes"); a letter grade glued to letters
# (OPEN, EXtra) is a word, so those still need the space.
# The grade badge is read glued to a capitalised title as often as spaced
# from it ("G1Arima Kinen", "DEBUTJunior Make Debut"). A badge that is
# itself a word's start ("OP" in "OPEN Cup") still needs its space.
_RACE_GRADE_PREFIX_RE = re.compile(r'^(?:(DEBUT|G[123]|OP|PRE[- ]?OP|EX)\s+|(G[123]|DEBUT|EX)(?=(?-i:[A-Z])))(.+)$', re.I)


def _split_race_grade(text):
    """The header line's grade and title, or ``(None, text)`` when it has no grade prefix."""
    match = _RACE_GRADE_PREFIX_RE.match(text)
    if not match:
        return None, text
    grade = _canonical_race_grade(match.group(1) or match.group(2))
    title = match.group(3).strip()
    return (grade, title) if grade is not None and title else (None, text)


def _valid_race_result_header_line(line):
    """Validate one OCR line before the race-header geometry is consulted."""

    if not isinstance(line, dict):
        return False
    confidence = line.get('confidence')
    box = line.get('box')
    if type(confidence) not in (int, float):
        return False
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    if any(type(value) not in (int, float) for value in box):
        return False
    try:
        confidence = float(confidence)
        coordinates = tuple(float(value) for value in box)
    except (OverflowError, TypeError, ValueError):
        return False
    if not math.isfinite(confidence) or not all(math.isfinite(value) for value in coordinates):
        return False
    left, top, right, bottom = coordinates
    if (left < 0 or top < 0 or right <= left or bottom <= top
            or not within(dict(line, box=coordinates), _RACE_RESULT_HEADER_BOX)):
        return False
    return True


def _race_result_grade_observation(lines):
    """Read a grade only when one result-header title owns the same token.

    Race-list cards can show several grades at once.  This helper is called
    only for a classified result panel and still requires one exact grade and
    one associated title in the result header band.  A distant, duplicated,
    malformed, or competing token remains unknown.
    """

    grade_lines = []
    title_lines = []
    for line in lines:
        if (not _valid_race_result_header_line(line)
                or line['confidence'] < 95):
            continue
        text = re.sub(r'\s+', ' ', str(line.get('text', '')).strip())
        if not text:
            continue
        grade = _canonical_race_grade(text)
        if grade is not None:
            grade_lines.append((grade, line))
            continue
        grade, title = _split_race_grade(text)
        if grade is not None:
            grade_lines.append((grade, line))
            title_lines.append((title, line))
            continue
        if any(char.isalpha() for char in text):
            title_lines.append((text, line))

    if len(grade_lines) != 1:
        return None, None
    grade, grade_line = grade_lines[0]
    associated = []
    for title, title_line in title_lines:
        if title_line is grade_line:
            associated.append((title, title_line, 0.0, 0.0))
            continue
        grade_left, grade_top, grade_right, grade_bottom = grade_line['box']
        title_left, title_top, title_right, title_bottom = title_line['box']
        vertical_delta = abs((grade_top + grade_bottom) / 2 -
                             (title_top + title_bottom) / 2)
        horizontal_gap = max(grade_left - title_right, title_left - grade_right, 0)
        if vertical_delta <= 18 and horizontal_gap <= 32:
            associated.append((title, title_line, vertical_delta, horizontal_gap))
    if len(associated) != 1:
        return None, None
    title, title_line, vertical_delta, horizontal_gap = associated[0]
    return grade, dict(
        basis='result_header_grade_title_geometry',
        grade=dict(text=grade_line.get('text', ''), confidence=grade_line.get('confidence'),
                   box=list(grade_line.get('box', []))),
        title=dict(text=title, confidence=title_line.get('confidence'),
                   box=list(title_line.get('box', []))),
        geometry=dict(vertical_delta_px=vertical_delta, horizontal_gap_px=horizontal_gap),
    )


def number(observation,minimum=97):
    if not isinstance(observation,dict):
        return None
    text=observation.get('text','').strip()
    confidence=observation.get('confidence',0)
    return int(text) if re.fullmatch(r'\d{1,4}',text) and confidence>=minimum else None


def terminal_skill_point_observation(lines):
    """Read one labeled completion-modal skill-point counter.

    The label is a fixed modal anchor and may be a little less confident than
    the numeric counter.  Values require the original 97 confidence floor and
    one unique label/value pair; duplicates remain unresolved.
    """
    labels=[line for line in lines if isinstance(line,dict)
            and line.get('confidence',0)>=90
            and line.get('text','').strip()=='Remaining Skill Points'
            and within(line,(400,550,590,600))]
    if len(labels)!=1:
        return False,None,None
    matches=[]
    for line in lines:
        if not isinstance(line,dict) or line.get('confidence',0)<97:
            continue
        if not within(line,(585,550,710,600)):
            continue
        match=re.fullmatch(r'(\d{1,4})\s*pt\(s\)',line.get('text','').strip())
        if match:
            matches.append((int(match[1]),line))
    values={value for value,_line in matches}
    if len(values)!=1:
        return True,None,None
    value,line=matches[0]
    proof=dict(basis='same_modal_label_counter',
               label=dict(text=labels[0].get('text',''),confidence=labels[0].get('confidence'),box=list(labels[0].get('box',[]))),
               counter=dict(text=line.get('text',''),confidence=line.get('confidence'),box=list(line.get('box',[]))))
    return True,value,proof


def training_preview(raw):
    """Recognize the failure popup above any selected training tab.

    Its percentage can be occluded or mid-animation. The fixed label, training
    header and current-stat layout establish a preview, not an awarded gain.
    """
    recovery = raw.get('preview_recovery') if isinstance(raw, dict) else None
    if isinstance(recovery, dict):
        from .preview_recovery import validated_recovery
        recovery = validated_recovery(raw)
    phase = recovery.get('phase') if isinstance(recovery, dict) else None
    if (isinstance(phase, dict) and phase.get('menu_proven') is True
            and phase.get('result_proven') is False):
        return True
    if not raw['current_grid'] or raw['header'].strip().lower()!='training':return False
    for line in raw['lines']:
        if not _failure_banner_word(line['text']) or line['confidence']<90:continue
        left,top,right,bottom=line['box']
        if (250<=left<right<=850 and 750<=top<bottom<=850
            and right-left<=110 and bottom-top<=45):return True
    return False


class CompactInputEngine:
    """The RapidOCR engine, always handed an image as one compact array.

    Callers pass BGR views flipped from RGB arrays. The engine cuts every text
    box it detects out of the image with OpenCV, and OpenCV copies a flipped
    view in full for each cut: dozens of whole-pane copies per frame, which took
    longer than detection and recognition together. The same pixels in one
    compact array read identically.
    """

    def __init__(self, engine):
        self._engine = engine

    def __call__(self, img, *args, **kwargs):
        import numpy as np
        if isinstance(img, np.ndarray):
            img = np.ascontiguousarray(img)
        return self._engine(img, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._engine, name)


# The five boxes under the stat labels where the bar's coloured strip shows,
# in frame coordinates, and the fraction of their pixels that must be a
# saturated colour (not white, grey or black) for the frame to be tried as
# the current stat bar.
STRIP_PROBE_BOXES=tuple((x,700,x+35,719) for x in (310,410,510,610,710))
STRIP_PROBE_MIN=.35


def _strip_saturation(colors):
    """The fraction of a crop's pixels that carry a colour.

    A pixel counts when its channels differ by more than 40 and the brightest
    is above 120: the strip in any theme colour passes, and the white label
    text, a grey panel or a dark scene does not. Which colour it is does not
    matter, and never did: the value crops read the numbers.
    """
    colors=colors.astype('int16')
    spread=colors.max(axis=2)-colors.min(axis=2)
    return float(((spread>40)&(colors.max(axis=2)>120)).mean())


class NeuralReader:
    def __init__(self,model_dir='.local/models/rapidocr'):
        from rapidocr import RapidOCR,OCRVersion,ModelType,LangRec
        import numpy as np
        from PIL import Image,ImageOps
        from rapidocr.ch_ppocr_rec import TextRecInput
        self.np,self.Image,self.ImageOps,self.TextRecInput=np,Image,ImageOps,TextRecInput
        self.engine=CompactInputEngine(RapidOCR(params=dict(PARAMS,**{'Global.model_root_dir':str(model_dir),
            'Rec.lang_type':LangRec.EN,'Rec.ocr_version':OCRVersion.PPOCRV5,'Rec.model_type':ModelType.MOBILE})))
        self.models={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(model_dir).glob('*.onnx') if p.name in ('PP-OCRv6_det_small.onnx','en_PP-OCRv5_rec_mobile.onnx')}
        from .code_identity import function_digest
        component_code = b''.join(
            function_digest(globals()[name])
            for name in ('_performance_panel_line_eligible',
                         '_performance_panel_component_requests',
                         '_performance_panel_localized_requests',
                         '_performance_panel_cap_requests',
                         '_main_stat_cap_requests')
            if name in globals())
        self.fingerprint=hashlib.sha256(json.dumps([PARAMS,self.models,PREVIEW_RECOVERY_VERSION],sort_keys=True).encode()+
            function_digest(type(self).read)+component_code+
            function_digest(type(self)._localize_training_badges)+
            function_digest(detect_training_result_layout)+
            str(TRAINING_BADGE_LOCALIZATION_VERSION).encode()+
            str(TRAINING_GAIN_SOURCE_REFINEMENT_VERSION).encode()+
            str(TRAINING_RESULT_LAYOUT_VERSION).encode()+
            function_digest(detect_current_state_layout)+
            str(CURRENT_STATE_LAYOUT_VERSION).encode()+
            b'rapidocr3.9.2-en-v5-v3').hexdigest()

    def _localize_training_badges(self, pane, *, header, result_grid,
                                  screen=None, blocked_boxes=None):
        """Run the bounded source-pixel badge pass for one result pane.

        The normal reader supplies BGR crops to RapidOCR.  The localizer keeps
        its proof in decoded RGB coordinates, so only the injected recognizer
        performs the representation conversion.  An unresolved discovery is
        retained as diagnostics and never changes the ordinary regions.
        """
        from .training_badge_localization import localize_training_badges

        def recognize(crops):
            if not crops:
                return []
            response = self.engine.text_rec(
                self.TextRecInput(img=[crop[:, :, ::-1] for crop in crops])
            )
            return [
                {"text": text, "confidence": float(score)}
                for text, score in zip(response.txts, response.scores)
            ]

        return localize_training_badges(
            pane,
            recognize=recognize,
            header=header,
            result_grid=result_grid,
            screen=screen,
            blocked_boxes=blocked_boxes,
        )

    def _refine_training_gain_regions(self, pane, *, fields, header,
                                      result_grid, preview=False):
        """Run relative source crops for fields lacking a native resolution."""
        from .training_gain_source_refinement import refine_training_gain_regions

        def recognize(crops):
            if not crops:
                return []
            response = self.engine.text_rec(
                self.TextRecInput(img=[crop[:, :, ::-1] for crop in crops])
            )
            return [
                {"text": text, "confidence": float(score)}
                for text, score in zip(response.txts, response.scores)
            ]

        return refine_training_gain_regions(
            pane,
            recognize=recognize,
            fields=fields,
            header=header,
            result_grid=result_grid,
            preview=preview,
            source_metadata={"engine_fingerprint": self.fingerprint},
        )

    def read_training(self,pane):
        """Inspect result crops and attach a typed panel when its menu is visible."""
        if pane.size!=(810,1080):raise ValueError('Expected the gameplay crop.')
        array=self.np.array(pane.convert('RGB'))
        def crop(b):
            left, top, right, bottom = _normalize_crop_box(b)
            return array[top:bottom,left-PANE[0]:right-PANE[0],::-1]
        requests=[('header',(155,0,250,29)),('option',(220,162,400,198))]
        blue=[]
        for x in (270,470):
            c=crop((x,911,x+35,934)).astype('int16')
            blue.append(float(((c[:,:,0]>c[:,:,2]+25)&(c[:,:,1]>c[:,:,2]+15)&(c[:,:,0]>100)).mean()))
        grid=min(blue)>.45
        boxes=[(300,832,414,890),(498,832,610,890),(696,832,812,890),(300,950,414,1008),(498,950,610,1008),(696,950,812,1008)]
        requests += [('gain.'+f,b) for f,b in zip(FIELDS,boxes)]
        requests += [('wide_gain.'+f,((250,448,646)[i%3],812 if i<3 else 930,(462,660,858)[i%3],909 if i<3 else 1027)) for i,f in enumerate(FIELDS)]
        requests += [('result.'+f,((322,518,714)[i%3],834 if i<3 else 952,(322,518,714)[i%3]+126,876 if i<3 else 994)) for i,f in enumerate(FIELDS)]
        requests += [('result.skill_points',(708,952,810,990))]
        # The performance sidebar remains visible while the lower result cards
        # animate.  Keep these signed rows as typed source regions so the
        # preview producer can distinguish them from result totals.
        requests += [('performance_gain.'+f,(245,296+56*i,319,333+56*i))
                     for i,f in enumerate(CURRENCIES)]
        requests=[(name,_normalize_crop_box(box)) for name,box in requests]
        result=self.engine.text_rec(self.TextRecInput(img=[crop(b) for _,b in requests]))
        regions={name:dict(text=text,confidence=round(float(score)*100,4),box=list(box)) for (name,box),text,score in zip(requests,result.txts,result.scores)}
        header=regions['header']['text'] if regions['header']['confidence']>=95 else ''
        totals=sum(bool(re.fullmatch(r'\d{1,4}/\d{3,4}',regions['result.'+f]['text'])) and regions['result.'+f]['confidence']>=95 for f in FIELDS[:5])
        result_grid=(grid or totals>=3) and header.lower().startswith('training')
        # A translucent transition can lower the fixed header confidence below
        # the ordinary 95 floor while leaving the exact Training label and one
        # result-card ratio readable.  Keep the same source-panel gate for the
        # bounded relative reread, but allow that conservative header floor.
        header_observation=regions.get('header',{})
        header_proof=header
        if (not header_proof and isinstance(header_observation,dict)
                and str(header_observation.get('text','')).strip().casefold()=='training'
                and header_observation.get('confidence',0)>=85):
            header_proof='Training'
        result_panel_proof=grid or any(
            bool(re.fullmatch(r'\d{1,4}/\d{3,4}',str(regions.get('result.'+f,{}).get('text','')).strip()))
            and regions.get('result.'+f,{}).get('confidence',0)>=90
            for f in FIELDS[:5]
        )
        refinement=None
        refinement_fields=_training_gain_refinement_fields(regions)
        preview_hint=any(
            _failure_banner_word(line.get('text'))
            and line.get('confidence',0)>=90
            for line in (regions.values() if isinstance(regions,dict) else ())
            if isinstance(line,dict)
        )
        if header_proof and result_panel_proof and refinement_fields:
            refinement=self._refine_training_gain_regions(
                pane, fields=refinement_fields, header=header_proof,
                result_grid=True, preview=preview_hint,
            )
            for name,observation in refinement.get('regions',{}).items():
                if name not in regions:
                    regions[name]=observation
        raw=dict(lines=[regions['header'],regions['option']],regions=regions,header=header,
            result_grid=result_grid,current_grid=False,
            engine_fingerprint=self.fingerprint+'-training-'+hashlib.sha256(__import__('tracen_replay.code_identity',fromlist=['function_digest']).function_digest(type(self).read_training)).hexdigest(),
            model_sha256=self.models,gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest(),inspection='training_result_only')
        if refinement is not None:
            raw['training_gain_source_refinement']=refinement
        # A localized source-pixel pass is supplemental to the fixed result
        # crops.  It is gated by the same result-grid/header proof and is
        # cached with the raw record so the parser can reuse it without a
        # second fresh OCR request.
        try:
            localization = self._localize_training_badges(
                pane, header=header, result_grid=result_grid,
            )
            raw['training_badge_localization'] = localization
            if localization.get('status') == 'localized':
                from .training_badge_localization import attach
                raw = attach(raw, localization)
        except (TypeError, ValueError, RuntimeError):
            pass
        # ``read_training`` is normally used for high-rate result inspection,
        # so run the general detector only to build the bounded preview panel
        # while the selected option and Failure badge are visible.  The panel
        # is typed and source-linked; it never turns result crops or totals into
        # preview effects.
        try:
            detected=self.engine(array[:,:,::-1])
            preview_lines=[]
            for box,text,score in zip(
                    detected.boxes if detected.boxes is not None else [],
                    detected.txts if detected.txts is not None else [],
                    detected.scores if detected.scores is not None else []):
                preview_lines.append(dict(
                    text=text,confidence=round(float(score)*100,4),
                    box=[round(float(box[:,0].min()))+148,
                         round(float(box[:,1].min())),
                         round(float(box[:,0].max()))+148,
                         round(float(box[:,1].max()))]))
            # Keep the full detector lines on the high-rate reading.  The
        # result crops identify their columns, while these same-frame
            # labels and header anchors let shared state readers validate
            # field identity and the goal countdown.  If detection returns no
            # lines, retain the lightweight header/option lines above.
            if preview_lines:
                raw['lines'] = preview_lines
            layout_lines = preview_lines if preview_lines else list(regions.values())
            result_layout = detect_training_result_layout(
                layout_lines,
                header=header or header_proof,
                current_grid=False,
                result_grid=result_grid,
            )
            if result_layout.get('result_grid') is True:
                # The fixed blue probe can miss the pink/red/grey applied
                # cards.  This same-frame banner + labeled-card proof is the
                # source-bound replacement signal for the stored raw record.
                result_grid = True
                raw['result_grid'] = True
                raw['header'] = raw.get('header') or result_layout['header']['text']
                raw['training_result_layout'] = result_layout
            raw['preview_lines'] = preview_lines
            from .preview_observations import produce_preview_panel_from_lines
            panel=produce_preview_panel_from_lines(
                dict(raw,lines=preview_lines,preview_lines=preview_lines))
            if panel is not None:
                raw['preview_panel']=panel
        except (TypeError, ValueError, RuntimeError):
            # A detector failure must leave the original result inspection
            # intact; callers will retain an explicit preview gap.
            pass
        return raw

    def read(self,pane):
        if pane.size != (810,1080):
            raise ValueError('Neural OCR accepts only the gameplay crop.')
        strip_saturation=_strip_saturation
        array=self.np.array(pane.convert('RGB'))
        # The recognizer knows where each word it read sits. Asking for that
        # costs nothing and changes no text or score; it is kept for the
        # receipt band alone, where an obstruction over a line has to be
        # judged word by word.
        result=self.engine(array[:,:,::-1],return_word_box=True)
        lines=[]
        if result.txts:
            found=getattr(result,'word_results',None) or ((),)*len(result.txts)
            for box,text,score,words in zip(result.boxes,result.txts,result.scores,found):
                line=dict(text=text,confidence=round(float(score)*100,4),
                    box=[round(float(box[:,0].min()))+148,round(float(box[:,1].min())),round(float(box[:,0].max()))+148,round(float(box[:,1].max()))])
                if words and 770<=line['box'][1]<1000:
                    line['word_boxes']=[dict(text=word,box=_word_box(quad)) for word,_score,quad in words]
                lines.append(line)
        def crop(box):
            left, top, right, bottom = _normalize_crop_box(box)
            return array[top:bottom,left-PANE[0]:right-PANE[0],::-1]
        def blue(box):
            colors=crop(box).astype('int16')
            return float(((colors[:,:,0]>colors[:,:,2]+25)&(colors[:,:,1]>colors[:,:,2]+15)&(colors[:,:,0]>100)).mean())
        header=' '.join(l['text'] for l in lines if within(l,(148,0,450,30)) and l['confidence']>=90)
        text='\n'.join(l['text'] for l in lines)
        grid=min(blue((x,911,x+35,934)) for x in (270,470))>.45 and header.lower().startswith('training')
        # The label strip of the stat bar takes the trainee's theme colour:
        # blue on one recording, pink and orange on two others. The probe
        # asks only for a saturated strip under every label; the geometry
        # proof below, and the fixed value crops it opens, decide the rest.
        current=min(strip_saturation(crop(box)) for box in STRIP_PROBE_BOXES)>STRIP_PROBE_MIN
        result_layout = detect_training_result_layout(
            lines,
            header=header,
            current_grid=current,
            result_grid=grid,
        )
        current_layout = detect_current_state_layout(
            lines,
            header=header,
            current_grid=current,
            result_layout=result_layout,
        )
        if current_layout.get('current_grid') is True:
            # The colour probe can disappear on the translucent/shifted
            # career bar.  Promote only same-frame label/value/cap geometry;
            # applied result layouts remain a separate parser phase.
            current = True
        if result_layout.get('result_grid') is True:
            # Friendship result cards may be pink/red/grey while the legacy
            # blue probe is false.  Promote only the independently observed
            # same-frame result layout; selectable menus remain vetoed by the
            # helper's current-grid gate.
            grid = True
        requests=[]
        localized_metadata={}
        cap_metadata={}
        stat_cap_metadata={}
        current_metadata={}
        if current:
            # The main bar moves down during the final-race transition.  When
            # the shared same-frame layout proof has identified its value
            # rows, keep the established horizontal columns but move each
            # numeric crop to that observed row.  This preserves the fixed
            # field identity while avoiding dialogue/background pixels in the
            # legacy y=721 crop.
            layout_rows = {
                row.get('field'): row
                for row in current_layout.get('field_geometry', [])
                if isinstance(row, dict) and isinstance(row.get('field'), str)
            } if current_layout.get('current_grid') is True else {}
            for field, base_box in zip(FIELDS, BOXES):
                request_box = base_box
                row = layout_rows.get(field)
                value_line = row.get('value') if isinstance(row, dict) else None
                value_box = value_line.get('box') if isinstance(value_line, dict) else None
                if isinstance(value_box, (list, tuple)) and len(value_box) == 4:
                    try:
                        left = max(float(base_box[0]), float(value_box[0]))
                        right = min(float(base_box[2]), float(value_box[2]))
                        top = float(value_box[1])
                        bottom = float(value_box[3])
                        if left < right and top < bottom:
                            request_box = (left, top, right, bottom)
                            current_metadata[f'current.{field}'] = dict(
                                role='current_stat', input_eligible=True,
                                geometry_basis='same_frame_current_stat_bar_value_line',
                                layout_schema=current_layout.get('schema_version'),
                            )
                    except (TypeError, ValueError, OverflowError):
                        pass
                requests.append((f'current.{field}', request_box))
            requests += [('countdown',(264,57,325,101))]
        panel_requests, component_metadata, localized_metadata, cap_metadata = _performance_panel_requests(lines, current)
        requests += panel_requests
        if not grid:
            stat_cap_requests = _main_stat_cap_requests(lines)
            requests += [(name, box) for name, box, _meta in stat_cap_requests]
            stat_cap_metadata = {name: meta for name, _box, meta in stat_cap_requests}
        if grid:
            gain_boxes=[(300,832,414,890),(498,832,610,890),(696,832,812,890),(300,950,414,1008),(498,950,610,1008),(696,950,812,1008)]
            requests += [(f'gain.{f}',b) for f,b in zip(FIELDS,gain_boxes)]
            requests += [(f'result.{f}',((322,518,714)[i%3],834 if i<3 else 952,(322,518,714)[i%3]+126,876 if i<3 else 994)) for i,f in enumerate(FIELDS)]
            requests += [('result.skill_points',(708,952,810,990))]
            # The performance sidebar stays visible while the result cards
            # animate, and the shared detector can merge a row's current value
            # and its award into one low-confidence line (``58+26``).  Request
            # the same signed rows read_training uses so the dedicated crop can
            # recover that award on an ordinary reading too.
            requests += [(f'performance_gain.{f}',(245,296+56*i,319,333+56*i)) for i,f in enumerate(CURRENCIES)]
        if 'spend performance points to learn' in text.lower():
            requests += [(f'projected_performance.{f}',(392+83*i,846,425+83*i,876)) for i,f in enumerate(CURRENCIES)]
            boxes=[(308,376,365,404),(403,376,455,404),(497,376,549,404),(591,376,644,404),(683,376,735,404),(756,376,817,404)]
            requests += [(f'modal_current.{f}',b) for f,b in zip(FIELDS,boxes)]
        elif header.lower().startswith('lessons'):
            requests += [(f'performance.{f}',(344+104*i,88,394+104*i,123)) for i,f in enumerate(CURRENCIES)]
        requests=[(name,_normalize_crop_box(box)) for name,box in requests]
        regions={}
        def recognition_crop(name, box):
            image = crop(box)
            metadata = (localized_metadata.get(name) or cap_metadata.get(name)
                        or stat_cap_metadata.get(name))
            if metadata and metadata.get('preprocess') in {
                    'panel_grayscale_autocontrast',
                    'stat_grayscale_autocontrast'}:
                # Translucent panels can lose their glyphs in the general
                # detector.  A grayscale/autocontrast view is still the same
                # source pixels and is requested only for a fixed row whose
                # labeled geometry established the panel.  It is not a
                # second source observation.
                pil = self.Image.fromarray(image[:, :, ::-1])
                pil = self.ImageOps.autocontrast(self.ImageOps.grayscale(pil))
                gray = self.np.asarray(pil)
                image = self.np.repeat(gray[:, :, None], 3, axis=2)[:, :, ::-1]
            return image
        if requests:
            rec=self.engine.text_rec(self.TextRecInput(img=[recognition_crop(name,b) for name,b in requests]))
            for (name,box),text,score in zip(requests,rec.txts,rec.scores):
                observation=dict(text=text,confidence=round(float(score)*100,4),box=list(box))
                if name in component_metadata:
                    observation.update(component_metadata[name])
                if name in localized_metadata:
                    observation.update(localized_metadata[name])
                if name in cap_metadata:
                    observation.update(cap_metadata[name])
                if name in stat_cap_metadata:
                    observation.update(stat_cap_metadata[name])
                if name in current_metadata:
                    observation.update(current_metadata[name])
                regions[name]=observation
        refinement=None
        if grid:
            header_proof=next(
                (line.get('text') for line in lines
                 if str(line.get('text','')).strip().casefold()=='training'
                 and line.get('confidence',0)>=90),
                header,
            )
            refinement_fields=_training_gain_refinement_fields(regions)
            preview_hint=any(
                _failure_banner_word(line.get('text'))
                and line.get('confidence',0)>=90
                for line in lines if isinstance(line,dict)
            )
            if header_proof and refinement_fields:
                refinement=self._refine_training_gain_regions(
                    pane, fields=refinement_fields, header=header_proof,
                    result_grid=True, preview=preview_hint,
                )
                for name,observation in refinement.get('regions',{}).items():
                    if name not in regions:
                        regions[name]=observation
        raw = dict(lines=lines,regions=regions,header=header,result_grid=grid,current_grid=current,
                   engine_fingerprint=self.fingerprint,model_sha256=self.models,
                   gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())
        if current_layout.get('current_grid') is True:
            raw['current_state_layout'] = current_layout
        if result_layout.get('result_grid') is True:
            raw['training_result_layout'] = result_layout
        if refinement is not None:
            raw['training_gain_source_refinement']=refinement
        # Keep source-localized badge candidates beside the ordinary OCR
        # regions.  This second pass is bounded to unambiguous orange groups
        # inside verified training result cards; previews and unrelated panels
        # are rejected before OCR is requested.
        if grid and header.lower().startswith('training'):
            try:
                localization = self._localize_training_badges(
                    pane, header=header, result_grid=grid,
                )
                raw['training_badge_localization'] = localization
                if localization.get('status') == 'localized':
                    from .training_badge_localization import attach
                    raw = attach(raw, localization)
            except (TypeError, ValueError, RuntimeError):
                pass
        # A normal video read is the source-bound entry point for translucent
        # training previews.  The helper schedules fixed gameplay-crop rows
        # only when the immutable detector geometry identifies a candidate;
        # ordinary frames return unchanged and cached replay can retain the
        # attached provenance without rerunning OCR.
        try:
            from .preview_recovery import recover_in_memory
            raw = recover_in_memory(raw, pane, reader=self)
        except (TypeError, ValueError, RuntimeError):
            # Preview recovery is supplemental to the base OCR reading.  A
            # failed bounded reread must leave the source observation usable,
            # with no partially selected preview amount attached.
            pass
        return raw


# A row's value as the panel writes it: no leading zero, so the "015" a
# recognizer returns for a row reading "0" beside its "+15" award badge is
# not a value of fifteen.
_PANEL_VALUE = r'0|[1-9]\d{0,2}'

_PERFORMANCE_PANEL_ROWS = (
    # The abbreviated labels and the /200 cap are part of the panel identity;
    # the values themselves are deliberately read from a separate band.
    ('dance', 'Da', 320, 334),
    ('passion', 'Pa', 376, 390),
    ('vocal', 'Vo', 432, 446),
    ('visual', 'Vi', 488, 502),
    ('composure', 'Co', 544, 558),
)


def _performance_panel_line_eligible(line):
    """Return whether one OCR line may supply a panel value.

    Raw sidecars can retain detector lines that were reviewed as context or
    as a noisy/merged alternative.  They remain useful diagnostics, but an
    explicit exclusion must never become a semantic balance through the
    confidence fallback.
    """
    if not isinstance(line, dict) or line.get('input_eligible') is False:
        return False
    role = line.get('role')
    if isinstance(role, str):
        role = role.strip().casefold()
        if role.endswith('_excluded') or role in {
            'source_context_line',
            'merged_current_projection_observation',
            'noisy_amount_candidate_excluded',
        }:
            return False
    return True


def _performance_panel_component_requests(lines):
    """Build same-row component crops for merged panel values.

    The boxes derive from the detector line's own geometry and retain a small
    vertical inset to avoid the adjacent ``/cap`` line.  This helper does not
    know a recording, timestamp, expected value, or balance; it only requests
    components when one row has exactly one explicit merged observation.
    """
    requests = []
    for field, _label, label_y, _cap_y in _PERFORMANCE_PANEL_ROWS:
        band = (190, label_y - 30, 335, label_y + 15)
        merged = []
        for line in lines:
            if not _performance_panel_line_eligible(line) or not within(line, band):
                continue
            text = re.sub(r'\s+', '', str(line.get('text', '')).strip())
            if re.fullmatch(r'\d{1,3}\+\d{1,3}', text):
                merged.append(line)
        if len(merged) != 1:
            continue
        line = merged[0]
        box = line.get('box')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            left, top, right, bottom = [float(value) for value in box]
        except (TypeError, ValueError):
            continue
        width = right - left
        height = bottom - top
        if width <= 20 or height <= 8:
            continue
        split = left + width * 0.5
        x_inset = max(4.0, width * 0.065)
        y_inset = max(2.0, height * 0.08)
        # Leave a separator margin on the current crop.  Including the first
        # pixels of the plus glyph can turn ``89`` into ``894`` in the
        # localized recognizer; the projected crop keeps the existing narrow
        # overlap so a clipped plus sign remains optional by role.
        separator = max(4.0, width * 0.04)
        current_box = [round(left + x_inset), round(top + y_inset),
                       round(split - separator), round(bottom - y_inset)]
        projected_box = [round(split - 2), round(top + y_inset),
                         round(right - max(2.0, width * 0.02)), round(bottom - y_inset)]
        if (current_box[2] <= current_box[0] or projected_box[2] <= projected_box[0]
                or current_box[3] <= current_box[1] or projected_box[3] <= projected_box[1]):
            continue
        parent = dict(text=line.get('text', ''), confidence=line.get('confidence', 0),
                      box=list(line.get('box', [])))
        for component, box in (('current', current_box), ('projected', projected_box)):
            name = f'performance_panel_{component}.{field}'
            requests.append((name, box, {
                'role': f'panel_{component}_component',
                'input_eligible': True,
                'component': component,
                'geometry_basis': 'same_row_merged_panel_line',
                'parent_observation': parent,
            }))
    return requests


def _performance_panel_cap_top(lines,cap_y):
    """The upper edge of a panel row's cap line, when the detector read one."""
    found=[line for line in lines
           if _performance_panel_line_eligible(line)
           and re.fullmatch(r'/\s*\d{1,4}',re.sub(r'\s+','',str(line.get('text','')).strip()))
           and within(line,(185,cap_y-25,280,cap_y+25))
           and isinstance(line.get('box'),(list,tuple)) and len(line['box'])==4]
    return min(line['box'][1] for line in found) if found else None


def _performance_panel_slot_box(source,left,right):
    """One row's whole value slot, at the height the detector read it.

    A detector box can stop short of a number's first digit: ``18`` comes back
    as a clean ``8``, and the confidence belongs to the glyphs inside that box
    rather than to where its edges fell, so a clipped reading looks settled.
    The box is sound vertically -- the clipping is horizontal -- so widening it
    across the slot asks the one question the reading cannot answer itself:
    whether another digit stands to its left.
    """
    box=source.get('box')
    if not isinstance(box,(list,tuple)) or len(box)!=4:
        return None
    try:
        a,b,c,d=[float(value) for value in box]
    except (TypeError,ValueError):
        return None
    if c-a<=0 or d-b<=0 or a<=left:
        return None
    return [left,round(b-3),right,round(d+3)]


def _performance_panel_localized_requests(lines):
    """Request bounded value crops for rows the detector did not expose.

    A translucent panel can leave only a few of its rows in the general OCR
    output.  When at least three rows share the fixed panel row geometry, use
    the observed value boxes to derive a common x-range and reread only the
    missing rows.  The request carries no value expectation and is never made
    for a row whose source line is a merged current/projection shape; that
    shape remains owned by the component split path.
    """
    if not isinstance(lines,list):
        return []
    observed={}
    merged_fields=set()
    # A badge's number would otherwise stand in for the row it floats over,
    # and the row whose own value the detector missed would never be reread.
    badge_parts=_performance_more_badge_parts(lines)
    badges=_performance_more_badge_numbers(lines,lines)
    for field,_label,label_y,_cap_y in _PERFORMANCE_PANEL_ROWS:
        band=(160,label_y-27,275,label_y+5)
        plain=[]
        merged=[]
        for line in lines:
            if not _performance_panel_line_eligible(line) or not within(line,band):
                continue
            if any(line is badge for badge in badges):
                continue
            text=re.sub(r'\s+','',str(line.get('text','')).strip())
            if re.fullmatch(_PANEL_VALUE,text):
                plain.append(line)
            elif re.fullmatch(r'\d{1,3}\+\d{1,3}',text):
                merged.append(line)
        if merged:
            merged_fields.add(field)
        if len(plain)==1:
            observed[field]=plain[0]
    if len(observed)<3:
        return []
    lefts=sorted(float(line['box'][0]) for line in observed.values())
    rights=sorted(float(line['box'][2]) for line in observed.values())
    middle=len(lefts)//2
    left=max(160,round(lefts[middle]-8))
    right=min(275,round(rights[middle]+14))
    if right-left<35:
        return []
    requests=[]
    for field,_label,label_y,_cap_y in _PERFORMANCE_PANEL_ROWS:
        source=observed.get(field)
        if source is not None and field not in merged_fields:
            # Asked for however the line read. A clipped box reads its own
            # glyphs perfectly, so confidence cannot excuse the row from it.
            slot=_performance_panel_slot_box(source,left,right)
            if slot is not None:
                requests.append((f'performance_panel_localized_slot.{field}',slot,{
                    'role':'panel_localized_current',
                    'input_eligible':True,
                    'component':'current',
                    'geometry_basis':'slot_widened_value_geometry',
                    'source_row_geometry':dict(field=field,label_y=label_y,box=list(slot)),
                    'preprocess':'panel_grayscale_autocontrast',
                }))
        if source is not None and source.get('confidence',0)>=90:
            continue
        if field in merged_fields:
            continue
        box=[left,label_y-31,right,label_y+14]
        # Never take in the row's cap. A crop that does reads the cap's digits
        # when the value itself is faint, and a row whose value happens to
        # equal its cap cannot tell the two apart afterwards.
        cap=_performance_panel_cap_top(lines,_cap_y)
        if cap is not None and box[1]<cap<box[3]:
            box=[left,box[1],right,cap]
        covered=_performance_more_badge_bottom(badge_parts,box)
        if covered is not None and box[1]<covered<box[3]:
            box=[left,covered,right,box[3]]
        requests.append((f'performance_panel_localized_current.{field}',box,{
            'role':'panel_localized_current',
            'input_eligible':True,
            'component':'current',
            'geometry_basis':'fixed_row_panel_geometry',
            'source_row_geometry':dict(field=field,label_y=label_y,box=list(box)),
            'preprocess':'panel_grayscale_autocontrast',
        }))
        if source is None:
            continue
        # The detector found this row's own number and was unsure of it. A
        # crop of fixed geometry can reach into the row's cap, where a faint
        # value comes back as the cap's digits, while a crop of the detector's
        # own box inherits a box that clipped a digit. Read both: they either
        # agree on the number or the row stays unknown.
        a,b,c,d=source['box']
        glyph=[max(left,a-6),b-6,min(right,c+6),d+6]
        if glyph==box:
            continue
        requests.append((f'performance_panel_localized_glyph.{field}',glyph,{
            'role':'panel_localized_current',
            'input_eligible':True,
            'component':'current',
            'geometry_basis':'detected_value_line_geometry',
            'source_row_geometry':dict(field=field,label_y=label_y,box=list(glyph)),
            'preprocess':'panel_grayscale_autocontrast',
        }))
    return requests


def _performance_panel_heading(lines):
    """Whether the sidebar's ``Performance`` heading is at its fixed place.

    This is the identification the preview and result parser already relies
    on.  The panel can be on screen without the stat grid, while a training's
    result cards animate, and its heading is the one word that never moves.
    """
    if not isinstance(lines, list):
        return False
    return any(isinstance(l, dict) and l.get('text') == 'Performance' and within(l, (150, 250, 270, 285))
               for l in lines)


def _performance_panel_requests(lines, current):
    """The sidebar's bounded rereads, wherever the panel is on screen.

    The performance sidebar is persistent: it stays up while a training's
    result cards animate, where the stat grid is gone.  Its rereads are
    therefore asked for beside the grid or under the panel's own heading,
    and each builder still gates on the panel's row geometry, asking for
    nothing where the rows are not there.  A detector can merge a row's
    current and projected values in one line: the merged observation is kept
    and two same-row component crops are asked for, so a source-backed split
    is possible when their geometry is clear.  Returns the crop requests and
    the metadata of the component, localized and cap crops.
    """
    if not current and not _performance_panel_heading(lines):
        return [], {}, {}, {}
    component = _performance_panel_component_requests(lines)
    localized = _performance_panel_localized_requests(lines)
    caps = _performance_panel_cap_requests(lines)
    requests = [(name, box) for name, box, _meta in component + localized + caps]
    return (requests, {name: meta for name, _box, meta in component},
            {name: meta for name, _box, meta in localized}, {name: meta for name, _box, meta in caps})


def _performance_panel_identity(lines):
    """Return panel rows only when the fixed sidebar identity is visible.

    The ordinary screen classifier is intentionally not used here.  A menu,
    dialogue or transition frame can still contain the persistent sidebar, so
    the sidebar needs its own conservative identity check.  A lone
    ``Performance`` header, or numbers that merely happen to be near the
    sidebar, is insufficient: both header words, every abbreviated row label,
    and every row cap must be present at the expected geometry.
    """
    def text(line):
        return re.sub(r'\s+', ' ', str(line.get('text', '')).strip())

    def candidates(expected, box, confidence=90, aliases=()):
        accepted={expected,*aliases}
        return [line for line in lines if line.get('confidence', 0) >= confidence
                and text(line) in accepted and within(line, box)]

    # The two-line heading is more resistant to incidental UI text than a
    # single keyword.  Keep the geometry broad enough for ordinary OCR box
    # jitter while requiring one unambiguous instance of each word.
    header = candidates('Performance', (145, 245, 280, 290))
    # ``Points`` is a fixed UI keyword.  A detector can clip its final
    # characters while retaining the distinctive prefix; allow only these
    # explicit grammar forms at the lower anchor floor and keep the original
    # OCR text in the returned proof.
    points_header = candidates('Points', (155, 265, 280, 315), 80,
                               aliases=('Poin','Point'))
    if len(header) != 1 or len(points_header) != 1:
        return None

    labels = {}
    caps = {}
    cap_values = {}
    for field, label, label_y, cap_y in _PERFORMANCE_PANEL_ROWS:
        row_labels = candidates(label, (140, label_y - 25, 205, label_y + 25))
        row_caps = [line for line in lines if line.get('confidence', 0) >= 80
                    and re.fullmatch(r'/\s*(\d{1,4})', text(line))
                    and within(line, (185, cap_y - 25, 280, cap_y + 25))]
        if len(row_labels) != 1 or len(row_caps) != 1:
            return None
        cap_match = re.fullmatch(r'/\s*(\d{1,4})', text(row_caps[0]))
        if cap_match is None or int(cap_match[1]) <= 0:
            return None
        labels[field] = row_labels[0]
        caps[field] = row_caps[0]
        cap_values[field] = int(cap_match[1])
    return dict(header=header[0], points_header=points_header[0], labels=labels, caps=caps,
                cap_values=cap_values, value_minimum_confidence=90,
                geometry_basis='labeled_panel_row_geometry')


def _performance_panel_identity_from_localized(lines, regions, stats):
    """Recognize a partially detected panel from fixed same-frame geometry.

    This fallback is intentionally stricter than accepting numbers near the
    sidebar: the current-stat grid must be present, three distinct panel rows
    must be observed, and at least one slash-cap line must anchor the panel.
    Missing labels/caps stay unknown and are never supplied by totals or
    neighboring rows.  Localized rows are accepted only from the explicit
    fixed-row refinement namespace.
    """
    if not isinstance(stats,dict) or not isinstance(stats.get('values'),dict):
        return None
    if sum(type(value) is int and value >= 0
           for value in stats['values'].values()) < 3:
        return None
    source_rows=[]
    for field,_label,label_y,_cap_y in _PERFORMANCE_PANEL_ROWS:
        band=(160,label_y-30,280,label_y+15)
        values=[]
        for line in (lines if isinstance(lines,list) else ()):
            if not _performance_panel_line_eligible(line) or not within(line,band):
                continue
            text=re.sub(r'\s+','',str(line.get('text','')).strip())
            if re.fullmatch(_PANEL_VALUE,text) and line.get('confidence',0)>=90:
                values.append(int(text))
        values.extend(item['value'] for item in _performance_panel_localized_candidates(
            regions, field, label_y, 90))
        if len(set(values))==1:
            source_rows.append(field)
    if len(source_rows)<3:
        return None
    cap_values={field:None for field,_label,_label_y,_cap_y in _PERFORMANCE_PANEL_ROWS}
    cap_count=0
    for field,_label,_label_y,cap_y in _PERFORMANCE_PANEL_ROWS:
        caps=[line for line in lines if _performance_panel_line_eligible(line)
              and line.get('confidence',0)>=80
              and re.fullmatch(r'/\s*(\d{1,4})',
                               re.sub(r'\s+','',str(line.get('text','')).strip()))
              and within(line,(185,cap_y-25,280,cap_y+25))]
        if len(caps)==1:
            cap_values[field]=int(re.fullmatch(
                r'/\s*(\d{1,4})',re.sub(r'\s+','',str(caps[0].get('text','')).strip()))[1])
            cap_count+=1
    # A bounded cap sidecar can supply a missing/weak row after validating the
    # same source frame.  It contributes only its own labeled crop; no value
    # is inferred from the current panel or from an accounting balance.
    try:
        from .numeric_cap_refinement import read_performance_caps
        refined_caps, _refined_proof = read_performance_caps(lines, regions)
    except (ImportError, TypeError, ValueError):
        refined_caps = {}
    for field, value in refined_caps.items():
        if field in cap_values and cap_values[field] is None:
            cap_values[field] = value
            cap_count += 1
    if cap_count<1:
        return None
    return dict(header=None, points_header=None, labels={}, caps={},
                cap_values=cap_values, value_minimum_confidence=90,
                geometry_basis='fixed_row_panel_geometry', source_rows=source_rows)


def _performance_panel_record(observation):
    """Copy one panel observation into provenance without dropping roles."""
    record = dict(text=observation.get('text', ''),
                  confidence=observation.get('confidence', 0),
                  box=list(observation.get('box', [])))
    for key in ('role', 'input_eligible', 'component', 'geometry_basis',
                'parent_observation'):
        if key in observation:
            record[key] = observation[key]
    return record


def _performance_panel_component_candidates(regions, field, label_y,
                                            component, minimum_confidence=97):
    """Read only explicitly named, same-row component crop observations."""
    if not isinstance(regions, dict):
        return []
    prefixes = (
        f'performance_panel_{component}.',
        f'panel_{component}.',
    )
    band = (190, label_y - 30, 335, label_y + 15)
    candidates = []
    for name in sorted(regions):
        if not isinstance(name, str) or not any(name.startswith(prefix)
                                                for prefix in prefixes):
            continue
        if not name.endswith('.' + str(field)):
            continue
        observation = regions[name]
        if not isinstance(observation, dict) or not _performance_panel_line_eligible(observation):
            continue
        if observation.get('component') not in (None, component):
            continue
        box = observation.get('box')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            in_band = within(observation, band)
        except (TypeError, ValueError):
            in_band = False
        if not in_band:
            continue
        try:
            confidence = float(observation.get('confidence', 0))
        except (TypeError, ValueError):
            continue
        if confidence < minimum_confidence:
            continue
        text = re.sub(r'\s+', '', str(observation.get('text', '')).strip())
        if component == 'current':
            match = re.fullmatch(_PANEL_VALUE, text)
        else:
            # The plus glyph can be clipped by the right component crop.  The
            # crop role supplies the sign semantics; merged/slash text does
            # not qualify as a localized component.
            match = re.fullmatch(r'\+?(\d{1,3})', text)
        if not match:
            continue
        value = int(match[1] if component == 'projected' else match[0])
        candidates.append(dict(value=value, observation=observation,
                               region=name, component=component))
    return candidates


_SINGLE_DIGIT_CROP_CONFIDENCE=70


def _performance_panel_localized_candidates(regions, field, label_y,
                                            minimum_confidence=97):
    """Read fixed-geometry current crops from a same-frame refinement."""
    if not isinstance(regions,dict):
        return []
    prefixes=(
        'performance_panel_localized_current.',
        'performance_panel_localized_glyph.',
        'panel_localized_current.',
    )
    band=(160,label_y-35,280,label_y+20)
    candidates=[]
    for name in sorted(regions):
        if not isinstance(name,str) or not any(name.startswith(prefix) for prefix in prefixes):
            continue
        if not name.endswith('.'+str(field)):
            continue
        observation=regions[name]
        if not isinstance(observation,dict) or not _performance_panel_line_eligible(observation):
            continue
        if observation.get('component') not in (None,'current'):
            continue
        if observation.get('role') not in (None,'panel_localized_current'):
            continue
        if observation.get('geometry_basis') not in (None,'fixed_row_panel_geometry','detected_value_line_geometry'):
            continue
        try:
            confidence=float(observation.get('confidence',0))
            if not within(observation,band):
                continue
        except (TypeError,ValueError):
            continue
        text=re.sub(r'\s+','',str(observation.get('text','')).strip())
        if not re.fullmatch(_PANEL_VALUE,text):
            continue
        # A lone digit reads at a lower confidence than a number of two or
        # three glyphs, and the crop of a row the detector missed is the
        # only reading of it. One digit is taken from 70; the ledger still
        # wants the same value on two frames before it counts.
        floor=(_SINGLE_DIGIT_CROP_CONFIDENCE if len(text)==1 and name.startswith('performance_panel_localized_current.')
               else minimum_confidence)
        if confidence<floor:
            continue
        candidates.append(dict(value=int(text),observation=observation,region=name,component='current',localized=True,
                               below_floor=confidence<minimum_confidence))
    return candidates


def _performance_panel_slot_candidates(regions, field, label_y,
                                       minimum_confidence=97):
    """Read the slot-widened crop of a row the detector already reported."""
    if not isinstance(regions,dict):
        return []
    band=(160,label_y-35,280,label_y+20)
    candidates=[]
    for name in sorted(regions):
        if not isinstance(name,str) or not name.startswith('performance_panel_localized_slot.'):
            continue
        if not name.endswith('.'+str(field)):
            continue
        observation=regions[name]
        if not isinstance(observation,dict) or not _performance_panel_line_eligible(observation):
            continue
        if observation.get('geometry_basis')!='slot_widened_value_geometry':
            continue
        try:
            if not within(observation,band) or float(observation.get('confidence',0))<minimum_confidence:
                continue
        except (TypeError,ValueError):
            continue
        text=re.sub(r'\s+','',str(observation.get('text','')).strip())
        if not re.fullmatch(_PANEL_VALUE,text):
            continue
        candidates.append(dict(value=int(text),text=text,observation=observation,region=name))
    return candidates


def _panel_clipped_value(current, slot_candidates):
    """The slot reading that proves the detector's box clipped a leading digit.

    The slot crop covers the detector's own box and the empty part of the slot
    beside it, so it saw everything that reading saw.  When it comes back with
    that reading as the tail of a longer number, the extra digits stood where
    the box stopped, and the row is the longer number.  Anything else -- a
    disagreement that is not an extension, a second reading of either crop --
    is not this, and the row is left to the ordinary conflict rules.
    """
    if len(slot_candidates)!=1 or len(current)!=1 or current[0].get('localized'):
        return None
    source=current[0]['observation']
    slot=slot_candidates[0]
    text=re.sub(r'\s+','',str(source.get('text','')).strip())
    if not re.fullmatch(_PANEL_VALUE,text):
        return None
    if slot['text']==text or not slot['text'].endswith(text):
        return None
    box=source.get('box'); wider=slot['observation'].get('box')
    if not isinstance(box,(list,tuple)) or not isinstance(wider,(list,tuple)):
        return None
    if len(box)!=4 or len(wider)!=4:
        return None
    try:
        if not (float(wider[0])<float(box[0]) and float(wider[2])>=float(box[2])-2):
            return None
    except (TypeError,ValueError):
        return None
    return dict(value=slot['value'],observation=slot['observation'],localized=True,clipped=True)


def _performance_panel_cap_requests(lines):
    """Request bounded rereads for absent or ambiguous panel cap rows."""
    requests = []
    for field, _label, _label_y, cap_y in _PERFORMANCE_PANEL_ROWS:
        candidates = []
        for line in lines:
            if not _performance_panel_line_eligible(line):
                continue
            try:
                in_band = within(line, (170, cap_y - 28, 285, cap_y + 28))
                confidence = float(line.get('confidence', 0))
            except (TypeError, ValueError):
                continue
            text = re.sub(r'\s+', '', str(line.get('text', '')).strip())
            if in_band and confidence >= 90 and re.fullmatch(r'/\d{1,4}', text):
                candidates.append(text)
        if len(set(candidates)) == 1:
            continue
        requests.append((
            f'performance_panel_cap.{field}',
            (170, cap_y - 28, 285, cap_y + 28),
            dict(role='panel_cap', input_eligible=True,
                 component='cap', geometry_basis='fixed_row_panel_cap_geometry',
                 preprocess='panel_grayscale_autocontrast'),
        ))
    return requests


def _main_stat_cap_requests(lines):
    """Request bounded rereads for missing main-stat cap rows.

    The detector's source lines establish each stat column before a reread is
    scheduled.  The request is therefore tied to a labeled, same-frame bar;
    it never supplies a cap value or borrows one from another frame/layout.
    """
    requests = []
    for field, (left, _top, right, _bottom) in zip(FIELDS[:5], BOXES[:5]):
        headers = [line for line in lines
                   if _performance_panel_line_eligible(line)
                   and str(line.get('text', '')).strip().casefold() == field
                   and within(line, (left - 24, 650, right + 24, 785))]
        if len(headers) != 1:
            continue
        header = headers[0]
        header_y = (header['box'][1] + header['box'][3]) / 2
        cap_candidates = []
        for line in lines:
            if not _performance_panel_line_eligible(line):
                continue
            text = re.sub(r'\s+', '', str(line.get('text', '')).strip())
            match = re.fullmatch(r'(?:\d{1,4})?[/VYlI]\d{1,4}', text,
                                 re.IGNORECASE)
            if (match and within(line, (left - 12, header_y + 20,
                                        right + 12, header_y + 100))):
                cap_candidates.append(text)
        if len(set(cap_candidates)) == 1:
            continue
        requests.append((
            f'stats_cap.{field}',
            (left - 12, header_y + 20, right + 12, header_y + 100),
            dict(role='stat_cap', input_eligible=True, component='cap',
                 geometry_basis='labeled_main_stat_bar_cap',
                 preprocess='stat_grayscale_autocontrast'),
        ))
    return requests


def _performance_panel_values(lines, identity, regions=None):
    """Read independently labeled panel values; omit ambiguous values."""
    points = {}
    for field, _label, label_y, _cap_y in _PERFORMANCE_PANEL_ROWS:
        observation = _performance_panel_field(
            lines, field, label_y,
            minimum_confidence=identity.get('value_minimum_confidence',97),
            regions=regions)
        if (observation['current'] is not None
            and observation['status'] != 'resolved_merged_panel_value'):
            value = observation['current']['value']
            # The cap is source evidence for this row, so a value above it is
            # an OCR contradiction rather than a plausible balance.  Omit the
            # field and leave it unknown instead of clamping or choosing a
            # value that would make the accounting balance.
            cap=identity['cap_values'].get(field)
            if cap is None or value <= cap:
                points[field] = value
    return points


def _performance_more_badge_numbers(lines, candidates):
    """The candidate lines that are a "N more" badge's number.

    A concert bonus draws a small badge over the panel, just above the row it
    talks about, and the detector reads it either whole ("13 more") or split
    into its number and the word "more". Split, the number looks exactly like
    a row value inside that row's band, so the badge above a row would be
    read as the row's own points. The number belongs to the badge when the
    word sits beside it on the same line of pixels.
    """
    words = [line for line in lines
             if isinstance(line, dict) and isinstance(line.get('box'), (list, tuple)) and len(line['box']) == 4
             and re.fullmatch(r'more', str(line.get('text', '')).strip(), re.I)]
    if not words:
        return []
    badges = []
    for line in candidates:
        box = line.get('box') if isinstance(line, dict) else None
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        if not re.fullmatch(r'\d{1,3}', re.sub(r'\s+', '', str(line.get('text', '')).strip())):
            continue
        for word in words:
            other = word['box']
            shared = min(box[3], other[3]) - max(box[1], other[1])
            if shared * 2 < min(box[3] - box[1], other[3] - other[1]):
                continue
            if -8 <= other[0] - box[2] <= 30:
                badges.append(line)
                break
    return badges


def _performance_more_badge_parts(lines):
    """Every line a "N more" badge is read as, whole or split into its parts."""
    parts = [line for line in lines
             if isinstance(line, dict) and isinstance(line.get('box'), (list, tuple)) and len(line['box']) == 4
             and re.fullmatch(r'(\d{1,3}\s*)?more', str(line.get('text', '')).strip(), re.I)]
    return parts + _performance_more_badge_numbers(lines, lines)


def _performance_more_badge_bottom(parts, box):
    """The lowest edge of a "N more" badge drawn over this crop, or None.

    The badge overlaps the top of the value it talks about, so a crop of the
    row's own geometry reads the badge instead of the value. Starting the
    crop under the badge leaves the part of the value the badge does not
    cover, which the recognizer reads or does not; the confidence floor still
    decides whether it becomes a value.
    """
    bottom = None
    for line in parts:
        left, top, right, low = line['box']
        if right <= box[0] or left >= box[2] or low <= box[1] or top >= box[3]:
            continue
        bottom = low if bottom is None else max(bottom, low)
    return bottom


def _merged_amount_disagrees(raw_merged, projected_value, minimum_confidence):
    """Whether a merged detector line's amount contradicts the projected component.

    A merged line under the confidence floor whose amount is the start of the
    component's ("9+1" beside a crop reading 11) is that line cut short, not
    a second reading; a confident line, or one that says something else,
    disagrees as before.
    """
    _value, amount, line = raw_merged
    if amount == projected_value:
        return False
    try:
        confidence = float(line.get('confidence', 0))
    except (TypeError, ValueError):
        confidence = 0.0
    return not (confidence < minimum_confidence and str(projected_value).startswith(str(amount)))


def _performance_panel_field(lines, field, label_y, minimum_confidence=97, regions=None):
    """Parse one labeled performance row without merging unrelated views.

    The value band belongs to the row identified by its abbreviated label and
    cap.  A merged ``current+projection`` value is accepted only at the same
    confidence floor used by the legacy training path.  Lower-confidence
    merged text is retained as an unresolved observation so a source review
    can explain the limitation without manufacturing either number.
    """
    band = (190, label_y - 30, 335, label_y + 15)
    raw = [line for line in lines if within(line, band)]
    badges = _performance_more_badge_numbers(lines, raw)
    candidates = [line for line in raw
                  if _performance_panel_line_eligible(line)
                  and line.get('confidence', 0) >= minimum_confidence
                  and not any(line is badge for badge in badges)]
    merged=[];current=[];projected=[];cut=[]
    for line in candidates:
        text=re.sub(r'\s+', '', str(line.get('text', '')).strip())
        match=re.fullmatch(r'(\d{1,3})\+(\d{1,3})',text)
        if match:
            merged.append(dict(value=int(match[1]), amount=int(match[2]), observation=line))
            continue
        if re.fullmatch(r'\d{1,3}\+',text):
            cut.append(line)
            continue
        match=re.fullmatch(_PANEL_VALUE,text)
        if match:current.append(dict(value=int(text),observation=line));continue
        match=re.fullmatch(r'\+(\d{1,3})',text)
        if match:projected.append(dict(value=int(match[1]),observation=line))

    # Keep merged detector readings as a diagnostic cross-check even when an
    # input role excludes them from the semantic candidates.  A localized
    # component that disagrees with that source text is still ambiguous.
    raw_merged = []
    for line in raw:
        text = re.sub(r'\s+', '', str(line.get('text', '')).strip())
        match = re.fullmatch(r'(\d{1,3})\+(\d{1,3})', text)
        if match:
            raw_merged.append((int(match[1]), int(match[2]), line))

    component_current = _performance_panel_component_candidates(
        regions, field, label_y, 'current', minimum_confidence)
    component_projected = _performance_panel_component_candidates(
        regions, field, label_y, 'projected', minimum_confidence)
    localized_current = _performance_panel_localized_candidates(
        regions, field, label_y, minimum_confidence)
    # Crops of one row that agree are one reading of it; they differ only in
    # where they were cut. Crops that disagree stay separate, and the row is
    # then as unresolved as any other disagreement leaves it.
    agreed = {item['value'] for item in localized_current}
    current.extend(dict(value=item['value'], observation=item['observation'], localized=True)
                   for item in (localized_current[:1] if len(agreed) == 1 else localized_current))
    slot_current = _performance_panel_slot_candidates(
        regions, field, label_y, minimum_confidence)
    clipped = _panel_clipped_value(current, slot_current)
    if clipped is not None:
        current = [clipped]
    else:
        # A crop of the whole slot saw everything the line saw. Reading the
        # same number is that row confirming itself and adds no candidate;
        # reading a different one that is not the line extended leaves two
        # readings of one row, which is a conflict like any other.
        seen = {item['value'] for item in current}
        current.extend(dict(value=item['value'], observation=item['observation'], localized=True)
                       for item in slot_current if item['value'] not in seen)
    evidence=dict(field=field,band=list(band),
                  raw_observations=[_performance_panel_record(line) for line in raw])
    if component_current or component_projected:
        evidence['component_observations'] = {
            'current': [_performance_panel_record(item['observation'])
                        for item in component_current],
            'projected': [_performance_panel_record(item['observation'])
                          for item in component_projected],
        }
    if localized_current:
        evidence['localized_observations'] = [
            _performance_panel_record(item['observation'])
            for item in localized_current
        ]
    if slot_current:
        evidence['slot_observations'] = [
            _performance_panel_record(item['observation'])
            for item in slot_current
        ]

    # Component crops are a separately localized source observation.  They
    # can resolve a low-confidence/merged detector line, but disagreement
    # with any eligible line remains unresolved rather than selecting a value
    # from balances or a neighboring row.
    current_values = {}
    for item in current:
        current_values.setdefault(item['value'], []).append(item['observation'])
    for item in component_current:
        current_values.setdefault(item['value'], []).append(item['observation'])
    for item in localized_current:
        current_values.setdefault(item['value'], []).append(item['observation'])
    projected_values = {}
    for item in projected:
        projected_values.setdefault(item['value'], []).append(item['observation'])
    for item in component_projected:
        projected_values.setdefault(item['value'], []).append(item['observation'])
    if (len(current_values) == 1 and len(projected_values) == 1
            and (component_current or component_projected)):
        current_value, current_observations = next(iter(current_values.items()))
        projected_value, projected_observations = next(iter(projected_values.items()))
        # A merged detector line is useful corroboration only when both
        # localized components agree with it exactly, even if the line was
        # excluded from semantic input.
        if raw_merged and (len(raw_merged) != 1
                           or raw_merged[0][0] != current_value
                           or _merged_amount_disagrees(raw_merged[0], projected_value, minimum_confidence)):
            evidence.update(status='unresolved_panel_value_conflict',
                            current=None, projected=None)
            return evidence
        basis = 'same_row_component_crops'
        current_observation = next((item['observation'] for item in component_current
                                    if item['value'] == current_value),
                                   current_observations[0])
        projected_observation = next((item['observation'] for item in component_projected
                                      if item['value'] == projected_value),
                                     projected_observations[0])
        evidence.update(status='resolved_separate_component_panel_values', basis=basis,
                        current=dict(value=current_value,
                                     observation=_performance_panel_record(current_observation)),
                        projected=dict(value=projected_value,
                                       observation=_performance_panel_record(projected_observation)))
        return evidence
    # If component alternatives exist but disagree, retain every raw value.
    # A single component cannot establish the other side of a merged line.
    if component_current or component_projected:
        if len(current_values) > 1 or len(projected_values) > 1:
            evidence.update(status='unresolved_panel_value_conflict', current=None,
                            projected=None)
            return evidence
        if raw_merged:
            if (len(raw_merged) != 1
                    or (current_values and next(iter(current_values)) != raw_merged[0][0])
                    or (projected_values and _merged_amount_disagrees(
                        raw_merged[0], next(iter(projected_values)), minimum_confidence))):
                evidence.update(status='unresolved_panel_value_conflict', current=None,
                                projected=None)
                return evidence

    # A fixed current crop cannot split an unresolved merged source line.  The
    # component path must establish both halves so the phase distinction stays
    # intact.
    if raw_merged and localized_current and not (component_current or component_projected):
        evidence.update(status='unresolved_panel_value_shape_conflict', current=None,
                        projected=None)
        return evidence

    # A value followed by a bare plus ("5+") is the award's box cut before its
    # digits: the row had an award this frame that was not read. The value
    # alone would say the row gave nothing, so the row stays unresolved until
    # a frame reads the award.
    if cut and not merged and not projected_values:
        evidence.update(status='unresolved_cut_merged_panel_value',current=None,projected=None)
        return evidence

    # A row under a "N more" badge shows its award beside a value the badge
    # covers, so the award reads alone. With nothing else in the band read as
    # a value and one amount read, it is that row's own.
    if (not current_values and not merged and len(projected_values)==1
            and _performance_more_badge_bottom(_performance_more_badge_parts(lines),band) is not None):
        value,observations=next(iter(projected_values.items()))
        evidence.update(status='resolved_projected_under_badge',basis='award_beside_badge_covered_value',
                        current=None,projected=dict(value=value,observation=_performance_panel_record(observations[0])))
        return evidence

    if len(merged)==1 and not current and not projected:
        item=merged[0]
        evidence.update(status='resolved_merged_panel_value',basis='labeled_panel_row_geometry',
                        current=dict(value=item['value'],observation=_performance_panel_record(item['observation'])),
                        projected=dict(value=item['amount'],observation=_performance_panel_record(item['observation'])))
        return evidence
    if len(merged)>1 or (merged and (current or projected)):
        evidence.update(status='unresolved_multiple_panel_value_shapes',current=None,projected=None)
        return evidence
    if not merged and not current and not projected:
        low_merged=[line for line in raw if re.fullmatch(r'\s*\d{1,3}\s*\+\s*\d{1,3}\s*',str(line.get('text','')))]
        if low_merged:
            excluded = [line for line in low_merged if not _performance_panel_line_eligible(line)]
            low = any(line.get('confidence', 0) < minimum_confidence for line in low_merged)
            role_merge = any(str(line.get('role', '')).strip().casefold()
                             == 'merged_current_projection_observation'
                             for line in low_merged)
            status = ('unresolved_low_confidence_merged_panel_value'
                      if low or role_merge else
                      'unresolved_excluded_panel_value_observation'
                      if len(excluded) == len(low_merged) else
                      'unresolved_low_confidence_merged_panel_value')
            evidence.update(status=status,current=None,projected=None)
        else:
            evidence.update(status='no_confident_panel_value',current=None,projected=None)
        return evidence
    if len(current)==1 and len(projected)==1:
        basis='same_row_localized_panel_crop' if any(item.get('localized') for item in current) else 'labeled_panel_row_geometry'
        evidence.update(status='resolved_separate_panel_values',basis=basis,
                        current=dict(value=current[0]['value'],observation=_performance_panel_record(current[0]['observation'])),
                        projected=dict(value=projected[0]['value'],observation=_performance_panel_record(projected[0]['observation'])))
        return evidence
    if len(current)==1 and not projected:
        basis=('slot_widened_panel_crop' if current[0].get('clipped') else
               'same_row_localized_panel_crop' if current[0].get('localized') else
               'labeled_panel_row_geometry')
        evidence.update(status='resolved_current_panel_value',basis=basis,
                        current=dict(value=current[0]['value'],observation=_performance_panel_record(current[0]['observation'])),
                        projected=None)
        return evidence
    evidence.update(status='unresolved_panel_value_conflict',current=None,projected=None)
    return evidence


def _failure_banner_word(text):
    """The result banner's FAILURE word, exact or with its final glyph clipped.

    The outcome animation cuts the last letter of the banner on some frames,
    the same way SUCCESS reads as SUCCES; both spellings are the one word.
    """
    if not isinstance(text, str):
        return False
    word = text.strip().upper().rstrip('!')
    return word in ('FAILURE', 'FAILURE'[:-1])


def performance_panel_facts(lines, screen, stats, regions=None):
    """Read the shared sidebar without letting it award screen-gated gains.

    The persistent panel can establish a current performance-point balance on
    an otherwise unknown screen.  Projected and awarded gains remain tied to
    the classified training screens and use the legacy path, which preserves
    the distinction between a preview and a committed result.
    """
    identity = _performance_panel_identity(lines)
    localized_identity = identity is None
    if identity is None:
        identity = _performance_panel_identity_from_localized(lines, regions, stats)
    if screen not in ('training_preview', 'training_result') and identity is not None:
        result={'performance_points': _performance_panel_values(lines, identity, regions)}
        if localized_identity:
            provenance={}
            for field,_label,label_y,_cap_y in _PERFORMANCE_PANEL_ROWS:
                observation=_performance_panel_field(
                    lines,field,label_y,
                    minimum_confidence=identity.get('value_minimum_confidence',90),
                    regions=regions)
                if observation['status']!='no_confident_panel_value':
                    provenance[field]=observation
            if provenance:
                result['performance_panel_provenance']=provenance
        return result

    eligible = screen in ('training_preview', 'training_result') or (
        stats.get('observation_profile') == 'race_day_lower_totals'
        and all(type(stats.get('values', {}).get(f)) is int for f in FIELDS))
    if not eligible or not _performance_panel_heading(lines):
        return {}
    points={};projected={};provenance={}
    for i,field in enumerate(CURRENCIES):
        observation=_performance_panel_field(lines,field,320+56*i,regions=regions)
        if observation['status'] not in ('no_confident_panel_value',):
            provenance[field]=observation
        current=observation.get('current')
        if current is not None:
            points[field]=current['value']
        award=observation.get('projected')
        if award is not None and (current is not None
                                  or observation.get('status')=='resolved_projected_under_badge'):
            projected[field]=award['value']
    result={'performance_points':points}
    if screen=='training_preview':result['projected_performance_gains']=projected
    elif screen=='training_result':result['awarded_performance_gains']=projected
    if provenance:result['performance_panel_provenance']=provenance
    return result


def _dedicated_result_performance_gains(facts, regions, screen, raw=None):
    """Recover a result-only signed performance badge from its own crop.

    During the result animation the shared sidebar detector can merge the
    current value and award (for example ``5+13``).  The dedicated
    ``performance_gain.<field>`` crop can still read the signed award cleanly
    in that same source frame.  Accept it only when the frame is already a
    committed successful result and the corresponding panel row is explicitly
    marked as an unresolved low-confidence merged value.  This keeps the
    current balance and preview channels independent from the award crop.

    The helper returns the accepted values and conflicts separately so a
    disagreeing direct crop never overwrites a panel award.  No value is
    inferred from a current counter or from another performance row.
    """

    if screen != 'training_result' or not isinstance(facts, dict):
        return {}, {}
    if isinstance(raw, dict) and (
            raw.get('result_grid') is not True or raw.get('current_grid') is True):
        return {}, {}
    if facts.get('training_outcome') != 'success' or facts.get('failure_banner'):
        return {}, {}
    if any(facts.get(key) is True for key in (
            'preview', 'preview_overlay_proven', 'preview_modifier_proven')):
        return {}, {}
    if not isinstance(regions, dict):
        return {}, {}
    panel_provenance = facts.get('performance_panel_provenance')
    if not isinstance(panel_provenance, dict):
        return {}, {}

    accepted = {}
    conflicts = {}
    for field in CURRENCIES:
        panel = panel_provenance.get(field)
        if not isinstance(panel, dict):
            continue
        if panel.get('field') != field or panel.get('status') != (
                'unresolved_low_confidence_merged_panel_value'):
            continue
        band = panel.get('band')
        if (not isinstance(band, (list, tuple)) or len(band) != 4
                or any(type(value) not in (int, float) for value in band)):
            continue
        try:
            band = tuple(float(value) for value in band)
        except (TypeError, ValueError, OverflowError):
            continue
        if (not all(math.isfinite(value) for value in band)
                or not band[0] < band[2] or not band[1] < band[3]):
            continue
        raw_observations = panel.get('raw_observations')
        merged = []
        if isinstance(raw_observations, list):
            for observation in raw_observations:
                if not isinstance(observation, dict):
                    continue
                text = re.sub(r'\s+', '', str(observation.get('text', '')).strip())
                match = re.fullmatch(r'(\d{1,3})\+(\d{1,3})', text)
                if match:
                    merged.append((int(match[1]), int(match[2]), observation))
        # The panel proof itself must retain one merged current+award source
        # observation.  A generic low-confidence line elsewhere is not enough
        # to bind a dedicated signed crop to this field.
        if len(merged) != 1:
            continue
        observation = regions.get('performance_gain.' + field)
        if not isinstance(observation, dict):
            continue
        text = re.sub(r'\s+', '', str(observation.get('text', '')).strip())
        match = re.fullmatch(r'\+(\d{1,3})', text)
        confidence = observation.get('confidence')
        box = observation.get('box')
        if (not match or type(confidence) not in (int, float)
                or not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(value) not in (int, float) for value in box)):
            continue
        try:
            confidence = float(confidence)
            coordinates = tuple(float(value) for value in box)
        except (TypeError, ValueError, OverflowError):
            continue
        if (not math.isfinite(confidence) or not 97 <= confidence <= 100
                or not all(math.isfinite(value) for value in coordinates)
                or not coordinates[0] < coordinates[2]
                or not coordinates[1] < coordinates[3]
                or coordinates[0] < PANE[0] or coordinates[1] < PANE[1]
                or coordinates[2] > PANE[2] or coordinates[3] > PANE[3]):
            continue
        center_x = (coordinates[0] + coordinates[2]) / 2
        center_y = (coordinates[1] + coordinates[3]) / 2
        if not (band[0] <= center_x <= band[2]
                and band[1] <= center_y <= band[3]):
            continue
        amount = int(match[1])
        direct = dict(
            field=field,
            amount=amount,
            region='performance_gain.' + field,
            text=text,
            confidence=float(confidence),
            box=list(box),
            basis='same_frame_dedicated_result_performance_region',
            panel_observation=copy.deepcopy(merged[0][2]),
        )
        panel_amount = merged[0][1]
        if amount != panel_amount:
            conflicts[field] = dict(
                status='unresolved',
                basis='same_frame_dedicated_result_performance_conflict',
                candidate_values=sorted({amount, panel_amount}),
                dedicated=direct,
                panel=copy.deepcopy(merged[0][2]),
            )
            continue
        existing = facts.get('awarded_performance_gains', {})
        existing_amount = existing.get(field) if isinstance(existing, dict) else None
        if existing_amount is None:
            accepted[field] = direct
        elif type(existing_amount) is int and existing_amount == amount:
            # Preserve one canonical value while retaining the dedicated crop
            # proof for later source/evaluator projections.
            accepted[field] = direct
        else:
            conflicts[field] = dict(
                status='unresolved',
                basis='same_frame_dedicated_result_performance_conflict',
                candidate_values=sorted({amount, existing_amount})
                if type(existing_amount) is int else [amount, existing_amount],
                dedicated=direct,
                existing=existing_amount,
            )
    return accepted, conflicts


def enrich_performance_panels(readings):
    """Fill only missing balances from a strict same-reading sidebar.

    This is a source-enrichment pass for replayed readings.  It deliberately
    does not call :func:`parse`, classify a screen, or touch effects, actions,
    stats, timestamps or primary evidence.  Every accepted value is read from
    the row's own ``ocr.neural`` lines after the independent panel identity
    check; it is never copied from a neighboring reading.

    Existing numeric balances remain authoritative.  If an existing value
    disagrees with the independently recognized panel, the row is preserved
    without filling any other field and the conflict is recorded in the
    row-level provenance.  ``None`` is an absent field and may be filled.
    """
    from copy import deepcopy

    if not isinstance(readings, list):
        raise TypeError('readings must be a list')

    def matching_recovery(provenance, row, panel_points):
        """Return whether provenance belongs to this exact source reading.

        A replay may be assembled more than once, and native boundary rows can
        be inserted between assemblies.  The source timestamp and evidence
        identify the reading; recognized values also have to agree so a stale
        recovery record cannot mask a changed OCR result.
        """
        if not isinstance(provenance, dict):
            return False
        if provenance.get('method') != 'strict_performance_panel_identity':
            return False
        if provenance.get('basis') != 'same_reading_ocr_neural':
            return False
        if provenance.get('source_timestamp_ms') != row.get('source_timestamp_ms'):
            return False
        if provenance.get('evidence') != row.get('evidence'):
            return False
        return provenance.get('recognized_values') == panel_points

    def recovery_record(index, row, identity, panel_points, existing_values,
                        added_fields, conflicts, status):
        return dict(
            method='strict_performance_panel_identity',
            basis='same_reading_ocr_neural',
            source_reading_index=index,
            source_timestamp_ms=deepcopy(row.get('source_timestamp_ms')),
            evidence=deepcopy(row.get('evidence')),
            cap_values=deepcopy(identity['cap_values']),
            recognized_values=deepcopy(panel_points),
            existing_values=existing_values,
            added_fields=added_fields,
            conflicts=conflicts,
            status=status,
        )

    enriched = []
    for index, row in enumerate(readings):
        if not isinstance(row, dict):
            enriched.append(deepcopy(row))
            continue
        output = deepcopy(row)
        ocr = row.get('ocr')
        lines = ocr.get('neural', []) if isinstance(ocr, dict) else []
        identity = _performance_panel_identity(lines)
        if identity is None:
            enriched.append(output)
            continue
        panel_points = _performance_panel_values(lines, identity)
        if not panel_points:
            enriched.append(output)
            continue

        original_facts = row.get('facts')
        if original_facts is not None and not isinstance(original_facts, dict):
            # A malformed facts container is outside this source-only pass;
            # preserving it is safer than replacing it with a synthesized one.
            enriched.append(output)
            continue
        facts = deepcopy(original_facts) if isinstance(original_facts, dict) else {}
        existing = facts.get('performance_points')
        existing_values = deepcopy(existing) if isinstance(existing, dict) else {}
        conflicts = {}
        if existing is not None and not isinstance(existing, dict):
            conflicts['performance_points'] = dict(existing=existing, panel=panel_points)
        elif isinstance(existing, dict):
            for field, panel_value in panel_points.items():
                existing_value = existing.get(field)
                if existing_value is not None and (
                    type(existing_value) is not int or existing_value != panel_value
                ):
                    conflicts[field] = dict(existing=existing_value, panel=panel_value)

        prior = facts.get('performance_panel_recovery')
        prior_matches = matching_recovery(prior, row, panel_points)
        if prior_matches and conflicts:
            prior_matches = (isinstance(prior.get('conflicts'), dict)
                              and prior.get('conflicts') == conflicts)
        elif prior_matches and prior.get('status') == 'conflict':
            # The source row now agrees with the panel.  A former conflict is
            # stale and must not survive as if it still described this state.
            prior_matches = False
        added = []
        if not conflicts:
            balances = deepcopy(existing) if isinstance(existing, dict) else {}
            for field, value in panel_points.items():
                if balances.get(field) is None:
                    balances[field] = value
                    added.append(field)
            if balances:
                facts['performance_points'] = balances

            if prior_matches:
                # Preserve the original evidence, added fields and existing
                # values.  Only the positional index can change when replay
                # enrichment inserts native readings before this row.
                preserved = deepcopy(prior)
                preserved['source_reading_index'] = index
                facts['performance_panel_recovery'] = preserved
            elif added:
                facts['performance_panel_recovery'] = recovery_record(
                    index, row, identity, panel_points, existing_values, added,
                    conflicts, 'recovered')
        elif prior_matches and isinstance(prior.get('conflicts'), dict) and prior.get('conflicts') == conflicts:
            # Repeated conflict checks should retain the first diagnostic
            # instead of replacing it with an already-observed record.
            preserved = deepcopy(prior)
            preserved['source_reading_index'] = index
            facts['performance_panel_recovery'] = preserved

        if conflicts and not (prior_matches and isinstance(prior.get('conflicts'), dict)
                              and prior.get('conflicts') == conflicts):
            facts['performance_panel_recovery'] = recovery_record(
                index, row, identity, panel_points, existing_values, [], conflicts,
                'conflict')

        # An already complete, agreeing balance needs no synthetic provenance
        # record.  This also keeps a second pass semantically idempotent for
        # source rows that were never enriched in the first place.
        if facts.get('performance_panel_recovery') is not None or added or conflicts:
            output['facts'] = facts
        enriched.append(output)
    return enriched


def _race_runner_card(raw, lines):
    """Read the gameplay Attributes card into a runner-scoped observation.

    The neural reader receives only the gameplay crop, so this adapter can
    expose card values without consulting the full-frame Career Profile or a
    result row.  OCR cannot establish the card owner; the race helper keeps
    that status unverified until an explicit gameplay owner proof is supplied.
    """
    def normalized(line):
        return re.sub(r'\s+', ' ', str(line.get('text', '')).strip())

    def exact(value, box, minimum=90):
        return [line for line in lines if line.get('confidence', 0) >= minimum
                and normalized(line).lower() == value.lower() and within(line, box)]

    attributes = exact('Attributes', (600, 85, 820, 165))
    if len(attributes) != 1:
        return None

    def number_in(box, minimum=90):
        values=[]
        for line in lines:
            if line.get('confidence', 0) < minimum or not within(line, box):
                continue
            match=re.fullmatch(r'\d{1,4}',normalized(line))
            if match:values.append((int(match[0]),line))
        return values

    stat_labels = {
        'speed': 'Speed', 'stamina': 'Stamina', 'power': 'Power',
        'guts': 'Guts', 'wit': 'Wit',
    }
    stats={}
    for field,label in stat_labels.items():
        labels=exact(label,(620,165,815,365))
        if len(labels) != 1:
            stats[field]=None
            continue
        label_line=labels[0];top=label_line['box'][1]-12;bottom=label_line['box'][3]+12
        values=number_in((800,top,950,bottom))
        stats[field]=values[0][0] if len(values)==1 else None

    race_grade_values=[normalized(line) for line in lines if line.get('confidence',0)>=90
                       and within(line,(260,0,390,80))
                       and re.fullmatch(r'(?:G[123]|OP|EX|PRE-OP)',normalized(line),re.I)]
    race_names=[_split_race_grade(normalized(line))[1] for line in lines if line.get('confidence',0)>=90
                and within(line,(430,0,760,80))
                and any(char.isalpha() for char in normalized(line))
                and not re.fullmatch(r'(?:G[123]|OP|EX|PRE-OP)',normalized(line),re.I)]
    race_name=race_names[0] if len(set(race_names))==1 else None
    race_grade=race_grade_values[0] if len(set(race_grade_values))==1 else None

    runner_names=[normalized(line) for line in lines if line.get('confidence',0)>=90
                  and within(line,(430,650,700,760))
                  and any(char.isalpha() for char in normalized(line))]
    runner_name=runner_names[0] if len(set(runner_names))==1 else None
    bib_values=[]
    for line in lines:
        if line.get('confidence',0)<90 or not within(line,(140,850,700,960)):
            continue
        match=re.search(r'\bNo\.\s*(\d{1,2})\s*[,.:]',normalized(line),re.I)
        if match:bib_values.append(int(match[1]))
    bib_number=bib_values[0] if len(set(bib_values))==1 else None

    aptitude={}
    for field,label in (('turf','Turf'),('medium','Medium'),('pace','Pace')):
        labels=exact(label,(730,350,845,475))
        if len(labels)!=1:
            aptitude[field]=None
            continue
        label_line=labels[0];center=(label_line['box'][1]+label_line['box'][3])/2
        values=[normalized(line) for line in lines if line.get('confidence',0)>=90
                and within(line,(830,center-25,920,center+25))
                and re.fullmatch(r'[SABC]',normalized(line),re.I)]
        aptitude[field]=values[0].upper() if len(set(values))==1 else None

    mood_labels=exact('Mood',(620,450,760,530))
    mood_values=[normalized(line) for line in lines if line.get('confidence',0)>=90
                 and within(line,(780,450,950,535))
                 and re.fullmatch(r'[A-Z][A-Z ]{2,20}',normalized(line))]
    mood=mood_values[0] if len(set(mood_values))==1 else None

    strategy={field:None for field in ('end','late','pace','front')}
    strategy_labels=[]
    for field,label in (('end','END'),('late','LATE'),('pace','PACE'),('front','FRONT')):
        matches=exact(label,(720,535,950,600))
        if len(matches)==1:
            strategy_labels.append((field,(matches[0]['box'][0]+matches[0]['box'][2])/2))
    strategy_values=[]
    for line in lines:
        if line.get('confidence',0)<90 or not within(line,(720,580,950,635)):
            continue
        match=re.fullmatch(r'\d{1,2}',normalized(line))
        if match:strategy_values.append((int(match[0]),(line['box'][0]+line['box'][2])/2))
    for value,x in strategy_values:
        if not strategy_labels:continue
        field,label_x=min(strategy_labels,key=lambda item:abs(item[1]-x))
        if abs(label_x-x) <= 35 and strategy[field] is None:strategy[field]=value

    change=exact('Change',(760,590,900,675))
    runners=exact('Runners',(800,700,950,800))
    controls={
        'change': {
            'label': 'Change',
            'visible': True if len(change)==1 else None,
            'enabled': None,
            'state': 'unknown',
        },
        'navigation': {
            'left_arrow_visible': None,
            'right_arrow_visible': None,
            'runners_button_visible': True if len(runners)==1 else None,
        },
    }
    card={
        'gameplay_only': True,
        'source_timestamp_ms': raw.get('source_timestamp_ms'),
        'source_frame_sha256': raw.get('source_frame_sha256'),
        'source_sha256': raw.get('source_sha256'),
        'evidence': raw.get('evidence'),
        'race_name': race_name,
        'race_grade': race_grade,
        'runner_name': runner_name,
        'bib_number': bib_number,
        'stats': stats,
        'aptitude': aptitude,
        'mood': mood,
        'strategy_counts': strategy,
        'selected_card_controls': controls,
    }
    from .race_runner_scope import read_runner_facts
    return read_runner_facts(card)


def parse(raw):
    lines=raw['lines'];regions=raw['regions'];text='\n'.join(l['text'] for l in lines if l['confidence']>=90)
    header=raw['header']
    if raw.get('inspection')=='training_result_only' and not header:
        observed=regions.get('header',{})
        if observed.get('text','').strip().lower()=='training' and observed.get('confidence',0)>=85:
            header='Training'
    stat_value_provenance={}
    def same_frame_numeric_region(prefix,field):
        """Cross-check a weak numeric crop against one same-frame OCR line."""
        if prefix!='current':
            return None
        name=prefix+'.'+field
        observation=regions.get(name,{})
        if not isinstance(observation,dict):
            return None
        raw_text=re.sub(r'\s+','',str(observation.get('text','')).strip())
        if not re.fullmatch(r'\d{1,4}',raw_text):
            return None
        box=observation.get('box')
        if not isinstance(box,(list,tuple)) or len(box)!=4:
            return None
        try:
            left,top,right,bottom=[float(value) for value in box]
        except (TypeError,ValueError):
            return None
        expanded=(left-8,top-8,right+8,bottom+8)
        candidates=[]
        for line in lines:
            if line.get('confidence',0)<95 or not within(line,expanded):
                continue
            line_text=re.sub(r'\s+','',str(line.get('text','')).strip())
            if line_text==raw_text and re.fullmatch(r'\d{1,4}',line_text):
                candidates.append(line)
        if len(candidates)!=1:
            return None
        stat_value_provenance[field]=dict(
            basis='same_frame_numeric_region_line_crosscheck',
            region=dict(text=observation.get('text',''),confidence=observation.get('confidence'),box=list(box)),
            line=dict(text=candidates[0].get('text',''),confidence=candidates[0].get('confidence'),box=list(candidates[0].get('box',[]))),
        )
        return int(raw_text)
    def values(prefix,fields=FIELDS):
        result={}
        for field in fields:
            value=number(regions.get(prefix+'.'+field,{}))
            if value is None:
                value=same_frame_numeric_region(prefix,field)
            result[field]=value
        return result
    def currencies(modal=False):
        # Full detector boxes preserve leading digits that tight fixed crops can cut off.
        result={}
        for i,field in enumerate(CURRENCIES):
            box=(382+83*i,840,436+83*i,883) if modal else (337+104*i,85,407+104*i,126)
            candidates=[l for l in lines if within(l,box)]
            # A detector can merge adjacent counters during the modal fade.
            # Such a box is not an observation of either individual currency.
            numbers=[number(l) for l in candidates if number(l) is not None
                     and l['box'][2]-l['box'][0]<=(75 if modal else 100) and number(l)<=999]
            result[field]=numbers[0] if len(numbers)==1 else None
            if not numbers:
                # An empty balance is drawn as a dim grey 0 that the reader
                # returns with low confidence or as the slot's only token.
                # A lone single-digit '0' is that zero; anything else stays unread.
                dim=[l for l in candidates if l.get('text','').strip()=='0' and l.get('confidence',0)>=50
                     and l['box'][2]-l['box'][0]<=40]
                if len(dim)==1 and len(candidates)==1:
                    result[field]=0
                    facts.setdefault('dim_zero_currency_fields',[]).append(dict(field=field,modal=modal,
                        confidence=dim[0].get('confidence'),box=dim[0].get('box')))
            wide=regions.get(('wide_projected_performance.' if modal else 'wide_performance.')+field,{})
            value=number(wide)
            if value is not None and value<=999:
                # Wider crops are purpose-built to include the full counter;
                # tight legacy numeric regions are deliberately not consulted.
                result[field]=value
            padding=raw.get('currency_padding',{}).get(field) if not modal else None
            if padding is not None:
                from .refine_skill_points import counter_reading
                value,conflicts=counter_reading(padding,result[field])
                result[field]=value if not conflicts and type(value) is int and 0<=value<=999 else None
        return result
    preview=training_preview(raw)
    result_grid=raw['result_grid'] and not preview
    if raw.get('inspection')=='training_result_only' and header.lower().startswith('training'):
        count=sum(bool(re.fullmatch(r'\d{1,4}/\d{4}',regions.get('result.'+f,{}).get('text',''))) and regions.get('result.'+f,{}).get('confidence',0)>=90 for f in FIELDS[:5])
        result_grid=result_grid or count>=2
    screen=classify(text,header,result_grid,preview)
    if 'Go on a fun outing?' in text and 'entire turn' in text:screen='outing_confirmation'
    elif 'Recreation' in text and 'Event Progress' in text and 'Trainee Umamusume' in text:screen='outing_selection'
    if 'Concert bonuses updated!' in text:screen='concert_bonus_update'
    elif 'Concert Info' in text and 'Concert Bonus Changes' in text:screen='concert_info'
    if screen=='unknown' and raw['header'].lower().startswith('complete career') and 'remaining performance points' in text.lower():screen='career_completion_hub'
    if screen=='unknown' and raw['header'].lower().startswith('training') and not raw['current_grid']:
        lower=[l['text'] for l in lines if l['confidence']>=90 and within(l,(260,790,850,1005))]
        labels={t for t in lower if t in ('Speed','Stamina','Power','Guts','Wit','Skill Pts')}
        totals=[t for t in lower if re.search(r'\d{1,4}/\d{3,4}',t)]
        if len(labels)>=2 and len(totals)>=2:screen='training_result_candidate'
    # A strong, fully contained result banner can resolve the detector's
    # unknown/candidate classification.  The helper keeps preview frames and
    # fuzzy or weak banner text unresolved, so result parsing remains gated by
    # the source-backed screen classification.
    from .training_outcome import classify_result_screen, training_result_scaffold_visible
    screen = classify_result_screen(lines, header, screen)
    # Large animation badges over a visible result scaffold identify the
    # applied-result frame even before the outcome banner is legible.  The
    # badge amounts are recorded as observations; ownership is resolved by
    # the bounded recovery pass against the same window's result totals.
    animation_badges = []
    if header.lower().startswith('training') and not raw['current_grid'] and screen in ('unknown', 'training_result_candidate'):
        animation_badges = animation_gain_badges(lines)
        if animation_badges and training_result_scaffold_visible(lines):
            screen = 'training_result'
    outcome_lines=[l for l in lines if l['confidence']>=95 and within(l,(250,770,850,1000))]
    for first in list(outcome_lines):
        if first['text'].startswith('Learned ') and not re.search(r'[.!]$',first['text']):
            following=[l for l in lines if 90<=l['confidence']<95 and 0<l['box'][1]-first['box'][1]<40 and re.fullmatch(r'Class[.!]',l['text'])]
            outcome_lines.extend(following)
    # A receipt that lost its number often reads under the band's gate; the
    # gain popup naming its stat on the same frame vouches for the line.
    from .gameplay import cut_receipt_lines
    outcome_lines.extend(l for l in cut_receipt_lines(lines,(250,770,850,1000)) if l not in outcome_lines)
    outcome_lines.sort(key=lambda l:(l['box'][1],l['box'][0]))
    from .receipt_wrapping import join as join_wrapped_friendship_receipts
    outcome_lines=join_wrapped_friendship_receipts(
        outcome_lines,
        raw.get('wrapped_receipt_observations'),
        raw,
    )
    # A hint receipt whose fixed wording an obstruction damaged, its number
    # and its name proven clear by the same obstruction's geometry, is
    # repaired to what those fixed words say. This happens before the lines
    # are joined: damaged wording also hides the name that wrapped onto the
    # following line.
    obstructed={(item.get('text'),tuple(item.get('box') or ()))
                for item in raw.get('resolved_receipt_occlusions') or []
                if isinstance(item,dict) and item.get('basis')=='overlay_covers_fixed_hint_wording'}
    if obstructed:
        from .receipt_grammar import hint_wording
        for position,line in enumerate(outcome_lines):
            if (line['text'],tuple(line['box'])) not in obstructed:continue
            mended=hint_wording(line['text'])
            if mended:outcome_lines[position]=dict(line,text=mended,original_hint_wording=line['text'])
    # The same for the fixed subject word of a stat or performance receipt
    # the obstruction damaged ("Yocals went up by 20." under the cursor).
    obstructed_subjects={(item.get('text'),tuple(item.get('box') or ()))
                         for item in raw.get('resolved_receipt_occlusions') or []
                         if isinstance(item,dict) and item.get('basis')=='overlay_covers_fixed_subject_word'}
    if obstructed_subjects:
        from .receipt_grammar import subject_receipt
        for position,line in enumerate(outcome_lines):
            if (line['text'],tuple(line['box'])) not in obstructed_subjects:continue
            mended=subject_receipt(line['text'])
            if mended:outcome_lines[position]=dict(line,text=mended,original_subject_word=line['text'])
    # And the direction word such a receipt lost under the obstruction
    # ("Speed went  by 5."), the frame's gain popup having stated which way.
    obstructed_directions={(item.get('text'),tuple(item.get('box') or ())):item.get('direction')
                           for item in raw.get('resolved_receipt_occlusions') or []
                           if isinstance(item,dict) and item.get('basis')=='overlay_covers_fixed_direction_word'}
    if obstructed_directions:
        from .receipt_grammar import direction_receipt
        for position,line in enumerate(outcome_lines):
            direction=obstructed_directions.get((line['text'],tuple(line['box'])))
            mended=direction_receipt(line['text'],direction) if isinstance(direction,str) else None
            if mended:outcome_lines[position]=dict(line,text=mended,original_direction_word=line['text'])
    joined=[];index=0
    while index<len(outcome_lines):
        line=outcome_lines[index]
        if re.match(r'Learned the song ["“]',line['text']) and not re.search(r'["”][.!]$',line['text']) and index+1<len(outcome_lines):
            following=outcome_lines[index+1]
            if 0<following['box'][1]-line['box'][1]<40 and re.fullmatch(r'[^"“”]+["”][.!]',following['text']):
                original_prefix=line.get('original_symbol_text',line['text'])
                line=dict(line,text=line['text']+' '+following['text'],confidence=min(line['confidence'],following['confidence']));index+=1
                # A symbol on the continuation belongs to the joined receipt.
                # Keep its physical source lines in the observation and the
                # complete original sentence alongside the normalized effect.
                if following.get('visual_symbol_observation') and isinstance(following.get('original_symbol_text'),str):
                    line.update(original_symbol_text=original_prefix+' '+following['original_symbol_text'],
                                visual_symbol_observation=following['visual_symbol_observation'])
        elif line['text'].startswith('Learned ') and not re.search(r'[.!]$',line['text']) and index+1<len(outcome_lines):
            following=outcome_lines[index+1]
            if (0<following['box'][1]-line['box'][1]<40 and abs(following['box'][0]-line['box'][0])<=15
                and len(following['text'].split())<=4 and re.fullmatch(r'[A-Z][^.!?]{0,60}[.!]',following['text'])
                and not effects_from_lines([following])):
                line=dict(line,text=line['text']+' '+following['text'],confidence=min(line['confidence'],following['confidence']));index+=1
        elif re.match(r'Gained \d+ hint level\(?s?\)? for ',line['text']) and not re.search(r'[.!]$',line['text']) and index+1<len(outcome_lines):
            following=outcome_lines[index+1]
            if (0<following['box'][1]-line['box'][1]<40 and abs(following['box'][0]-line['box'][0])<=15
                and len(following['text'].split())<=4 and re.search(r'[.!]$',following['text']) and not effects_from_lines([following])):
                mended=line.get('original_hint_wording')
                line=dict(line,text=line['text']+' '+following['text'],confidence=min(line['confidence'],following['confidence']));index+=1
                # The receipt as it was read spans both physical lines.
                if mended:line['original_hint_wording']=mended+' '+following['text']
        joined.append(line);index+=1
    repairs={}
    from .receipt_grammar import normalize as normalize_receipt_grammar, boundary_repair_supported
    for i,line in enumerate(joined):
        # The optional plural marker is fixed UI grammar. Missing marker
        # characters cannot supply a missing digit or change the skill name.
        fixed=normalize_receipt_grammar(line['text'],allow_boundary_repair=boundary_repair_supported(line,raw.get('receipt_overlay_evidence')))
        fixed=re.sub(r'^(Gained \d+ hint level)\(?s?\)?( for \S.*[.!])$',r'\1(s)\2',fixed)
        fixed=re.sub(r'^((?:Speed|Stamina|Power|Guts|Wit|Skill Pts)) (?:wet|welt) ((?:up|down) by \d+[.!]?)$',r'\1 went \2',fixed)
        fixed=re.sub(r'^((?:Speed|Stamina|Power|Guts|Wit|Skill Pts|Dance|Passion|Vocals?|Visuals?|Composure) went (?:up|down) by)(\d+[.!]?)$',r'\1 \2',fixed)
        fixed=re.sub(r'^(Energy recovered by)(\d+[.!]?)$',r'\1 \2',fixed)
        fixed=re.sub(r'^(Energy went (?:up|down))by\s*(\d+[.!]?)$',r'\1 by \2',fixed)
        fixed=re.sub(r'^(Energy went (?:up|down) by)(\d+[.!]?)$',r'\1 \2',fixed)
        fixed=re.sub(r"^(Friendship with .+?)(?<!\s)(didn't go up[.!]?)$",r'\1 \2',fixed)
        fixed=re.sub(r'^(Friendship with .+? is)maxed out([.!]?)$',r'\1 maxed out\2',fixed)
        # Repair only the fixed receipt keyword. Recipient spelling, amount,
        # confidence and the complete past-tense sentence remain untouched.
        fixed=re.sub(r'^(?:Friewdship|Frendship|Frienlship)( with .+? went up by \d+[.!])$',r'Friendship\1',fixed)
        fixed=re.sub(r'^(Friendship with .+?) (?:wert|ent) (up by \d+[.!])$',r'\1 went \2',fixed)
        fixed=re.sub(r'^Friendship wh (.+? went up by \d+[.!])$',r'Friendship with \1',fixed)
        if fixed!=line['text']:
            repairs[fixed]=line['text'];joined[i]=dict(line,text=fixed)
    parsed_effects=effects_from_lines(joined,popups=lines)
    for effect in parsed_effects:
        symbol_line=next((l for l in joined if l['text']==effect['raw_text'] and l.get('visual_symbol_observation')),None)
        if symbol_line:
            effect.update(original_text=symbol_line['original_symbol_text'],visual_symbol_observation=symbol_line['visual_symbol_observation'])
        wrapped_line=next((l for l in joined if l.get('text')==effect['raw_text']
                           and (l.get('wrapped_receipt_proof')
                                or l.get('wrapped_receipt_kind'))),None)
        if wrapped_line:
            effect.update(
                wrapped_receipt_parts=copy.deepcopy(wrapped_line.get('wrapped_receipt_parts', [])),
            )
            if wrapped_line.get('wrapped_receipt_proof'):
                effect['wrapped_receipt_proof']=copy.deepcopy(wrapped_line['wrapped_receipt_proof'])
            if (wrapped_line.get('original_text') != effect['raw_text']
                    or wrapped_line.get('wrapped_receipt_kind')):
                effect['original_text']=wrapped_line.get('original_text')
                effect['text_normalization']=wrapped_line.get(
                    'text_normalization', 'source_wrapped_friendship_receipt')
        if effect['raw_text'] in repairs:
            effect['original_text']=repairs[effect['raw_text']];effect['text_normalization']='fixed_receipt_verb'
        wording_line=next((l for l in joined if l['text']==effect['raw_text'] and l.get('original_hint_wording')),None)
        if wording_line:
            effect['original_text']=wording_line['original_hint_wording']
            effect['text_normalization']='obstructed_hint_wording'
        subject_line=next((l for l in joined if l['text']==effect['raw_text'] and l.get('original_subject_word')),None)
        if subject_line:
            effect['original_text']=subject_line['original_subject_word']
            effect['text_normalization']='obstructed_subject_word'
        direction_line=next((l for l in joined if l['text']==effect['raw_text'] and l.get('original_direction_word')),None)
        if direction_line:
            effect['original_text']=direction_line['original_direction_word']
            effect['text_normalization']='obstructed_direction_word'
    # Typewriter/fade frames can expose a prefix such as "... by 5" of "... by 57."
    # Numeric receipts require their visible sentence terminator in this layout,
    # except one whose number came from the gain popup: its line never had one.
    effects=[e for e in parsed_effects if e.get('amount') is None or e.get('amount_basis')=='gain_popup_on_same_frame'
             or re.search(r'[.!]$',e['raw_text'])]
    pending_effects=[e for e in parsed_effects if e not in effects]
    if screen not in ('unknown','event_outcome'):
        effects=[];pending_effects=[]
    if (effects or pending_effects) and screen=='unknown':screen='event_outcome'
    # A centered ``+N Energy`` popup can remain readable while its lower
    # ``Energy recovered by N.`` receipt is covered by an animated particle.
    # Keep this channel source-bound and separate from the ordinary receipt
    # grammar.  Equal amounts are merged into one effect with both proofs;
    # conflicting amounts remain visible for later reconciliation.
    energy_popup = None
    if screen in ('unknown', 'event_outcome'):
        from .energy_popup import merge_energy_popup_effects, read_energy_popup
        energy_popup = read_energy_popup(raw)
        if energy_popup is not None:
            effects = merge_energy_popup_effects(effects, energy_popup)
            screen = 'event_outcome'
    from .item_reward_banner import read_reward
    reward=read_reward(raw)
    if reward and screen in ('unknown','event_outcome'):
        effects.append(reward)
        screen='event_outcome'
    # A condition-cured banner is a source-backed gameplay result.  Merge it
    # only after the ordinary receipt grammar has run so a complete receipt
    # keeps its original text while the banner contributes its independent
    # visual proof.  Modal previews and training/result panels stay outside
    # this guard and cannot be promoted by a coincidental heading.
    if screen in ('unknown','event_outcome'):
        from .condition_removal_banner import (
            merge_condition_removal_effects,
            read_condition_cured_banner,
        )
        condition_banner=read_condition_cured_banner(raw)
        if condition_banner:
            effects=merge_condition_removal_effects(effects,condition_banner)
            if screen=='unknown':
                screen='event_outcome'
    options=[]
    for l in lines:
        if within(l,(210,160,420,200)) and l['confidence']>=90:
            m=re.fullmatch(r'(Speed|Stamina|Power|Guts|Wit)\s+Lvl\s*\d*',l['text'],re.I)
            if m:options.append(m[1].lower())
    option=options[0] if len(set(options))==1 and options else None
    recovery_option = None
    if preview and option is None and isinstance(raw.get('preview_recovery'), dict):
        from .preview_recovery import validated_recovery
        verified_preview = validated_recovery(raw)
        recovery_option = verified_preview.get('option') if verified_preview else None
    if option is None and preview and isinstance(recovery_option, str):
        option = recovery_option if recovery_option in FIELDS else None
    from .stat_state_details import read_goal_turns
    countdown, countdown_provenance = read_goal_turns(
        lines, regions.get('countdown'))
    stats=dict(values=values('current') if raw['current_grid'] else None,training_preview=preview,
               preview_option=option if preview else None,completed_action=None,
               turns_remaining_to_goal=countdown)
    if countdown_provenance:
        stats['turns_remaining_provenance'] = countdown_provenance
    if stat_value_provenance:
        stats['value_provenance']=stat_value_provenance
    if not raw['current_grid']:
        from .race_hub_stats import observation as race_hub_observation
        race_totals=race_hub_observation(lines,grid_verified=raw.get('race_hub_grid_verified') is True)
        if race_totals:
            stats.update(values=race_totals['values'],observation_profile=race_totals['profile'],
                         field_observations=race_totals['field_observations'])
    calendar=[l['text'] for l in lines if within(l,(390,28,830,62)) and l['confidence']>=95
              and re.fullmatch(r'(?:Junior|Classic|Senior) Year (?:Pre-Debut|(?:Early|Late) [A-Z][a-z]{2})|Finale Underway',l['text'])]
    stats['calendar_text']=' '.join(calendar) or None
    facts={}
    if energy_popup is not None:
        # Preserve the independent visual proof even when a complete receipt
        # supplied the canonical effect and the merge avoided a duplicate.
        facts['energy_popup_observation'] = copy.deepcopy(energy_popup)
    # Keep the explicitly typed training overlay beside the ordinary facts.
    # It is a source observation only; preview_observations will keep it out
    # of committed/applied effect reconstruction.
    from .preview_observations import parse_preview_overlay
    facts.update(parse_preview_overlay(raw))
    if isinstance(raw.get('result_card_occlusion'), dict):
        # Pixel-backed result-card occlusion is diagnostic metadata.  It does
        # not supply a result value and remains separate from numeric effects.
        facts['result_card_occlusion'] = copy.deepcopy(raw['result_card_occlusion'])
    if isinstance(raw.get('preview_recovery'), dict):
        facts['preview_recovery'] = copy.deepcopy(raw['preview_recovery'])
    from .status_badges import read_badges
    facts['status_badges'] = read_badges(lines)
    from .training_identity import read_identity
    identity_option = option.capitalize() if option else None
    identity_screen = screen
    if (identity_screen not in ('training_result', 'training_preview', 'training')
            and header.strip().casefold().startswith('training')):
        # A transition frame can expose the selected training heading/name
        # before either grid classifier fires. Keep that source identity as
        # observation metadata; the existing result action linkage remains
        # the only commitment signal.
        identity_screen = 'training'
    training_identity = read_identity(lines, identity_screen, identity_option)
    if training_identity:
        facts.update(training_identity)
    from .stat_state_details import read_main_stat_caps
    main_stat_caps,main_stat_cap_proof=read_main_stat_caps(raw)
    from .numeric_cap_refinement import read_stat_caps
    refined_stat_caps, refined_stat_cap_proof = read_stat_caps(
        lines, regions, result_grid=raw.get('result_grid') is True,
        current_grid=raw.get('current_grid') is True)
    for field, value in refined_stat_caps.items():
        if field not in main_stat_caps:
            main_stat_caps[field] = value
            main_stat_cap_proof[field] = refined_stat_cap_proof[field]
    if main_stat_caps:
        facts['stat_caps']=main_stat_caps
        facts['stat_cap_provenance']=main_stat_cap_proof
    # Performance caps are a separate source channel from current/projected
    # points.  Keep their proof beside the panel values so state assembly can
    # attach caps to the same reading without choosing a value from deltas.
    from .numeric_cap_refinement import read_performance_caps
    performance_caps, performance_cap_proof = read_performance_caps(lines, regions)
    if performance_caps:
        facts['performance_caps'] = performance_caps
        facts['performance_cap_provenance'] = performance_cap_proof
    if screen=='race_result' and raw.get('race_identity_refinement'):
        facts['race_identity_refinement']=raw['race_identity_refinement']
    achieved=[l for l in lines if l['confidence']>=95
              and within(l,(440,85,760,116)) and l['text'].strip()=='Goal Achieved!']
    if len(achieved)==1:
        facts['goal_status_observation']=dict(status='achieved',raw_text=achieved[0]['text'],
            confidence=achieved[0]['confidence'],box=achieved[0]['box'],
            semantics='visible_status_only; completion_time_and_rewards_not_inferred')
    if raw.get('occluded_receipt_lines'):
        facts['occluded_receipt_lines']=raw['occluded_receipt_lines']
    if raw.get('resolved_receipt_occlusions'):
        facts['resolved_receipt_occlusions']=raw['resolved_receipt_occlusions']
    if raw.get('receipt_overlay_evidence'):
        facts['receipt_overlay_evidence']=raw['receipt_overlay_evidence']
    if screen=='concert_bonus_update':facts['bonus_update_receipt']=True
    if screen=='concert_info':
        current={};planned={};bonus_evidence={}
        for i,field in enumerate(('friendship_training_effectiveness','specialty_priority','support_chain_event_frequency')):
            observations=[l for l in lines if l['confidence']>=90 and within(l,((270,460,650)[i],380,(460,650,840)[i],425))]
            for observation in observations:
                value=re.sub(r'\s+','',observation['text'])
                # The level slot is numeric. Preserve this constrained OCR
                # normalization rather than treating a missing field as zero.
                if i==2:value=re.sub(r'(?<=Lvl)O(?=Lvl|[>▶→]|$)','0',value)
                pair=re.fullmatch(r'\+(\d+)%?[>▶→]?\+(\d+)%?',value) if i<2 else re.fullmatch(r'Lvl(\d+)[>▶→]?Lvl(\d+)',value)
                single=re.fullmatch(r'\+(\d+)%?',value) if i<2 else re.fullmatch(r'Lvl(\d+)',value)
                if pair:current[field]=int(pair[1]);planned[field]=int(pair[2])
                elif single:current[field]=int(single[1]);planned[field]=int(single[1])
                if pair or single:bonus_evidence[field]=dict(observation,normalized_text=value)
        facts.update(current_concert_bonuses=current,planned_concert_bonuses=planned,
                     concert_bonus_evidence=bonus_evidence,
                     bonus_snapshot_is_not_activation=True)
    if pending_effects:facts['effect_candidates']=pending_effects
    from .animated_performance import candidates
    animation=candidates(lines,screen)
    if animation:facts['animated_performance_candidates']=animation
    stat_animation=candidates(lines,screen,stat=True)
    if stat_animation:facts['animated_stat_candidates']=stat_animation
    if screen=='career_summary':
        final={}
        for i,field in enumerate(FIELDS[:5]):
            candidates=[l for l in lines if within(l,(305+113*i,282,382+113*i,324)) and number(l) is not None]
            final[field]=number(candidates[0]) if len(candidates)==1 else None
        facts['final_attributes']=final
        facts['owned_skill_list_complete']=False
        from .inventory import visible_cards
        facts['visible_owned_skill_cards']=visible_cards(raw,final)
    if screen=='career_completion_hub':
        final={}
        for i,field in enumerate(FIELDS[:5]):
            candidates=[l for l in lines if within(l,(718,316+31*i,785,349+31*i)) and number(l) is not None]
            final[field]=number(candidates[0]) if len(candidates)==1 else None
        point_lines=[l for l in lines if l['confidence']>=95 and within(l,(280,900,425,936))]
        matches=[re.fullmatch(r'Skill Pts\s*(\d{1,4})',l['text']) for l in point_lines]
        numbers={int(m[1]) for m in matches if m}
        final['skill_points']=numbers.pop() if len(numbers)==1 else None
        stats['values']=final
        facts['current_skill_points']=final['skill_points']
        facts['final_attributes']=final
    if screen=='career_finish_confirmation':
        # These are remaining balances inside the modal, not spent points or
        # proof that the user finished the career rather than canceling.
        has_label,skill_points,skill_point_proof=terminal_skill_point_observation(lines)
        if has_label:
            facts['current_skill_points']=skill_points
            if skill_point_proof:
                facts['current_skill_points_provenance']=skill_point_proof
        if any(l['confidence']>=97 and l['text']=='Remaining Performance Points'
               and within(l,(400,595,710,635)) for l in lines):
            boundaries=(335,425,535,625,720,815);remaining={}
            for i,field in enumerate(CURRENCIES):
                values={number(l) for l in lines if within(l,(boundaries[i],632,boundaries[i+1],678))
                        and number(l) is not None}
                remaining[field]=values.pop() if len(values)==1 else None
            facts['remaining_performance_points']=remaining
    if screen=='career_account_totals':
        # Post-career account awards are not another race or career stat gain.
        facts['counts_as_career_action']=False
        for name,box in (('account_fans',(275,400,850,465)),('monthly_fans',(275,535,850,590))):
            matches=[re.fullmatch(r'([\d,]+)\s*\(\s*\+([\d,]+)\s*\)',l['text'])
                     for l in lines if l['confidence']>=97 and within(l,box)]
            values={(int(m[1].replace(',','')),int(m[2].replace(',',''))) for m in matches if m}
            if len(values)==1:
                total,increase=values.pop();facts[name]=dict(total=total,increase=increase)
        if 'monthly_fans' in facts:
            facts['monthly_fans']['scope']='club' if any(l['text']=='Club' and l['confidence']>=97
                                                       and within(l,(300,500,400,540)) for l in lines) else 'unknown'
        levels={number(l) for l in lines if within(l,(480,205,550,255)) and number(l) is not None}
        if len(levels)==1:facts['bond_level']=levels.pop()
        matches=[re.fullmatch(r'([\d,]+)/([\d,]+)\(\+([\d,]+)\)',l['text'])
                 for l in lines if l['confidence']>=97 and within(l,(550,225,850,280))]
        values={tuple(int(part.replace(',','')) for part in m.groups()) for m in matches if m}
        if len(values)==1:
            current,required,increase=values.pop()
            if 0<=current<=required and required>0:facts['bond_progress']=dict(current=current,required=required,increase=increase)
    if screen=='training_result':
        from .training_outcome import banner_facts, source_bound_banner_facts
        from .stat_state_details import read_training_result_values
        facts.update(banner_facts(lines,screen))
        # A validated weak-state sidecar carries the exact result-banner crop
        # beside the raw detector lines.  Promote it only through the strict
        # source-bound hook; ordinary OCR and training previews remain subject
        # to the existing outcome rules.
        facts.update(source_bound_banner_facts(raw, screen=screen))
        if animation_badges:
            facts['animation_gain_badges'] = animation_badges
        occluded_values, occluded_value_provenance = read_training_result_values(raw)
        gains={}; totals={};caps={};gain_candidates={};gain_provenance={};result_candidates={};digit_crosschecks=[];partial_results={}
        result_value_sources={}
        result_refinement_provenance={}
        for field in FIELDS:
            resolution=resolve_gain_regions(regions,field)
            gain_provenance[field]=resolution
            gain_candidates[field]=source_amounts(resolution['candidates'],resolution)
            if type(resolution.get('canonical_amount')) is int:
                gains[field]=resolution['canonical_amount']
            r=regions.get('gain.'+field,{})
            result_region=regions.get('result.'+field,{})
            refined_result_region=regions.get('numeric_result.'+field,{})
            refined_result_applied = False
            refined_ratio = (refined_result_region if isinstance(refined_result_region,dict)
                             and re.fullmatch(r'(\d{1,4})/(\d{3,4})',
                                              re.sub(r'\s+','',str(refined_result_region.get('text','')).strip()))
                             else None)
            if refined_ratio:
                original_ratio = re.fullmatch(
                    r'(\d{1,4})/(\d{3,4})',
                    re.sub(r'\s+','',str(result_region.get('text','')).strip()))
                original_ratio_is_complete = bool(
                    original_ratio
                    and 1000 <= int(original_ratio[2])
                    and int(original_ratio[1]) <= int(original_ratio[2]))
                if not original_ratio_is_complete:
                    result_refinement_provenance[field] = copy.deepcopy(refined_result_region)
                    result_region = refined_result_region
                    refined_result_applied = True
            tight_digits=number(result_region)
            signed=re.fullmatch(r'\+(\d{1,3})',r.get('text',''))
            if (type(resolution.get('canonical_amount')) is int and signed
                and r.get('confidence',0)>=80
                and tight_digits==int(signed[1])):
                digit_crosschecks.append(field)
            r=result_region
            m=re.fullmatch(r'(\d{1,4})/(\d{3,4})',
                           re.sub(r'\s+','',str(r.get('text','')).strip()))
            if m and 1000<=int(m[2]) and int(m[1])<=int(m[2]):
                if r.get('confidence',0)>=90:result_candidates[field]=int(m[1])
                # The ordinary OCR threshold remains strict.  A complete
                # ratio from a validated, source-bound reread has already
                # passed its own geometry, source-anchor, and provenance
                # checks, so it can supply the canonical snapshot at the
                # sidecar's lower (>=90) recognition threshold.
                cap_proof = occluded_value_provenance.get(field, {})
                cap_is_source_agreed = (
                    not refined_result_applied
                    and cap_proof.get('cap_status') == 'readable'
                    and cap_proof.get('cap') == int(m[2])
                )
                if r.get('confidence',0)>=97 or refined_result_applied:
                    totals[field]=int(m[1])
                    # A cap is canonical only when the same-frame detector
                    # ratio agrees with the result crop.  Validated numeric
                    # refinements retain their own source-bound cap contract.
                    if refined_result_applied or cap_is_source_agreed:
                        caps[field]=int(m[2])
                    result_value_sources[field] = dict(
                        source=('numeric_result.' if refined_result_applied else 'result.') + field,
                        observation=copy.deepcopy(r))
            if field!='skill_points':
                from .result_counter import partial_counter
                partials=[]
                for view,observation in (('original',r),('padded',raw.get('result_numerator_refinement',{}).get('result.'+field,{}))):
                    partial=partial_counter(observation)
                    if partial:partials.append(dict(partial,source_view=view))
                if partials:partial_results[field]=partials
        # A result-card numerator can remain legible while its cap is covered
        # by the animation.  The shared reader has already tied this value to
        # the same-frame label and fixed card geometry; let it fill only a
        # field still absent from the strict complete-ratio path.
        accepted_occluded_provenance={}
        result_value_conflicts={}
        for field, value in occluded_values.items():
            if field not in totals:
                totals[field] = value
                result_candidates[field] = value
                accepted_occluded_provenance[field] = occluded_value_provenance[field]
            elif totals[field] == value:
                # A complete/refined ratio and the malformed-cap reader agree
                # on the numerator.  Publish one proof for the accepted
                # same-frame result instead of a contradictory duplicate.
                accepted_occluded_provenance[field] = occluded_value_provenance[field]
            else:
                canonical = totals[field]
                result_value_conflicts[field] = dict(
                    basis='same_frame_result_value_conflict',
                    status='unresolved',
                    candidate_values=sorted({canonical, value}),
                    occluded=copy.deepcopy(occluded_value_provenance[field]),
                    canonical=dict(
                        value=canonical,
                        source=result_value_sources.get(field, {}).get(
                            'source', 'result.' + field),
                        observation=copy.deepcopy(
                            result_value_sources.get(field, {}).get('observation'))),
                )
                # Neither reading is canonical after disagreement.  Keep the
                # two observations only in the conflict proof; do not leave a
                # scalar candidate, cap, or refinement marker that downstream
                # state assembly could mistake for a canonical field.
                totals.pop(field, None)
                result_candidates.pop(field, None)
                caps.pop(field, None)
                result_refinement_provenance.pop(field, None)
        totals['skill_points']=number(regions.get('result.skill_points',{}))
        result_candidates['skill_points']=number(regions.get('result.skill_points',{}),90)
        facts.update(training_gains=gains,result_values=totals,stat_caps=caps,
                     training_gain_candidates=gain_candidates,
                     training_gain_crop_provenance=gain_provenance,
                     result_value_candidates=result_candidates,
                     gain_digit_crosschecks=digit_crosschecks)
        if accepted_occluded_provenance:
            facts['result_value_provenance'] = accepted_occluded_provenance
        if result_value_conflicts:
            facts['result_value_conflicts'] = result_value_conflicts
        if result_refinement_provenance:
            facts['result_snapshot_refinement'] = result_refinement_provenance
        if partial_results:
            facts['partial_result_counter_readings']=partial_results
            facts['result_numerator_candidates']={field:sorted({p['value'] for p in views}) for field,views in partial_results.items()}
        performance_candidates={}
        for field in CURRENCIES:
            observation=regions.get('performance_gain.'+field,{})
            if not isinstance(observation,dict):
                continue
            match=re.fullmatch(r'\+(\d{1,3})',str(observation.get('text','')).strip())
            confidence=observation.get('confidence',0)
            if not match:
                continue
            if 90 <= confidence < 97:
                # Keep a typed, source-region candidate for a later bounded
                # same-result-group corroboration.  It is intentionally not a
                # gain: one weak crop must never award an effect by itself.
                performance_candidates[field]=dict(
                    amount=int(match[1]),
                    region='performance_gain.'+field,
                    text=str(observation.get('text','')).strip(),
                    confidence=confidence,
                    box=list(observation.get('box',[]))
                    if isinstance(observation.get('box'),(list,tuple)) else [],
                    basis='dedicated_performance_gain_region_below_canonical_threshold',
                    status='unresolved_low_confidence',
                )
        # Do not publish a dedicated signed crop by itself.  It is merged
        # below only after the same-frame committed-result panel proof has
        # been established.
        if performance_candidates:facts['performance_gain_candidates']=performance_candidates
    if screen=='lesson_confirmation':
        gains={}
        boundaries=(276,370,465,559,652,745,831)
        for i,field in enumerate(FIELDS):
            # A gain halved above the cap is drawn in parentheses, "(+6)", and
            # reads a little lower than the plain "+12"; it is the game's own
            # number for what the lesson will add.
            candidates=[l for l in lines if within(l,(boundaries[i],403,boundaries[i+1],431))
                        and (l['confidence']>=97 or (l['confidence']>=90 and re.fullmatch(r'\(\s*\+\s*\d+\s*\)',l['text'].strip())))]
            matches=[re.fullmatch(r'(?:\(\s*)?\+\s*(\d+)(?:\s*\))?',l['text'].strip()) for l in candidates]
            numbers={int(m[1]) for m in matches if m}
            if len(numbers)==1:gains[field]=numbers.pop()
        confirmation_names=[l['text'].strip() for l in lines if within(l,(280,85,650,125)) and l['confidence']>=95 and any(c.isalpha() for c in l['text'])]
        facts.update(name_candidates=confirmation_names,
                     projected_performance_points=currencies(True),current_stats=values('modal_current'),
                     projected_stat_gains=gains,
                     projected_effects=preview_effects([l for l in lines if l['confidence']>=97 and within(l,(440,130,820,245))], typed=True),awarded_effects=[])
        # A confirmation title is the same-frame identity for its projected
        # effect.  Carry it through the parser-owned typed fact so the preview
        # adapter can keep the offer linked without searching nearby receipts
        # or inferring identity from balances.
        if len(set(confirmation_names)) == 1:
            for effect in facts['projected_effects']:
                effect.update(source_offer=confirmation_names[0],
                              context_title=confirmation_names[0],
                              # The title and projected amount are read from
                              # this same confirmation frame.  Mark the
                              # parser-owned relationship explicitly so the
                              # source-bound preview adapter can distinguish
                              # it from an untrusted amount copied into a
                              # typed fact.
                              source_semantics='lesson_offer')
        for effect in facts['projected_effects']:
            match=re.fullmatch(r'(Friendship Training Effectiveness|Specialty Priority|Support Chain Event Frequency Lvl)\s*\+\s*(\d+)%?',effect['raw_text'])
            if match:effect.update(field={'Friendship Training Effectiveness':'friendship_training_effectiveness','Specialty Priority':'specialty_priority','Support Chain Event Frequency Lvl':'support_chain_event_frequency'}[match[1]],amount=int(match[2]))
    if screen=='lesson_selection':
        facts.update(performance_points=currencies(),available_effects=preview_effects(lines))
    facts.update(performance_panel_facts(lines, screen, stats, regions))
    if screen=='concert_info':
        from .concert_bonus_info import read_concert_bonus
        facts['concert_bonus']=read_concert_bonus(lines)
    if screen == 'training_result':
        # The shared panel pass runs after the fixed signed crops and can
        # otherwise overwrite a clean dedicated award with only its current
        # balance.  Reconcile the dedicated result crop only through the
        # source-bound merged-row proof; previews and counters stay separate.
        direct, direct_conflicts = _dedicated_result_performance_gains(
            facts, regions, screen, raw,
        )
        for field, proof in direct.items():
            amount = proof['amount']
            awards = facts.setdefault('awarded_performance_gains', {})
            if field not in awards:
                awards[field] = amount
            elif awards[field] != amount:
                direct_conflicts.setdefault(field, dict(
                    status='unresolved',
                    basis='same_frame_dedicated_result_performance_conflict',
                    candidate_values=[awards[field], amount],
                    dedicated=proof,
                    existing=awards[field],
                ))
        if direct:
            facts['performance_gain_source_provenance'] = direct
        if direct_conflicts:
            facts['performance_gain_source_conflicts'] = direct_conflicts
    if facts.get('training_outcome')=='failure' or facts.get('failure_banner'):
        facts['unawarded_performance_projection']=facts.pop('awarded_performance_gains',{})
    if screen in ('skill_selection','skill_confirmation','skill_receipt'):
        facts.update(points_semantics='possibly_projected_remaining_points',spent_skill_points=None,item_list_complete=False)
        labels=[l for l in lines if l['text']=='Skill Points' and l['confidence']>=97]
        if len(labels)==1:
            label=labels[0];candidates=[l for l in lines if within(l,(label['box'][2],label['box'][1]-10,850,label['box'][3]+10)) and number(l) is not None]
            if len(candidates)==1:facts['displayed_skill_points']=number(candidates[0])
        if screen=='skill_selection' and 'skill_point_refinement' in raw:
            from .refine_skill_points import counter_reading
            value,conflicts=counter_reading(raw['skill_point_refinement'],facts.get('displayed_skill_points'))
            if conflicts:
                facts.pop('displayed_skill_points',None)
                facts['skill_point_conflict']=conflicts
            elif value is not None:
                facts['displayed_skill_points']=value
                facts['skill_point_refinement_basis']='same_frame_counter_crops'
        if screen=='skill_confirmation':
            # Fixed card headings only; a scrollbar means this cannot establish list completeness.
            facts['visible_skill_names']=[l['text'].strip() for l in lines if l['confidence']>=97 and 365<=l['box'][0]<=390 and any(within(l,(365,y-15,740,y+15)) for y in (129,283,437,590,743))]
        if screen=='skill_selection':
            cards=[]
            controls=[l for l in lines if within(l,(690,395,800,875)) and ((number(l) is not None and l['box'][2]>=766) or (l['text']=='Obtained' and l['confidence']>=90))]
            for control in controls:
                cy=(control['box'][1]+control['box'][3])/2
                headings=[l for l in lines if l['confidence']>=95 and 355<=l['box'][0]<=380 and within(l,(360,cy-65,680,cy-35))
                          and not re.match(r'(?:Slightly|Moderately|Increase|Recover|Gain|Control)\b',l['text'])]
                if len(headings)==1:
                    heading=headings[0];name=re.sub(r'\s*[○◯◎]\s*$','',heading['text']).strip()
                    variants={v['variant'] for v in raw.get('skill_variants',[]) if v['name_box']==heading['box']}
                    if variants:name=name.removesuffix('�').rstrip()
                    cards.append(dict(name=name,name_box=heading['box'],displayed_cost=number(control),
                                      menu_status='obtained_or_selected' if control['text']=='Obtained' else 'available',
                                      variant=variants.pop() if len(variants)==1 else None,variant_verified=False))
            facts['skill_cards']=cards
    if screen=='race_result':
        item_headers=[l for l in lines if l['text']=='Items' and l['confidence']>=97
                      and within(l,(250,500,830,900))]
        quantities=[]
        if len(item_headers)==1:
            for line in lines:
                match=re.fullmatch(r'[x\u00d7]\s*(\d{1,6})',line['text'])
                if (match and line['confidence']>=97 and
                    within(line,(260,item_headers[0]['box'][3],825,940))):
                    quantities.append(dict(quantity=int(match[1]),name=None,
                        box=line['box'],raw_text=line['text'],confidence=line['confidence']))
        facts['visible_item_quantities']=quantities
        m=re.search(r'Fans\s+([\d,]+)\s*\(\+([\d,]+)\)',text,re.I)
        facts.update(fans=int(m[1].replace(',','')),fans_gained=int(m[2].replace(',','')))
        names=[_split_race_grade(l['text'])[1] for l in lines if l['confidence']>=95 and within(l,(280,425,810,456))
               and not re.fullmatch(r'DEBUT|G[123]|OP|PRE-OP|EX',l['text'],re.I)]
        places=[re.fullmatch(r'(\d{1,2})(?:st|nd|rd|th)',l['text'],re.I) for l in lines if l['confidence']>=95 and within(l,(280,160,550,355))]
        places={int(m[1]) for m in places if m}
        descriptions=[l['text'] for l in lines if l['confidence']>=97 and within(l,(260,457,810,495)) and re.search(r'\b(?:Turf|Dirt)\b',l['text'])]
        course=re.search(r'^(.+?)\s+(Turf|Dirt)\s+(\d+)m\s+\(([^)]+)\)\s+(Right|Left|Straight)(?:\s*/\s*(Outer|Inner))?',' '.join(descriptions),re.I)
        conditions=[l['text'].lower() for l in lines if l['confidence']>=97
                    and within(l,(690,457,810,495))
                    and re.fullmatch(r'Firm|Good|Heavy',l['text'],re.I)]
        race_grade, race_grade_provenance = _race_result_grade_observation(lines)
        facts.update(race_name=names[0] if len(names)==1 else None,placing=places.pop() if len(places)==1 else None,
            race_grade=race_grade,
            course=dict(venue=course[1],surface=course[2].lower(),distance_m=int(course[3]),distance_category=course[4].lower(),direction=course[5].lower(),variant=course[6].lower() if course[6] else None) if course else None,
            course_condition=conditions[0] if course and len(conditions)==1 else None,
            item_rewards_complete=False)
        if race_grade_provenance is not None:
            facts['race_grade_provenance'] = race_grade_provenance
    runner_card=_race_runner_card(raw,lines)
    if runner_card is not None:
        facts['race_runner_attributes']=runner_card
    titles=[l['text'] for l in lines if within(l,(240,195,850,245)) and l['confidence']>=95 and l['box'][3]<=250 and l['text']!='MAX']
    candidate_titles=[l['text'] for l in lines if within(l,(240,195,850,245)) and l['confidence']>=90 and l['box'][3]<=250 and l['text']!='MAX']
    result = dict(screen=screen,stats=stats,training_option=option if screen=='training_result' else None,
                  effects=effects,facts=facts,completed_action='training' if screen=='training_result' else None,
                  context_title=' '.join(titles) or None,context_title_candidate=' '.join(candidate_titles) or None,ocr={'neural':lines})
    # Preserve the source/model envelope alongside parsed rows.  Inspection
    # mergers need this physical identity to distinguish a same-timestamp
    # reread of one frame from an unrelated frame or recording.  Older raw
    # fixtures simply omit these fields and therefore remain ordinary parsed
    # observations without fabricated provenance.
    for key in ('source_sha256', 'source_frame_sha256', 'source_frame_id',
                'engine_fingerprint', 'model_sha256', 'gameplay_sha256'):
        if key in raw and raw.get(key) is not None:
            result[key] = copy.deepcopy(raw[key])
    return result
