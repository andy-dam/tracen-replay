"""Abstain on dialogue text covered by the gameplay profile's green overlay.

This detects an obstruction, not a replacement digit. Other unobstructed frames
must supply the receipt. It does not claim to detect every cursor or overlay.
"""
import hashlib
import json
import math
import re


# The color mask captures the green fill, not the white cursor outline and
# antialiased edge. In this capture profile the outline extends three pixels
# beyond the fill. The source glyph-band check below prevents padding near a
# neighboring baseline from erasing a readable receipt.
CURSOR_OUTLINE_PADDING = 3
CURSOR_WHITE_SURROUND = .3
MIN_GLYPH_OVERLAP_PX = 3
GLYPH_ROW_MIN_PIXELS = 8
GLYPH_ROW_DENSITY = .08

# Result receipts can be covered by the pastel horseshoe/confetti animation
# that follows a friendship or stat award.  This is separate from the green
# cursor detector below: the animation is a source-pixel obstruction only and
# must never be interpreted as a replacement character or a name hint.
PARTICLE_SCAN_MARGIN = 12
PARTICLE_MIN_PIXELS = 20
PARTICLE_MAX_PIXELS = 2400
PARTICLE_MIN_SIDE = 4
PARTICLE_MAX_SIDE = 80
PARTICLE_MIN_OVERLAP_PX = 3


def numeric_line(line):
    return 770<=line['box'][1]<1000 and bool(re.search(
        r'went|recover|Gained|Friendship|Frienlship|Friewdship|Frendship',
        line['text']) and re.search(r'\d',line['text']))


def friendship_status_line(line):
    """Recognize complete nonnumeric friendship-status receipt sentences.

    This is deliberately a receipt-shape check only. It does not repair OCR
    spelling or turn a status into an effect; ``vision.parse`` remains the
    authority for semantic parsing. The raw ``mad``/``maed`` forms are kept
    here so a cursor over their fixed grammar still causes a conservative
    abstention without normalizing them.
    """
    if not 770<=line['box'][1]<1000:
        return False
    text=line.get('text','').strip()
    return bool(re.fullmatch(
        r"(?:Friendship|Frienlship|Friewdship|Frendship) with .+? "
        r"(?:didn['’]t go up|is (?:maxed|mad|maed) out)[.!]?",
        text,
        re.I,
    ))


def receipt_line(line):
    return numeric_line(line) or friendship_status_line(line) or (
        770<=line['box'][1]<1000 and bool(re.match(r'^Inspired by\s+\S',line.get('text',''))))


def hint_data_boxes(line):
    """The boxes of a hint receipt's number and skill name, or None.

    The recognizer returns where each word it read sits, and a hint receipt
    says its number and its name in words of their own: "Gained 4 hint
    level(s) for Pace Chaser Corners". Everything between them is fixed UI
    text. The split is only trusted while the fixed words are within the
    repair tolerance of what they should say, so a line damaged past that
    yields nothing here.
    """
    from .receipt_grammar import hint_wording
    words=line.get('word_boxes')
    if not isinstance(words,list) or len(words)<6:return None
    if not all(isinstance(w,dict) and isinstance(w.get('box'),list) and len(w['box'])==4 for w in words):return None
    text=' '.join(w.get('text','') for w in words)
    if text!=line.get('text') or not (hint_wording(text) or re.fullmatch(r'Gained \d+ hint level\(s\) for \S.*',text)):
        return None
    return [words[1]['box']]+[w['box'] for w in words[5:]]


def hint_wording_obstructed(line,overlays):
    """True when an obstruction on a hint receipt covers none of its data.

    The number and the name are what the receipt says; the words between them
    are fixed. An overlay that touches neither can only have damaged that
    fixed wording, which is what allows the wording, and nothing else, to be
    repaired.
    """
    from .receipt_grammar import hint_wording
    # Only a line whose fixed wording the obstruction damaged: a line that
    # reads cleanly needs no repair, and unblocking it would assert a receipt
    # the event may already have counted under a better reading.
    if not hint_wording(line.get('text','')):return False
    boxes=hint_data_boxes(line)
    if not boxes or not overlays:return False
    for overlay in overlays:
        for box in boxes:
            if (min(overlay[2],box[2])-max(overlay[0],box[0])>0
                    and min(overlay[3],box[3])-max(overlay[1],box[1])>0):
                return False
    return True


