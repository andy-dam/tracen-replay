"""Read the skill-menu counter directly when detection misses a small number."""
import re


def counter_reading(views,original=None):
    """Crop agreement is one frame's evidence, not independent observations."""
    values=[]
    for view in views:
        text=view.get('text','').strip()
        if view.get('confidence',0)>=97 and re.fullmatch(r'\d{1,4}',text):
            values.append(int(text))
    candidates=set(values)
    if type(original) is int:candidates.add(original)
    if len(candidates)>1:return None,sorted(candidates)
    if len(views)==2 and len(values)==2:return values[0],[]
    return original,[]

