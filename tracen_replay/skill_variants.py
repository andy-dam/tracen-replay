"""Recognize isolated circle suffixes that text OCR often drops from skill names."""
import argparse
import hashlib
import json
from pathlib import Path
from .vision import parse
from .refine_contrast import fingerprint
from .full_recording import save_json


def circle_suffix(pane,box):
    import cv2
    import numpy as np
    left,top,right,bottom=box
    crop=np.array(pane.convert('RGB').crop((right-148-32,top-2,right-148+3,bottom+2)))
    gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY);votes=[]
    for threshold in (190,200,210):
        mask=(gray<threshold).astype('uint8')*255
        contours,hierarchy=cv2.findContours(mask,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
        choices=[]
        if hierarchy is None:return None
        for index,contour in enumerate(contours):
            if hierarchy[0][index][3]!=-1:continue
            x,y,w,h=cv2.boundingRect(contour);area=cv2.contourArea(contour)
            if not (7<=x and 10<=w<=22 and 10<=h<=22 and .8<=w/h<=1.2 and area>=.6*w*h):continue
            # A suffix is separated from the final letter, not an 'o' in a word.
            if x<3 or mask[max(0,y):y+h,x-3:x].any():continue
            holes=[]
            for child,c in enumerate(contours):
                if hierarchy[0][child][3]!=index:continue
                xx,yy,ww,hh=cv2.boundingRect(c)
                if abs(xx+ww/2-x-w/2)<=2 and abs(yy+hh/2-y-h/2)<=2:holes.append(cv2.contourArea(c))
            if not holes:continue
            ratio=max(holes)/area
            nested=False
            for child,c in enumerate(contours):
                parent=hierarchy[0][child][3];depth=1
                while parent>=0 and parent!=index:
                    parent=hierarchy[0][parent][3];depth+=1
                if parent==index and depth>=3 and cv2.contourArea(c)>5:nested=True
            if nested:choices.append('double_circle')
            elif .65<=ratio<=1:choices.append('single_circle')
            elif .2<=ratio<=.5:choices.append('double_circle')
        if len(choices)!=1:return None
        votes+=choices
    return votes[0] if len(set(votes))==1 else None


def refine(root):
    from PIL import Image
    root=Path(root);dest=root/'skill-variants';dest.mkdir(exist_ok=True);count=0
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'));row=parse(raw)
        if row['screen']!='skill_selection':continue
        target=dest/path.name
        method='isolated_circle_geometry_three_threshold_agreement_v2'
        if target.exists() and json.loads(target.read_text(encoding='utf-8')).get('method')==method:continue
        image_path=root/raw['evidence'];observations=[]
        with Image.open(image_path) as pane:
            for card in row['facts'].get('skill_cards',[]):
                variant=circle_suffix(pane,card['name_box'])
                if variant:observations.append(dict(name=card['name'],name_box=card['name_box'],variant=variant))
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),observations=observations,method=method));count+=1
    print(json.dumps(dict(stage='skill_variant_refinement_complete',new_frames=count)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path);refine(parser.parse_args().output)