def overlay_beyond_sentence_end(line,overlays):
    """True when every obstruction sits past the line's closing punctuation.

    A cursor parked just after "Wit went up by 10." touches no glyph: the
    sentence ends with its full stop, read, and nothing of the receipt can
    hide beyond it. An obstruction inside the sentence, even between two
    words, may cover a digit the recognizer never saw, so only the space
    past the terminator is clear.
    """
    words=line.get('word_boxes')
    if not isinstance(words,list) or not words or not overlays:return False
    if not all(isinstance(w,dict) and isinstance(w.get('box'),list) and len(w['box'])==4 for w in words):return False
    if ' '.join(w.get('text','') for w in words)!=line.get('text'):return False
    last=words[-1]
    if not str(last.get('text','')).endswith(('.','!')):return False
    return all(overlay[0]>=last['box'][2] for overlay in overlays)


def subject_word_obstructed(line,overlays):
    """True when an obstruction on a one-word receipt covers only its subject.

    "Vocals went up by 20." with the cursor parked over the V reads as
    "Yocals went up by 20." on every frame. The direction and the number are
    what the receipt says; the subject is one of a few fixed UI words. An
    overlay that touches the subject and no other word can only have damaged
    that word, which is what allows it, and nothing else, to be repaired.
    """
    from .receipt_grammar import subject_receipt
    if not subject_receipt(line.get('text','')):return False
    words=line.get('word_boxes')
    if not isinstance(words,list) or len(words)<5 or not overlays:return False
    if not all(isinstance(w,dict) and isinstance(w.get('box'),list) and len(w['box'])==4 for w in words):return False
    if ' '.join(w.get('text','') for w in words)!=line.get('text'):return False
    subject=words[0]['box']
    def touches(overlay,box):
        return (min(overlay[2],box[2])-max(overlay[0],box[0])>0
                and min(overlay[3],box[3])-max(overlay[1],box[1])>0)
    if not any(touches(overlay,subject) for overlay in overlays):return False
    return not any(touches(overlay,w['box']) for overlay in overlays for w in words[1:])


def recovered_leading_digit(line,alignments):
    """Padded OCR views can expose a digit missed by the tight alignment crop."""
    from .refine_receipts import consensus
    views=line.get('receipt_crop_views',[])
    accepted=consensus(views)
    if not accepted:return False
    complete=re.fullmatch(r'(.+?by)\s*(\d+)[.!]?',accepted['text'])
    if not complete or accepted['text']!=line['text']:return False
    for item in alignments:
        if item['line_box']!=line['box'] or item.get('confidence',0)<95:continue
        partial=re.fullmatch(r'(.+?by)\s*(\d+)[.!]?',item.get('recognized_text',''))
        if (partial and re.sub(r'\s','',partial[1])==re.sub(r'\s','',complete[1])
            and len(complete[2])>len(partial[2]) and complete[2].endswith(partial[2])):return True
    return False


def numeric_bounds(box,words,columns,line_length):
    """Include the gap after 'by': an obscured leading digit has no OCR column."""
    if line_length<=0 or len(words)!=len(columns) or any(not c for c in columns):return None
    if any(v<0 or v>=line_length for c in columns for v in c):return None
    positions=[i for i,w in enumerate(words[:-1]) if w=='by' and re.fullmatch(r'\d+[.!]?',words[i+1])]
    if len(words)==3 and words[0]=='Gained' and re.fullmatch(r'\d+',words[1]) and re.fullmatch(r'fans[.!]?',words[2]):positions=[0]
    if len(positions)!=1:return None
    i=positions[0];a,b,c,d=box;scale=(c-a)/line_length
    start=max(columns[i])+.75
    digits=words[i+1].rstrip('.!')
    if len(columns[i+1])!=len(words[i+1]):return None
    last_digit=columns[i+1][len(digits)-1]
    # Sentence punctuation and detector padding are not part of the award.
    end=min(line_length,last_digit+1.5)
    if i+2<len(words):end=min(end,min(columns[i+2])-.5)
    if start>=min(columns[i+1]) or end<=last_digit:return None
    return [a+start*scale,b,a+end*scale,d]


