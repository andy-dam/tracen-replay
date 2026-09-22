"""The stat bar's fixed value rectangles.

The geometry here is shared: ``vision`` opens these crops, and the modules
that refine or recover a value measure against them. Deciding that a frame
shows the bar is not done here. ``current_state_layout`` proves it from the
frame's own labels, values and caps, whatever colour the strip is painted.
"""

BOXES = [(309,721,366,747), (406,721,463,747), (503,721,560,747),
         (598,721,655,747), (692,721,747,747), (755,721,836,760)]
