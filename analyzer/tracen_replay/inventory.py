"""Visible final-summary ownership, separate from carts and complete inventory."""
import re


_CARD_COLUMNS=((310,550),(590,830))
_GRID_MIN_Y=480
_GRID_MAX_Y=943
_ROW_PITCH=63
_LEVEL_RE=re.compile(r'Lvl\s*(\d+)',re.I)
_CARD_CONFIDENCE=95
_ROW_MATCH_TOLERANCE=28
_CONTINUATION_GAP=18
_MAX_CARD_HEIGHT=52
# Skill names can begin with a digit (for example ``564 Escapades``) or
# contain an equals sign (``U=ma2``).  The panel gate below supplies the
# semantic boundary; this expression only rejects OCR control/punctuation
# fragments that cannot be a card label.
_CARD_TEXT_RE=re.compile(r"[A-Za-z0-9][A-Za-z0-9 =.,!'()&:/+\-]*")


def _box(line):
    box=line.get('box') if isinstance(line,dict) else None
    if not isinstance(box,(list,tuple)) or len(box)!=4:return None
    try:
        return tuple(float(value) for value in box)
    except (TypeError,ValueError):
        return None


def _center_y(line):
    box=_box(line)
    return (box[1]+box[3])/2 if box is not None else None


def _column_index(line):
    box=_box(line)
    if box is None or not _GRID_MIN_Y<=(_center_y(line) or -1)<_GRID_MAX_Y:return None
    if 300<=box[0]<=380 and box[2]<=550:return 0
    if 590<=box[0]<=620 and box[2]<=830:return 1
    return None


def _level_column(line):
    """Return the card column containing a level label, if its geometry fits."""
    box=_box(line)
    if box is None or not _GRID_MIN_Y<=(_center_y(line) or -1)<_GRID_MAX_Y:return None
    for column,(_,right) in enumerate(_CARD_COLUMNS):
        if right-90<=box[0] and box[2]<=right:
            return column
    return None


def _is_card_name_line(line):
    if not isinstance(line,dict) or line.get('confidence',0)<_CARD_CONFIDENCE:return False
    text=line.get('text','').strip()
    if not text or _LEVEL_RE.fullmatch(text) or not _CARD_TEXT_RE.fullmatch(text):return False
    return _column_index(line) is not None


def _logical_card_groups(lines):
    """Group wrapped OCR lines into source-aligned card labels.

    A card can wrap over two adjacent lines (for example ``Medium`` followed
    by ``Straightaways``).  Grouping uses only high-confidence card-shaped
    lines in one grid column, with a small vertical gap and stable left edge;
    unrelated rows therefore remain separate even when their text is close.
    """
    buckets=[[],[]]
    for line in lines:
        if _is_card_name_line(line):buckets[_column_index(line)].append(line)
    groups=[]
    for column,bucket in enumerate(buckets):
        bucket.sort(key=lambda line:((_box(line) or (0,0,0,0))[1],(_box(line) or (0,0,0,0))[0]))
        current=None
        for line in bucket:
            box=_box(line)
            if current is None:
                current=dict(column=column,lines=[line],top=box[1],bottom=box[3],left=box[0])
                continue
            previous_box=_box(current['lines'][-1])
            gap=box[1]-previous_box[3]
            same_left=abs(box[0]-current['left'])<=20
            combined_height=box[3]-current['top']
            if gap<=_CONTINUATION_GAP and same_left and combined_height<=_MAX_CARD_HEIGHT:
                current['lines'].append(line);current['bottom']=max(current['bottom'],box[3])
            else:
                groups.append(current)
                current=dict(column=column,lines=[line],top=box[1],bottom=box[3],left=box[0])
        if current is not None:groups.append(current)
    for group in groups:
        group['lines'].sort(key=lambda line:((_box(line) or (0,0,0,0))[1],(_box(line) or (0,0,0,0))[0]))
        group['center']=(group['top']+group['bottom'])/2
        group['text']=' '.join(line.get('text','').strip() for line in group['lines'])
        group['invalid']=False
        for line in lines:
            if line in group['lines'] or not isinstance(line,dict) or line.get('confidence',0)>=_CARD_CONFIDENCE:
                continue
            if _LEVEL_RE.fullmatch(line.get('text','').strip()) or not _CARD_TEXT_RE.fullmatch(line.get('text','').strip()):
                continue
            if _column_index(line)!=group['column'] or _box(line) is None:
                continue
            box=_box(line)
            if abs(box[0]-group['left'])>20:
                continue
            if box[3]<group['top']:
                gap=group['top']-box[3]
            elif box[1]>group['bottom']:
                gap=box[1]-group['bottom']
            else:
                gap=0
            if gap<=_CONTINUATION_GAP and max(group['bottom'],box[3])-min(group['top'],box[1])<=_MAX_CARD_HEIGHT:
                group['invalid']=True
                break
    return sorted(groups,key=lambda group:(group['center'],group['column'],group['left']))


