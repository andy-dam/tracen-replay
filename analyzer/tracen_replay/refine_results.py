"""Read result totals with room for four-digit attributes and adjacent grade icons."""
import hashlib
import json
import re


def apply_result_refinement(raw,refinement):
    if refinement['raw_sha256']!=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest():raise ValueError('Result refinement source mismatch.')
    regions=dict(raw['regions'])
    def valid(name,observation):
        if observation.get('confidence',0)<90:return False
        text=observation.get('text','').strip()
        if name=='result.skill_points':return bool(re.fullmatch(r'\d{1,4}',text))
        match=re.fullmatch(r'(\d{1,4})/(\d{4})',text)
        return bool(match and int(match[1])<=int(match[2]))
    for name,observation in refinement['regions'].items():
        if valid(name,observation) and (observation['confidence']>=97 or not valid(name,regions.get(name,{}))):regions[name]=observation
    from .result_counter import partial_counter
    partials={name:observation for name,observation in refinement['regions'].items()
              if name!='result.skill_points' and partial_counter(observation)}
    return dict(raw,regions=regions,result_numerator_refinement=partials)