def friendship_name_bounds(box,words,columns,line_length):
    """A readable amount cannot establish a cursor-covered recipient name."""
    if line_length<=0 or len(words)!=len(columns) or any(not c for c in columns):return None
    if any(v<0 or v>=line_length for c in columns for v in c):return None
    from .receipt_grammar import friendship_receipt
    repaired=friendship_receipt(' '.join(words))
    if len(words)<2:return None
    if repaired is None and (words[0].lower() not in ('friendship','frienlship','friewdship','frendship') or words[1].lower() not in ('with','wh')):return None
    normalized=[re.sub(r'[.!?]+$','',word).lower() for word in words]
    end=None
    for index in range(2,len(words)):
        if (repaired is not None or normalized[index] in ('went','wert','ent')) and normalized[index+1:index+3]==['up','by']:
            end=index;break
        if normalized[index] in ("didn't","didn’t") and normalized[index+1:index+3]==['go','up']:
            end=index;break
        if normalized[index]=='is' and normalized[index+1:index+3] in (['maxed','out'],['mad','out'],['maed','out']):
            end=index;break
    if end is None:return None
    if end<=2:return None
    a,b,c,d=box;scale=(c-a)/line_length
    # An obscured first/last letter has no OCR column. Protect the gaps
    # after "with" and before "went", not merely the recognized letters.
    start=max(columns[1])+.75;stop=min(columns[end])-.5
    if start>=min(columns[2]) or stop<=max(columns[end-1]):return None
    return [a+max(0,start)*scale,b,a+min(line_length,stop)*scale,d]


def inspiration_name_bounds(box,words,columns,line_length):
    """Protect the name and omitted-letter gaps, excluding sentence punctuation."""
    if line_length<=0 or len(words)!=len(columns) or len(words)<3:return None
    if words[:2]!=['Inspired','by'] or any(len(w)!=len(c) or not c for w,c in zip(words,columns)):return None
    if any(v<0 or v>=line_length for c in columns for v in c):return None
    flattened=[v for group in columns for v in group]
    if any(left>=right for left,right in zip(flattened,flattened[1:])):return None
    if not words[-1].endswith(('!','.')):return None
    a,b,c,d=box;scale=(c-a)/line_length
    start=max(columns[1])+.75;end=columns[-1][-1]-.5
    name_columns=[v for group in columns[2:-1] for v in group]+columns[-1][:-1]
    if not name_columns or start>=min(name_columns) or end<=max(name_columns):return None
    return [a+start*scale,b,a+end*scale,d]


def _alignment_glyph_spans(item):
    """Map validated OCR character columns to absolute horizontal spans.

    The OCR line box includes inter-word whitespace. Using its full width for
    cursor overlap makes a pointer parked in a normal gap look like it covered
    a glyph. Alignment columns are source-linked geometry, so they let the
    occlusion check distinguish a real character hit from a small edge touch.
    """
    box=item.get('line_box');words=item.get('words');columns=item.get('columns')
    line_length=item.get('line_length')
    if (not isinstance(box,(list,tuple)) or len(box)!=4 or
        not isinstance(words,list) or not isinstance(columns,list) or
        len(words)!=len(columns) or not isinstance(line_length,(int,float)) or
        isinstance(line_length,bool) or not math.isfinite(float(line_length)) or
        line_length<=0):return None
    try:
        a,b,c,d=(float(value) for value in box)
    except (TypeError,ValueError):return None
    if not all(math.isfinite(value) for value in (a,b,c,d)) or c<=a or d<=b:return None
    scale=(c-a)/float(line_length);spans=[]
    for group in columns:
        if not isinstance(group,list) or not group:return None
        for value in group:
            if (isinstance(value,bool) or not isinstance(value,(int,float)) or
                not math.isfinite(float(value)) or value<0 or value>=line_length):return None
            start=a+float(value)*scale
            end=a+min(float(line_length),float(value)+1)*scale
            if end>start:spans.append([start,end])
    return spans


def _friendship_name_glyph_spans(item):
    """Protect the entire recipient, including gaps left by missing glyphs.

    OCR whitespace alone cannot distinguish a real word space from a letter
    hidden by the cursor. A different readable frame may establish identity;
    metadata assertions cannot make the obstructed frame readable.
    """
    if _alignment_glyph_spans(item) is None:
        return None
    bounds=friendship_name_bounds(item['line_box'],item['words'],item['columns'],item['line_length'])
    return [[bounds[0],bounds[2]]] if bounds else None