def _has_inspiration_marker(lines):
    # Legacy Origin is a section heading, not a skill-card label.  Requiring
    # its content-area geometry avoids confusing the tab label at y~455 with
    # the panel body.  This marker is only a negative signal: absence does not
    # prove that the current tab is Skills.
    for line in lines:
        if line.get('confidence',0)<90 or line.get('text','').strip()!='Legacy Origin':continue
        box=_box(line);center=_center_y(line)
        if box is not None and center is not None and 760<=center<=900 and box[0]<460:
            return True
    return False


def _skill_grid_pairs(lines, groups=None):
    """Return high-confidence left/right card rows with Skills geometry.

    The Inspiration tab uses a narrower inset (roughly x=391/624), while the
    Skills tab uses two regular columns (roughly x=318/601).  Pairing at least
    two rows lets a noisy/missing ``Lvl N`` line remain usable without treating
    arbitrary Career Info text as an inventory card.
    """
    if groups is None:groups=_logical_card_groups(lines)
    left=[group for group in groups if group['column']==0 and not group['invalid']]
    right=[group for group in groups if group['column']==1 and not group['invalid']]
    edges=[]
    for left_group in left:
        for right_group in right:
            distance=abs(left_group['center']-right_group['center'])
            if distance<=_ROW_MATCH_TOLERANCE:
                edges.append((distance,left_group,right_group))
    # A one-to-one match is required.  Ambiguous geometry is evidence against
    # a Skills panel, and no arbitrary closest-line choice should become a
    # panel anchor.
    pairs=[]
    for distance,left_group,right_group in sorted(edges,key=lambda edge:(edge[0],edge[1]['center'],edge[2]['center'])):
        left_options=[edge for edge in edges if edge[1] is left_group]
        right_options=[edge for edge in edges if edge[2] is right_group]
        if len(left_options)==1 and len(right_options)==1:
            pairs.append(((left_group['center']+right_group['center'])/2,left_group,right_group))
    pairs.sort(key=lambda item:item[0])
    return pairs


def _level_associated_card_rows(lines, groups=None):
    """Return level markers that have one unambiguous card neighbour."""
    if groups is None:groups=_logical_card_groups(lines)
    groups=[group for group in groups if not group['invalid']]
    levels=[line for line in lines
            if isinstance(line,dict) and line.get('confidence',0)>=_CARD_CONFIDENCE
            and _LEVEL_RE.fullmatch(line.get('text','').strip())
            and _level_column(line) is not None]
    associated=[]
    for level in levels:
        center=_center_y(level)
        column=_level_column(level)
        matches=[group for group in groups
                 if group['column']==column and abs(group['center']-center)<=_ROW_MATCH_TOLERANCE]
        if len(matches)==1:associated.append((matches[0]['center'],matches[0],level))
    return associated


def _has_skill_grid_marker(lines, groups=None):
    pairs=_skill_grid_pairs(lines, groups)
    if len(pairs)<2:return False
    centers=[pair[0] for pair in pairs]
    return _grid_pair_pitch(centers) is not None


