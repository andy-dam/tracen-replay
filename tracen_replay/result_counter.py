"""Read the current value before an explicit slash without inventing a cap."""
import re


def partial_counter(observation):
    if observation.get('confidence',0)<97:return None
    text=observation.get('text','').strip()
    match=re.fullmatch(r'(\d{1,4})/([1-9]\d{0,2})?',text)
    if not match:return None
    return dict(value=int(match[1]),raw_text=text,confidence=observation['confidence'],
                box=observation.get('box'),cap_unknown=True)