def _glyph_vertical_band(pixels,box):
    """Find the visible receipt glyph rows inside an OCR line region.

    OCR boxes contain several pixels of baseline and antialiasing slack. The
    warm dark text mask is independent of the green cursor and its white
    outline; selecting the dominant row run avoids treating that slack as a
    covered glyph. ``None`` keeps the prior center-line fallback for synthetic
    or fully covered lines where no text pixels remain.
    """
    try:
        a,b,c,d=(float(value) for value in box)
    except (TypeError,ValueError):return None
    if not all(math.isfinite(value) for value in (a,b,c,d)) or c<=a or d<=b:return None
    y0=max(0,int(math.floor(b)));y1=min(pixels.shape[0],int(math.ceil(d)))
    x0=max(0,int(math.floor(a-148)));x1=min(pixels.shape[1],int(math.ceil(c-148)))
    if y1<=y0 or x1<=x0:return None
    region=pixels[y0:y1,x0:x1]
    red,green,blue=region[:,:,0],region[:,:,1],region[:,:,2]
    # Dialogue ink is warm/dark; gray cursor outline and green fill fail this
    # test. The thresholds include antialiased text while excluding the white
    # bubble and its cool border.
    ink=((red<190)&(green<160)&(blue<150)&((red-green)>=8)&((green-blue)>=2))
    counts=ink.sum(axis=1);peak=int(counts.max(initial=0))
    if peak<GLYPH_ROW_MIN_PIXELS:return None
    threshold=max(GLYPH_ROW_MIN_PIXELS,peak*GLYPH_ROW_DENSITY)
    active=counts>threshold;runs=[];start=None
    for index,is_active in enumerate(active):
        if is_active and start is None:start=index
        if start is not None and (not is_active or index==len(active)-1):
            stop=index if not is_active else index+1
            runs.append((start,stop,int(counts[start:stop].sum())))
            start=None
    if not runs:return None
    start,stop,_=max(runs,key=lambda run:run[2])
    return [y0+start,y0+stop]


def _horizontal_hit(box,spans):
    return any(min(right,box[2])-max(left,box[0])>=MIN_GLYPH_OVERLAP_PX
               for left,right in spans)


def _vertical_hit(box,band,region):
    if band is None:
        middle=(region[1]+region[3])/2
        return box[1]<=middle<=box[3]
    # A cursor whose padded box merely touches the last three glyph rows is
    # at the lower edge of the receipt, as in the clear 238000 anchor. Require
    # evidence beyond that shared boundary before declaring text covered.
    return min(band[1],box[3])-max(band[0],box[1])>MIN_GLYPH_OVERLAP_PX


def overlay_boxes(pane):
    import numpy as np
    if pane.size != (810,1080):raise ValueError('Expected the gameplay crop.')
    pixels=np.asarray(pane.convert('RGB')).astype('int16')
    band=pixels[770:1000]
    r,g,b=band[:,:,0],band[:,:,1],band[:,:,2]
    mask=(g>140)&(g>r*1.3)&(g>b*1.2)&(r<170)&(b<170)
    ys,xs=np.nonzero(mask);pending=set(zip(xs.tolist(),ys.tolist()));boxes=[]
    while pending:
        point=pending.pop();stack=[point];component=[point]
        while stack:
            x,y=stack.pop()
            for neighbor in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if neighbor in pending:
                    pending.remove(neighbor);stack.append(neighbor);component.append(neighbor)
        xs,ys=zip(*component);left,right=min(xs),max(xs)+1;top,bottom=min(ys)+770,max(ys)+771
        if not (20<=len(component)<=180 and 5<=right-left<=20 and 8<=bottom-top<=26):continue
        neighborhood=pixels[max(770,top-10):min(1000,bottom+10),max(0,left-10):min(810,right+10)]
        white=(neighborhood.min(axis=2)>190)&(neighborhood.max(axis=2)-neighborhood.min(axis=2)<55)
        # The cursor sits on the white receipt bubble; parked over a word,
        # the letters under and beside it take a share of its surroundings
        # (0.37 to 0.46 measured over "Stamina"), so a third is enough.
        if float(white.mean())<CURSOR_WHITE_SURROUND:continue
        padding=CURSOR_OUTLINE_PADDING
        boxes.append([left+148-padding,top-padding,right+148+padding,bottom+padding])
    return boxes