def _grid_pair_pitch(centers):
    """Return the row pitch for a paired grid, allowing one missing row."""
    gaps=[b-a for a,b in zip(centers,centers[1:])]
    normal=[gap for gap in gaps if 40<=gap<=80]
    if not normal:return None
    pitch=sorted(normal)[len(normal)//2]
    for gap in gaps:
        multiple=round(gap/pitch)
        if multiple not in (1,2) or abs(gap-multiple*pitch)>12:return None
    return pitch


def _is_lattice_singleton(group, row, pair_rows):
    """Allow a card at a proven grid edge when its row has no peer.

    A two-column Skills page can end with one card (the source corpus has a
    13-card page).  A lone OCR label in the middle of a grid is more likely a
    section heading, so only the rows immediately outside the established
    paired range are eligible.  An associated ``Lvl N`` marker is handled by
    the separate partial-card path.
    """
    if not pair_rows:return False
    edge_rows={min(pair_rows)-1,max(pair_rows)+1}
    return row in edge_rows


def _summary_panel(lines):
    inspiration=_has_inspiration_marker(lines)
    if inspiration:return 'inspiration'
    groups=_logical_card_groups(lines)
    # A bare level marker is not a panel identity.  Require repeated paired
    # card rows, or an unambiguous level/card relationship for a genuinely
    # partial page.  Detached levels cannot establish Skills semantics.
    return 'skills' if _has_skill_grid_marker(lines,groups) or _level_associated_card_rows(lines,groups) else None


def visible_cards(raw,final_attributes):
    if sum(type(v) is int for v in final_attributes.values())<3:return []
    lines=raw.get('lines',[])
    tabs={l.get('text','').strip() for l in lines if l.get('confidence',0)>=90
          and _box(l) is not None and 445<=_box(l)[1]<=470}
    if not {'Skills','Inspiration','Career Info'}<=tabs:return []
    if _summary_panel(lines)!='skills':return []

    groups=_logical_card_groups(lines)
    pairs=_skill_grid_pairs(lines,groups)
    level_associations=_level_associated_card_rows(lines,groups)
    if len(pairs)<2 and not level_associations:return []
    pair_centers=[pair[0] for pair in pairs]
    pair_pitch=_grid_pair_pitch(pair_centers) if pair_centers else None
    # One paired row plus its associated level marker is enough to identify a
    # partial Skills panel.  A cadence failure across multiple paired rows is
    # still rejected as unrelated layout.
    if len(pair_centers)>1 and pair_pitch is None:return []
    # Only paired, high-confidence card groups can define the row lattice.
    # Low-confidence punctuation and unrelated headings therefore cannot shift
    # the origin or merge a wrapped name into an adjacent row.
    origin=pair_centers[0] if pair_centers else level_associations[0][0]
    pitch=pair_pitch or _ROW_PITCH
    paired_groups={id(group) for _,left,right in pairs for group in (left,right)}
    level_groups={id(group) for _,group,_ in level_associations}
    pair_rows={round((center-origin)/pitch) for center in pair_centers}
    invalid_group_centers={column: [group['center'] for group in groups
                                    if group['invalid'] and group['column']==column]
                           for column in (0,1)}
    level_lines=[line for line in lines
                 if isinstance(line,dict) and line.get('confidence',0)>=_CARD_CONFIDENCE
                 and _LEVEL_RE.fullmatch(line.get('text','').strip())
                 and _level_column(line) is not None]
    cards=[]
    for group in groups:
        if group['invalid']:continue
        # A group must participate in the validated grid or have its own
        # unambiguous level marker.  This keeps an unrelated heading that
        # happens to lie inside the row lattice from becoming an owned card.
        paired_with_invalid=any(group['column']!=column and
                                abs(group['center']-center)<=_ROW_MATCH_TOLERANCE
                                for column,centers in invalid_group_centers.items()
                                for center in centers)
        row=round((group['center']-origin)/pitch)
        if (id(group) not in paired_groups and id(group) not in level_groups
                and not paired_with_invalid
                and not _is_lattice_singleton(group,row,pair_rows)):
            continue
        row_center=origin+pitch*row
        if row<0 or row>6 or abs(group['center']-row_center)>_ROW_MATCH_TOLERANCE:continue
        names=group['lines']
        levels=[line for line in level_lines
                if _level_column(line)==group['column']
                and abs((_center_y(line) or 0)-row_center)<=_ROW_MATCH_TOLERANCE]
        level_values={int(_LEVEL_RE.fullmatch(line['text'].strip())[1]) for line in levels}
        cards.append(dict(name_text=group['text'],slot=[row,group['column']],text_evidence=names,
                          observed_level=next(iter(level_values)) if len(level_values)==1 else None,
                          level_evidence=levels,
                          level_conflicts=sorted(level_values) if len(level_values)>1 else [],
                          variant_verified=False,level_verified=False,
                          semantics='visible_owned_card_text; details require temporal agreement'))
    return cards


def summarize(readings):
    groups={};frames=[]
    for reading in readings:
        cards=[card for card in reading.get('facts',{}).get('visible_owned_skill_cards',[])
               if card.get('panel','skills')=='skills']
        if not cards:continue
        frames.append(dict(timestamp_ms=reading['source_timestamp_ms'],evidence=reading['evidence'],visible_cards=len(cards)))
        for card in cards:
            groups.setdefault(card['name_text'],[]).append(dict(timestamp_ms=reading['source_timestamp_ms'],
                evidence=reading['evidence'],slot=card['slot'],text_evidence=card['text_evidence'],
                observed_level=card.get('observed_level'),level_evidence=card.get('level_evidence',[]),
                observed_variant=card.get('observed_variant'),variant_evidence=card.get('variant_evidence'),
                level_conflicts=card.get('level_conflicts',[]),variant_conflicts=card.get('variant_conflicts',[])))
    owned=[]
    for name,observations in groups.items():
        times=sorted(set(o['timestamp_ms'] for o in observations))
        if not any(0<b-a<=500 for a,b in zip(times,times[1:])):continue
        details={}
        for field in ('level','variant'):
            values={o.get('observed_'+field) for o in observations if o.get('observed_'+field) is not None}
            values.update(v for o in observations for v in o.get(field+'_conflicts',[]))
            value=next(iter(values)) if len(values)==1 else None
            supporting=sorted({o['timestamp_ms'] for o in observations if value is not None and o.get('observed_'+field)==value})
            repeated=any(0<b-a<=500 for a,b in zip(supporting,supporting[1:]))
            details[field]=value if repeated else None
            details[field+'_verified']=repeated
            if len(values)>1:details[field+'_conflicts']=sorted(values)
        owned.append(dict(name_text=name,observations=observations,**details))
    return dict(observed_owned_cards=owned,summary_frames=frames,complete=False,
                scope='Repeated visible final-summary card text only; not a complete inventory or purchase event.',
                unresolved=['Off-screen cards and scroll coverage are not established.','Missing symbol variants and levels remain unverified.'])
