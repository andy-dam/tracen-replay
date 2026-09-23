"""Read large event stat and currency awards without surrounding balance changes."""
import re

from .gameplay import receipt_rows
from .layout import place
from .source_clock import elapsed


LABELS={'Dance':'dance','Passion':'passion','Vocals':'vocal','Visuals':'visual','Composure':'composure'}
# One moment of the base sampling: an outlier read on dense frames this
# close together is one sighting, not several.
_OUTLIER_MOMENT_MS=250
STAT_LABELS={'Speed':'speed','Stamina':'stamina','Power':'power','Guts':'guts','Wit':'wit','Skill Pts':'skill_points'}


def _label_field(text):
    """Return one performance field token from a noisy label line.

    The event animation sometimes prefixes a field label with a decorative
    two-letter badge (for example ``Me Composure``).  The token itself is the
    stable identity; accepting exactly one token keeps arbitrary narrative
    text, cap labels, and combined labels out of the candidate set.
    """
    value=' '.join(str(text or '').split())
    if re.search(r'\b(?:cap|bonus)\b',value,re.I):
        return None
    matches=[]
    for canonical,field in LABELS.items():
        pattern=r'(?<![A-Za-z])'+re.escape(canonical.rstrip('s'))+r's?(?![A-Za-z])'
        if re.search(pattern,value,re.I):
            matches.append((canonical,field))
    return matches[0] if len(matches)==1 else None


def _caption(label, lines):
    """Find a same-field award caption, retaining whether its verb is whole.

    ``went by`` is an OCR omission of the fixed ``up`` token in a positive
    performance award.  It is only eligible when the same source frame also
    contains exactly one signed positive gain for this field; the caption
    alone never establishes direction.
    """
    canonical, _field = label
    pattern=r'(?<![A-Za-z])'+re.escape(canonical.rstrip('s'))+r's?(?![A-Za-z])'
    top,bottom=receipt_rows()
    for line in lines:
        text=' '.join(str(line.get('text','')).split())
        if line.get('confidence',0)<95 or not top<=line.get('box',[0,0,0,0])[1]<bottom:
            continue
        exact=re.match(r'^'+pattern+r'\s+went\s+(up|down)\s+by(?:\s+(\d{1,3}))?',text,re.I)
        if exact:
            return dict(line=line,direction=exact.group(1).lower(),
                        amount=int(exact.group(2)) if exact.group(2) else None,
                        normalization=None)
        missing=re.match(r'^'+pattern+r'\s+went\s*by\s+(\d{1,3})',text,re.I)
        if missing:
            return dict(line=line,direction=None,amount=int(missing.group(1)),
                        normalization='signed_positive_animation_repairs_omitted_up')
    return None


def candidates(lines,screen,*,stat=False,allow_cap_coexistence=False):
    if screen!='event_outcome':return []
    found=[]
    # The award badges are pinned to the centre of the clear area.
    left,top,right,bottom=place((150,240,900,750 if stat else 650),'mc')
    receipt_top,receipt_bottom=receipt_rows()
    for label in lines:
        label_identity = None if stat else _label_field(label.get('text'))
        field=(STAT_LABELS if stat else LABELS).get(label['text']) if stat else (label_identity[1] if label_identity else None)
        a,b,c,d=label['box']
        if not field or label['confidence']<97 or not (left<=a<c<=right and top<=b<d<=bottom and 30<=d-b<=(70 if stat else 100)):continue
        canonical = next((name for name,value in (STAT_LABELS if stat else LABELS).items() if value==field),label['text'])
        caption = _caption((canonical,field),lines) if not stat else None
        captions=[caption['line']] if caption else []
        if stat:
            # Stat animations retain their literal receipt anchor. Performance
            # caption normalization must not remove this shared-reader input.
            captions=[line for line in lines if line['confidence']>=95
                      and receipt_top<=line['box'][1]<receipt_bottom
                      and re.match(r'^'+re.escape(canonical)+r' went up by\b',line['text'])]
        if not captions and not stat:continue
        if not captions and not allow_cap_coexistence and any(re.match(r'^'+re.escape(canonical)+r'\s+(?:cap|Bonus)\b',l['text'],re.I) for l in lines):continue
        gains=[]
        for line in lines:
            x,y,z,w=line['box'];match=re.fullmatch(r'\+(\d{1,3})',line['text'])
            # Animated cards can travel through the label before settling. A
            # bounded 180 px vertical association covers both directions while
            # the narrow horizontal center check and single-gain guard prevent
            # pairing adjacent performance columns.
            if match and line['confidence']>=97 and 50<=w-y<=(130 if stat else 110) and (-20 if stat else -180)<=b-w<=(25 if stat else 35) and abs((x+z-a-c)/2)<=60:
                gains.append(line)
        if len(gains)!=1:continue
        gain=gains[0]
        # A signed ``+N`` animation cannot support a downward receipt.  Keep
        # this explicit direction guard so a malformed or contradictory
        # caption never turns a positive card into an accepted award.
        if caption and caption.get('direction') == 'down':
            continue
        if caption and caption['amount'] is not None and int(caption['amount']) != int(gain['text'][1:]):
            continue
        if caption and caption['direction'] is None:
            # A missing direction can only be repaired by the explicitly
            # positive signed animation above. Never treat ``went by`` as a
            # generic up/down guess.
            receipt_normalization=caption['normalization']
            direction_basis='same_frame_signed_positive_gain'
        else:
            receipt_normalization=None
            direction_basis=None
        found.append(dict(kind='stat_change' if stat else 'performance_change',field=field,amount=int(gain['text'][1:]),
            raw_text=gain['text']+' '+label['text'],confidence=min(gain['confidence'],label['confidence']),
            gain_box=gain['box'],label_box=label['box'],receipt_text=captions[0]['text'] if captions else None,
            **({'label_text':label['text']} if label['text'] != canonical else {}),
            **({'receipt_normalization':receipt_normalization,'direction_basis':direction_basis}
               if receipt_normalization else {})))
    return found