def _particle_line_box(box):
    """Return a finite gameplay-space receipt box, or ``None``.

    OCR boxes are expressed in the full 960-pixel gameplay coordinate space,
    while ``pane`` is the 810-pixel crop whose origin is x=148.  Keeping this
    validation at the detector boundary prevents a malformed or auxiliary
    panel box from turning coloured pixels elsewhere in the image into receipt
    evidence.
    """
    if not isinstance(box,(list,tuple)) or len(box)!=4:
        return None
    try:
        values=tuple(float(value) for value in box)
    except (TypeError,ValueError):
        return None
    if (not all(math.isfinite(value) for value in values) or
        values[2]<=values[0] or values[3]<=values[1] or
        values[0]<148 or values[2]>958 or values[1]<770 or values[3]>1000):
        return None
    return values


def _particle_pixel(pixel):
    """Recognize the saturated pastel animation colours in source pixels.

    The friendship result animation uses pink, cyan, and yellow horseshoes.
    These channel families deliberately exclude the warm brown receipt ink,
    white dialogue bubble, and pale game background.  This is an obstruction
    detector only; it does not classify a glyph or infer a name.
    """
    try:
        red,green,blue=(int(channel) for channel in pixel[:3])
    except (TypeError,ValueError,IndexError):
        return False
    return (
        (red>=215 and blue>=185 and green<=215 and
         red-green>=22 and blue-green>=12) or
        (green>=215 and blue>=210 and red<=215 and
         green-red>=20 and blue-red>=15) or
        (red>=220 and green>=220 and blue<=210 and
         abs(red-green)<=35)
    )


def _particle_pixels(pixels,left,top,right,bottom):
    """Vectorised :func:`_particle_pixel` over one scan rectangle.

    Same channel tests as the scalar predicate, evaluated with NumPy on the
    cropped region instead of one ``getpixel`` call per source pixel; the
    result is the set of matching ``(x, y)`` gameplay-crop coordinates.
    """
    import numpy as np
    region=np.asarray(pixels)[top:bottom,left:right,:3].astype('int16')
    red,green,blue=region[...,0],region[...,1],region[...,2]
    mask=(
        ((red>=215)&(blue>=185)&(green<=215)&(red-green>=22)&(blue-green>=12))|
        ((green>=215)&(blue>=210)&(red<=215)&(green-red>=20)&(blue-red>=15))|
        ((red>=220)&(green>=220)&(blue<=210)&(np.abs(red-green)<=35))
    )
    ys,xs=np.nonzero(mask)
    return set(zip((xs+left).tolist(),(ys+top).tolist()))


def _particle_intersects(left, right):
    width=max(0,min(left[2],right[2])-max(left[0],right[0]))
    height=max(0,min(left[3],right[3])-max(left[1],right[1]))
    return width>=PARTICLE_MIN_OVERLAP_PX and height>=PARTICLE_MIN_OVERLAP_PX


def _blue_receipt_ink(pixel):
    """The darker blue stroke beneath a bright antialiased receipt fringe."""
    red,green,blue=pixel
    return red<180 and green<215 and blue-red>=30 and blue-green>=10


def _independent_pastel_core(component,pixels):
    """Require pastel area beyond the immediate edge of blue text strokes.

    Compressed or brightened blue glyphs can have a solid pastel fringe, not
    just isolated edge pixels. A particle must have its own 3x3 colour core
    whose one-pixel border is not dark blue receipt ink. This tests local
    pixels only; receipt wording and OCR amounts do not enter the decision.
    """
    points=set(component)
    for x,y in component:
        if not all((x+dx,y+dy) in points for dx in range(3) for dy in range(3)):
            continue
        if not any(_blue_receipt_ink(pixels.getpixel((xx,yy)))
                   for xx in range(max(0,x-1),min(pixels.width,x+4))
                   for yy in range(max(0,y-1),min(pixels.height,y+4))):
            return True
    return False


