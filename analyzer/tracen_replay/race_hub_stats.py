"""Read the lower race-day totals row from original gameplay OCR geometry."""
import re

FIELDS=('speed','stamina','power','guts','wit','skill_points')
LABELS=('Speed','Stamina','Power','Guts','Wit','Skill Pts')
# Full-frame coordinates for the supported English landscape layout. Label
# and value areas are separate so caps and preview gains cannot become totals.
COLUMNS=((285,377),(382,474),(478,568),(577,666),(670,752),(752,840))


def pixel_layout(image):
    """Use the same five blue-label checks as the ordinary current grid."""
    import numpy as np
    if image.size!=(810,1080):return False
    array=np.asarray(image.convert('RGB')).astype('int16')
    fractions=[]
    for x in (310,410,510,610,710):
        colors=array[760:779,x-148:x-148+35]
        fractions.append(float(((colors[:,:,2]>colors[:,:,0]+25)&
            (colors[:,:,1]>colors[:,:,0]+15)&(colors[:,:,2]>100)).mean()))
    return min(fractions)>.35


def observation(lines, *, grid_verified=False):
    def inside(line,box):
        bounds=line.get('box')
        if not isinstance(bounds,(list,tuple)) or len(bounds)!=4:return False
        x=(bounds[0]+bounds[2])/2;y=(bounds[1]+bounds[3])/2
        return box[0]<=x<=box[2] and box[1]<=y<=box[3]
    # This is a race-day control, not race participation or a result receipt.
    controls=[l for l in lines if l.get('confidence',0)>=95 and l.get('text')=='Race!'
              and inside(l,(440,890,660,990))]
    if not grid_verified and len(controls)!=1:return None
    values={};proofs={}
    for field,label,(left,right) in zip(FIELDS,LABELS,COLUMNS):
        labels=[l for l in lines if l.get('confidence',0)>=97 and l.get('text')==label
                and inside(l,(left,750,right,780))]
        candidates=[l for l in lines if l.get('confidence',0)>=97
                    and re.fullmatch(r'\d{1,4}',l.get('text',''))
                    and inside(l,(left,780,right,801))]
        if (not grid_verified and len(labels)!=1) or len(candidates)!=1:return None
        selected=candidates[0]
        values[field]=int(selected['text'])
        proofs[field]=dict(text=selected['text'],confidence=selected['confidence'],box=list(selected['box']))
    return dict(values=values,profile='race_day_lower_totals',field_observations=proofs)