def reconcile(event,readings,*,stat=False,receipt_observations=None):
    fact='animated_stat_candidates' if stat else 'animated_performance_candidates'
    proof_key='animated_stat_evidence' if stat else 'animated_performance_evidence'
    kind='stat_change' if stat else 'performance_change'
    groups={}
    for row in readings:
        if not event['first_seen_ms']<=row['source_timestamp_ms']<=event['last_seen_ms']:continue
        for candidate in row.get('facts',{}).get(fact,[]):
            groups.setdefault(candidate['field'],[]).append((row,candidate))
    if stat:
        from .award_tracking import tracked_stats,coexisting_cap_stats
        supplemental=tracked_stats(readings,event['first_seen_ms'],event['last_seen_ms'])
        supplemental+=coexisting_cap_stats(readings,event['first_seen_ms'],event['last_seen_ms'],receipt_observations or {})
        for row,candidate in supplemental:
            existing=groups.setdefault(candidate['field'],[])
            if not any(r['source_timestamp_ms']==row['source_timestamp_ms'] and c['amount']==candidate['amount'] for r,c in existing):
                existing.append((row,candidate))
    for field,observations in groups.items():
        values={c['amount'] for _,c in observations};times={r['source_timestamp_ms'] for r,_ in observations}
        repaired_sparse = (
            not stat
            and len(observations) >= 2
            and all(c.get('receipt_normalization') == 'signed_positive_animation_repairs_omitted_up'
                    for _, c in observations)
        )
        minimum_frames = 2 if repaired_sparse else 3
        if len(values)!=1 or len(times)<minimum_frames or max(times)-min(times)<50:continue
        if not any(c.get('receipt_text') or c.get('receipt_anchors') for _,c in observations):continue
        key=kind+'|'+field+'|';candidate=observations[0][1]
        prior=event['effects'].get(key)
        conflicts=[c for c in event['conflicting_readings'] if c['field']==key]
        observed=(receipt_observations or {}).get(key,[])
        amounts={e.get('amount') for _,_,e in observed}
        prefix_resolution=(stat and bool(observed) and all(type(n) is int and str(candidate['amount']).startswith(str(n)) for n in amounts)
                           and any(n!=candidate['amount'] for n in amounts)
                           and all(c.get('reason')=='changing_effect_value' for c in conflicts))
        matching={t for t,_,e in observed if e.get('amount')==candidate['amount']}
        others={t for t,_,e in observed if e.get('amount')!=candidate['amount']}
        # A dense reread samples one moment several times, so the single
        # outlier is one moment's frames: a read within a quarter second.
        display_agreement=(stat and len(matching)>=3 and max(matching)-min(matching)>=50
                           and bool(others) and elapsed(min(others),max(others))<=_OUTLIER_MOMENT_MS
                           and all(c.get('reason')=='changing_effect_value' for c in conflicts))
        if (prefix_resolution or display_agreement) and (conflicts or prior and prior['amount']!=candidate['amount']):
            event.setdefault('resolved_reading_conflicts',[]).append(dict(field=key,observed_amounts=sorted(amounts),
                accepted_amount=candidate['amount'],basis='repeated_labeled_animation_resolves_receipt_prefixes' if prefix_resolution else 'repeated_receipt_and_animation_resolve_single_outlier',
                receipt_evidence=[evidence for _,evidence,_ in observed]))
            event['conflicting_readings']=[c for c in event['conflicting_readings'] if c['field']!=key]
            event['effects'].pop(key,None);prior=None
        elif conflicts:continue
        if prior and prior['amount']!=candidate['amount']:
            event['effects'].pop(key)
            event['conflicting_readings'].append(dict(field=key,reason='receipt_animation_disagreement',
                receipt_amount=prior['amount'],animated_amount=candidate['amount']))
            continue
        proofs=[dict(source_timestamp_ms=r['source_timestamp_ms'],evidence=r['evidence'],
                     gain_box=c['gain_box'],label_box=c['label_box'],receipt_text=c['receipt_text'],
                     **{k:c[k] for k in ('label_anchors','label_identity_basis','receipt_anchors','cap_disambiguation') if k in c}) for r,c in observations]
        event.setdefault(proof_key,{})[field]=proofs
        if not prior:
            event['effects'][key]=dict(candidate,confirmation='repeated_labeled_award_animation')
            event['field_evidence'][key]=[r['evidence'] for r,_ in observations]