def animated_overlay_boxes(pane,line_boxes):
    """Find source-pixel particles crossing receipt rows.

    The detector is bounded by the OCR receipt boxes and returns global
    gameplay coordinates, matching :func:`overlay_boxes`.  It intentionally
    reports only a physical obstruction.  Later clear source frames must
    provide the receipt text and identity; this helper never repairs OCR or
    selects among names.
    """
    if pane.size!=(810,1080):
        raise ValueError('Expected the gameplay crop.')
    valid=[box for item in line_boxes or [] if (box:=_particle_line_box(item))]
    if not valid:
        return []
    # Receipt OCR boxes are global (x=148..958); the image crop starts at 148.
    left=max(0,int(math.floor(min(box[0] for box in valid)-PARTICLE_SCAN_MARGIN-148)))
    right=min(810,int(math.ceil(max(box[2] for box in valid)+PARTICLE_SCAN_MARGIN-148)))
    top=max(770,int(math.floor(min(box[1] for box in valid)-PARTICLE_SCAN_MARGIN)))
    bottom=min(1000,int(math.ceil(max(box[3] for box in valid)+PARTICLE_SCAN_MARGIN)))
    if right<=left or bottom<=top:
        return []
    pixels=pane.convert('RGB')
    pending=_particle_pixels(pixels,left,top,right,bottom)
    boxes=[]
    while pending:
        origin=pending.pop();stack=[origin];component=[origin]
        while stack:
            x,y=stack.pop()
            for nx in (x-1,x,x+1):
                for ny in (y-1,y,y+1):
                    point=(nx,ny)
                    if point in pending:
                        pending.remove(point);stack.append(point);component.append(point)
        if not (PARTICLE_MIN_PIXELS<=len(component)<=PARTICLE_MAX_PIXELS):
            continue
        # Antialiasing around blue receipt ink can match the pastel mask in
        # long, thin fringes. Require an actual patch of pastel colour rather
        # than treating the fringe's bounding rectangle as an obstruction.
        # This establishes only particle geometry, never the covered text.
        if not _independent_pastel_core(component,pixels):
            continue
        xs=[point[0] for point in component];ys=[point[1] for point in component]
        component_box=[min(xs)+148,min(ys),max(xs)+1+148,max(ys)+1]
        width=component_box[2]-component_box[0]
        height=component_box[3]-component_box[1]
        if not (PARTICLE_MIN_SIDE<=width<=PARTICLE_MAX_SIDE and
                PARTICLE_MIN_SIDE<=height<=PARTICLE_MAX_SIDE):
            continue
        if not any(_particle_intersects(component_box,box) for box in valid):
            continue
        boxes.append(component_box)
    return sorted(boxes,key=lambda box:(box[1],box[0],box[2],box[3]))


