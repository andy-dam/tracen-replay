"""Resolve a covered name suffix using repeated visible letters in the same slot."""
import re
from copy import deepcopy

from .source_clock import elapsed


def _overlap(a,b):
    return min(a[2],b[2])-max(a[0],b[0])>=3 and min(a[3],b[3])-max(a[1],b[1])>=3


def _aligned_name(row,effect):
    metadata=row.get('facts',{}).get('receipt_overlay_evidence',{})
    provenance=metadata.get('provenance')
    if not isinstance(provenance,dict) or provenance.get('validated_by')!='annotate_path':return None
    if provenance.get('source_timestamp_ms')!=row.get('source_timestamp_ms') or provenance.get('evidence')!=row.get('evidence'):return None
    def digest(value):return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value)
    if not all(digest(provenance.get(k)) for k in ('raw_sha256','evidence_sha256','source_sha256','source_frame_sha256','gameplay_sha256')):return None
    models=provenance.get('model_sha256')
    if not isinstance(models,dict) or not models or not all(digest(v) for v in models.values()):return None
    found=[]
    for item in metadata.get('alignments',[]):
        if item.get('confidence',0)<97:continue
        words=item.get('words',[]);columns=item.get('columns',[])
        text=' '.join(words)
        match=re.fullmatch(r'Friendship with (.+?) went up by (\d+)[.!]',text)
        if not match or match[1]!=effect['name'] or int(match[2])!=effect['amount']:continue
        if len(words)!=len(columns) or any(len(w)!=len(c) for w,c in zip(words,columns)):continue
        size=item.get('line_length',0);box=item.get('line_box',[])
        if size<=0 or len(box)!=4 or any(v<0 or v>=size for c in columns for v in c):continue
        original=[l for l in row.get('ocr',{}).get('neural',[]) if l.get('text')==effect.get('raw_text')]
        if len(original)!=1 or original[0].get('box')!=box:continue
        last=len(words)-4
        if last<=2 or words[last]!='went':continue
        x,y,z,w=box;scale=(z-x)/size;letters=[]
        for index in range(2,last):
            if index>2:letters.append((' ',None))
            letters.extend((char,[x+(col-1.5)*scale,y,x+(col+1.5)*scale,w])
                           for char,col in zip(words[index],columns[index]))
        if ''.join(char for char,_ in letters)!=effect['name']:continue
        # The exact name must be visibly clear in the supporting frame.
        if any(_overlap(glyph,cursor) for _,glyph in letters if glyph
               for cursor in metadata.get('overlay_boxes',[])):continue
        strictly_clear=not any(min(glyph[2],cursor[2])>max(glyph[0],cursor[0])
                               and min(glyph[3],cursor[3])>max(glyph[1],cursor[1])
                               for _,glyph in letters if glyph for cursor in metadata.get('overlay_boxes',[]))
        found.append(dict(box=box,letters=letters,alignment=item,strictly_clear=strictly_clear,provenance=provenance))
    return found[0] if len(found)==1 else None


def resolve(event,rows_by_evidence):
    """Only remove a false suffix variant; never add a new receipt or name.

    Two already accepted exact-name frames must establish the visible suffix.
    Every shorter reading must have a cursor over all omitted letters, with a
    continuous stationary receipt and unchanged neighboring text. OCR-only
    similarity, missing geometry and isolated gap frames are insufficient.
    """
    from .receipt_names import _dialogue_text_moved
    event['effects']=[deepcopy(e) if e['kind']=='friendship_change' else e for e in event['effects']]
    effects=[e for e in event['effects'] if e['kind']=='friendship_change']
    def key(effect):return 'friendship_change||'+effect['name']
    def observations(effect):
        result=[]
        for proof in dict.fromkeys(event['field_evidence'].get(key(effect),[])):
            row=rows_by_evidence.get(proof)
            if not row:continue
            matches=[e for e in row.get('effects',[]) if all(e.get(k)==effect.get(k)
                     for k in ('kind','name','amount')) and e.get('confidence',0)>=97]
            if len(matches)==1:
                aligned=_aligned_name(row,effect)
                if aligned:result.append((row,aligned))
        return result
    removed=[]
    for short in effects:
        candidates=[]
        for full in effects:
            if full is short or full['amount']!=short['amount'] or not full['name'].startswith(short['name']):continue
            if any(c['field'] in (key(short),key(full)) for c in event['conflicting_readings']):continue
            if len(full['name'])==len(short['name']) or ' ' in full['name'][len(short['name']):]:continue
            anchors=observations(full)
            if len({r['source_timestamp_ms'] for r,_ in anchors})<2:continue
            proofs=[]
            for proof in dict.fromkeys(event['field_evidence'].get(key(short),[])):
                row=rows_by_evidence.get(proof)
                if not row:break
                short_alignment=_aligned_name(row,short)
                if not short_alignment:break
                if any(e.get('kind')=='friendship_change' and e.get('name')==full['name']
                       for e in row.get('effects',[])):break
                lines=[l for l in row.get('ocr',{}).get('neural',[]) if l.get('text')==short.get('raw_text')]
                if len(lines)!=1:break
                box=lines[0]['box'];time=row['source_timestamp_ms'];matching=[]
                for anchor,aligned in anchors:
                    t=anchor['source_timestamp_ms']
                    if aligned['provenance']['source_sha256']!=short_alignment['provenance']['source_sha256']:continue
                    if not 0<elapsed(time,t)<=250 or max(abs(a-b) for a,b in zip(box,aligned['box']))>3:continue
                    prefix=aligned['letters'][:len(short['name'])]
                    if any(abs((a[0]+a[2]-b[0]-b[2])/2)>5
                           for (_,a),(_,b) in zip(short_alignment['letters'],prefix) if a and b):continue
                    span=sorted([r for r in rows_by_evidence.values() if time<=r['source_timestamp_ms']<=t],key=lambda r:r['source_timestamp_ms'])
                    if any(r['screen']!='event_outcome' for r in span):continue
                    if any(e.get('kind')=='friendship_change' and e.get('name') not in (short['name'],full['name'])
                           for r in span for e in r.get('effects',[])):continue
                    if any(elapsed(a['source_timestamp_ms'],b['source_timestamp_ms'])>250
                           or _dialogue_text_moved(a,b) for a,b in zip(span,span[1:])):continue
                    cursors=row.get('facts',{}).get('receipt_overlay_evidence',{}).get('overlay_boxes',[])
                    suffix=aligned['letters'][len(short['name']):]
                    if not suffix or any(not glyph or not any(_overlap(glyph,c) for c in cursors) for _,glyph in suffix):continue
                    matching.append(dict(evidence=anchor['evidence'],source_timestamp_ms=t,
                                         alignment=aligned['alignment'],provenance=aligned['provenance'],
                                         strictly_clear=aligned['strictly_clear'],omitted_letter_boxes=[g for _,g in suffix]))
                if len({p['source_timestamp_ms'] for p in matching})<2 or not any(p['strictly_clear'] for p in matching):break
                proofs.append(dict(evidence=proof,source_timestamp_ms=time,
                                   provenance=short_alignment['provenance'],
                                   overlay_boxes=row['facts']['receipt_overlay_evidence']['overlay_boxes'],anchors=matching))
            else:
                if proofs:candidates.append((full,proofs))
        if len(candidates)!=1:continue
        full,proofs=candidates[0]
        full.setdefault('occluded_name_variants',[]).append(dict(effect=dict(short),evidence=proofs))
        full['name_resolution']='repeated_visible_suffix_covered_in_shorter_reading'
        removed.append(short)
    event['effects']=[e for e in event['effects'] if e not in removed]
