"""The margins a recording's game keeps clear, fitted from its own frames.

A phone or tablet keeps rows at the top or bottom of its screen clear (a
camera cutout, a home bar) and the game lays its interface out inside them.
The margins are measured from labels every career shows many times, each at
the position it has on the PC pane and with the pin it follows: the turn
counter and the goal banner pinned to the top, the Back, Quick and Rest
buttons pinned to the bottom. Their shift from the PC pane, at the median
over sampled frames, gives the recording's top margin and, from the bottom,
its bottom margin. Across the screen every one of those labels must sit
where its pin and the frame's width put it; otherwise the recording is not
laid out the way the game lays itself out, and the fit refuses it rather
than read it wrongly.

A PC recording's game area is the client's pane, which keeps no margins,
and is not fitted.
"""
import statistics
from pathlib import Path

from .layout import Layout
from .pipeline import PipelineError

# Label text (lower case) -> (its top edge and its centre across, pane-local
# on the PC pane; its pin).
ANCHORS = {
    'turn(s)': (61, 201, 'tc'),
    'left': (78, 191, 'tc'),
    'entry criteria met!': (88, 377, 'tc'),
    'after this turn': (129, 169, 'tc'),
    'performance': (262, 60, 'tl'),
    'rest': (843, 202, 'bc'),
    'infirmary': (955, 193, 'bc'),
    'back': (1024, 75, 'bl'),
    'quick': (1038, 554, 'br'),
}
SAMPLES = 90
MINIMUM_CONFIDENCE = 95
# Enough sightings at each edge for a median that a few misreads cannot move.
MINIMUM_SIGHTINGS = 8
# How far, in working pixels, the labels may sit from where their pins put them across.
ACROSS_TOLERANCE = 6


def _sample(frames, count):
    if len(frames) <= count:
        return list(frames)
    step = len(frames) / count
    return [frames[int(index * step)] for index in range(count)]


def fit(report, root, reader):
    """The report's layout with its margins fitted; a PC layout is returned as it is."""
    layout = Layout.from_dict(report.get('layout'))
    if tuple(layout.pane) != (0, 0) + tuple(layout.frame):
        return layout
    down = {'t': [], 'b': []}
    across = []
    for frame in _sample(report['frames'], SAMPLES):
        with reader.Image.open(Path(root) / frame['evidence']) as image:
            pane = reader.np.array(image.convert('RGB').crop(layout.pane))
        result = reader.engine(pane[:, :, ::-1])
        for quad, text, score in zip(result.boxes if result.boxes is not None else [],
                                     result.txts or (), result.scores or ()):
            anchor = ANCHORS.get(' '.join(str(text).split()).lower())
            if anchor is None or float(score) * 100 < MINIMUM_CONFIDENCE:
                continue
            top, centre, pin = anchor
            down[pin[0]].append(float(quad[:, 1].min()) - top)
            shift = (float(quad[:, 0].min()) + float(quad[:, 0].max())) / 2 - centre
            across.append(shift - layout.offset(pin)[0])
    if len(down['t']) < MINIMUM_SIGHTINGS or len(down['b']) < MINIMUM_SIGHTINGS:
        raise PipelineError("The game's interface was not found in this recording "
                            f"(labels pinned to the top seen {len(down['t'])} times, to the bottom {len(down['b'])}).")
    if abs(statistics.median(across)) > ACROSS_TOLERANCE:
        raise PipelineError("The game's interface in this recording does not sit where its frame's width puts it.")
    top = max(0, round(statistics.median(down['t'])))
    bottom = max(0, round(layout.pane_height - 1080 - statistics.median(down['b'])))
    return Layout(layout.frame, layout.pane, top, bottom)