def annotate(raw,pane):
    candidates=[i for i,line in enumerate(raw['lines']) if receipt_line(line)]
    if not candidates:return raw
    if raw.get('gameplay_sha256')!=hashlib.sha256(pane.convert('RGB').tobytes()).hexdigest():
        raise ValueError('Receipt overlay proof differs from original OCR pixels.')
    cursor_boxes=overlay_boxes(pane)
    particle_boxes=animated_overlay_boxes(pane,[raw['lines'][i]['box'] for i in candidates])
    boxes=cursor_boxes+particle_boxes
    if not boxes:return raw
    import numpy as np
    pixels=np.asarray(pane.convert('RGB')).astype('int16')
    lines=[dict(line) for line in raw['lines']];blocked=[];resolved=[]
    for index in candidates:
        line=lines[index];a,b,c,d=line['box']
        full_line_region=[a,b,c,d]
        localized=[];name_regions=[];line_glyph_spans=[]
        for item in raw.get('overlay_alignment',[]):
            if item['line_box']!=line['box'] or item.get('confidence',100)<95:continue
            bounds=numeric_bounds(item['line_box'],item['words'],item['columns'],item['line_length']) if 'words' in item else item.get('numeric_box')
            if bounds:localized.append(bounds)
            if 'words' in item:
                name=friendship_name_bounds(item['line_box'],item['words'],item['columns'],item['line_length'])
                spans=_alignment_glyph_spans(item)
                if spans:line_glyph_spans.extend(spans)
                name_spans=_friendship_name_glyph_spans(item)
                if name:name_regions.append((name,name_spans))
                inspiration=inspiration_name_bounds(item['line_box'],item['words'],item['columns'],item['line_length']) if (
                    item.get('recognized_text')==line['text'] and ' '.join(item['words'])==line['text']) else None
                if inspiration:
                    name_regions.append((inspiration,[[inspiration[0],inspiration[2]]]))
                    localized.append(inspiration)
        if len(localized)==1:
            # Keep amount horizontal bounds for deciding whether the cursor
            # intersects the award, but calibrate its vertical position from
            # the whole receipt line. A cursor can erase most of a digit in
            # the localized crop while leaving enough neighboring receipt
            # ink to prove that the line is covered.
            amount_region=localized[0]
            line_spans=[[amount_region[0],amount_region[2]]]
        else:
            line_spans=line_glyph_spans or [[a,c]]
        line_region=full_line_region
        line_band=_glyph_vertical_band(pixels,line_region)
        overlaps=[box for box in boxes if _horizontal_hit(box,line_spans)
                  and _vertical_hit(box,line_band,line_region)]
        name_overlaps=[]
        for region,spans in name_regions:
            band=line_band
            # Calibrate vertical position from the full receipt, because the
            # cursor may erase most of the recipient's own visible ink.
            spans=([[region[0],region[2]]] if band is None
                   else (spans or [[region[0],region[2]]]))
            for box in boxes:
                if _horizontal_hit(box,spans) and _vertical_hit(box,band,region):
                    name_overlaps.append(box)
        combined=[]
        for box in overlaps+name_overlaps:
            if box not in combined:combined.append(box)
        overlaps=combined
        if overlaps:
            if not name_overlaps and recovered_leading_digit(line,raw.get('overlay_alignment',[])):
                resolved.append(dict(text=line['text'],box=line['box'],overlay_boxes=overlaps,
                                     basis='padded_views_recover_leading_digit',independent_frame_count=1))
                continue
            # An obstruction that covers a hint receipt's fixed wording and
            # neither its number nor its name leaves both readable, so the
            # line keeps its confidence and the wording may be repaired.
            if hint_wording_obstructed(line,overlaps):
                resolved.append(dict(text=line['text'],box=line['box'],overlay_boxes=overlaps,
                                     basis='overlay_covers_fixed_hint_wording',independent_frame_count=1))
                continue
            # An obstruction past the sentence's full stop touches nothing.
            if overlay_beyond_sentence_end(line,overlaps):
                resolved.append(dict(text=line['text'],box=line['box'],overlay_boxes=overlaps,
                                     basis='overlay_beyond_sentence_end',independent_frame_count=1))
                continue
            # Likewise an obstruction over the fixed subject word of a stat or
            # performance receipt, its direction and number untouched.
            if subject_word_obstructed(line,overlaps):
                resolved.append(dict(text=line['text'],box=line['box'],overlay_boxes=overlaps,
                                     basis='overlay_covers_fixed_subject_word',independent_frame_count=1))
                continue
            animated_overlaps=[box for box in overlaps if box in particle_boxes]
            blocked.append(dict(text=line['text'],box=line['box'],confidence=line['confidence'],overlay_boxes=overlaps,
                                animated_overlay_boxes=animated_overlaps,
                                animated_overlay_occluded=bool(animated_overlaps),
                                recipient_name_occluded=bool(name_overlaps)))
            line.update(confidence=0,overlay_occluded=True,pre_occlusion_confidence=line['confidence'])
    return dict(raw,lines=lines,occluded_receipt_lines=blocked,resolved_receipt_occlusions=resolved,
                receipt_overlay_evidence=dict(overlay_boxes=cursor_boxes,
                                              animated_overlay_boxes=particle_boxes,
                                              alignments=raw.get('overlay_alignment',[]),
                                              provenance=raw.get('receipt_overlay_provenance')))


def annotate_path(raw,path,original=None,*,source_sha256=None):
    if not any(receipt_line(l) for l in raw['lines']):return raw
    from PIL import Image
    from .refine_contrast import fingerprint
    raw=dict(raw)
    raw.pop('receipt_overlay_provenance',None)
    extra_path=path.with_suffix('.overlay.json')
    if extra_path.exists():
        extra=json.loads(extra_path.read_text(encoding='utf-8'))
        if extra['raw_sha256']!=fingerprint(original or raw) or extra['evidence_sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Receipt overlay alignment provenance changed.')
        origin=original or raw
        if source_sha256 and origin.get('source_sha256') and source_sha256!=origin['source_sha256']:
            raise ValueError('Receipt alignment belongs to a different recording.')
        raw=dict(raw,overlay_alignment=extra['lines'],receipt_overlay_provenance=dict(
            validated_by='annotate_path',raw_sha256=extra['raw_sha256'],
            evidence_sha256=extra['evidence_sha256'],model_sha256=extra.get('model_sha256'),
            source_sha256=source_sha256 or origin.get('source_sha256'),source_frame_sha256=origin.get('source_frame_sha256'),
            gameplay_sha256=origin.get('gameplay_sha256'),
            source_timestamp_ms=origin.get('source_timestamp_ms'),evidence=origin.get('evidence')))
    from .frame_cache import open_rgb
    return annotate(raw,open_rgb(path))
