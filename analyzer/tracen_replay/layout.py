"""Where the game's interface sits in the frames of one recording.

The game lays out one interface of 1080x1920 design units, scales it by
``min(width / 1080, height / 1920)`` of its game area, and pins each part of
it to an edge or a centre of that area. Frames are decoded at the scale that
gives a design unit the size it has on the PC pane (``UNIT`` pixels), so
every recording is read at the text size the readers were built for, in its
own shape and layout.

Every fixed box in the analyzer is written where it sits on the PC pane, in
the coordinates the reader reports lines in: the pane's own pixels shifted
right by ``ORIGIN_X``, the pane's left edge in a 1920x1080 PC frame. A box is
placed on another recording by the offset of the pin it follows:

- vertically ``t`` (the top of the game's clear area), ``m`` (the centre of
  the clear area), ``s`` (the centre of the whole game area, where popup
  windows sit) or ``b`` (the bottom of the clear area);
- horizontally ``l`` (left edge), ``c`` (centre) or ``r`` (right edge).

On a PC recording every offset is zero and a placed box is the box itself.
"""
import contextlib
from dataclasses import dataclass

# Pixels of the working frame per design unit: the PC pane's 1080 pixels for
# the game's 1920 units.
UNIT = 0.5625
# The PC pane in a 1920x1080 PC frame, and the x at which pane coordinates start.
REFERENCE_PANE = (148, 0, 958, 1080)
ORIGIN_X = REFERENCE_PANE[0]
REFERENCE_WIDTH = REFERENCE_PANE[2] - REFERENCE_PANE[0]
REFERENCE_HEIGHT = REFERENCE_PANE[3] - REFERENCE_PANE[1]
_VERTICAL = frozenset('tmsb')
_HORIZONTAL = frozenset('lcr')


@dataclass(frozen=True)
class Layout:
    """One recording's working frame, its game area and the game's clear margins.

    ``frame`` is the working frame's size; ``pane`` the game area in it
    (left, top, right, bottom); ``top`` and ``bottom`` the rows the game
    keeps clear at those edges of the game area (a camera cutout, a home
    bar), in working pixels. ``crop`` is the part of the recording the
    working frame is cut from (left, top, width, height in the recording's
    own pixels), for a game placed inside a wider video; None when the
    working frame is the whole recording.
    """
    frame: tuple
    pane: tuple
    top: int = 0
    bottom: int = 0
    crop: tuple = None

    @property
    def pane_width(self):
        return self.pane[2] - self.pane[0]

    @property
    def pane_height(self):
        return self.pane[3] - self.pane[1]

    @property
    def pane_box(self):
        """The game area in reader coordinates."""
        return (ORIGIN_X, 0, ORIGIN_X + self.pane_width, self.pane_height)

    @property
    def frame_box(self):
        """The whole working frame in reader coordinates."""
        left = ORIGIN_X - self.pane[0]
        return (left, -self.pane[1], left + self.frame[0], self.frame[1] - self.pane[1])

    def offset(self, pin):
        """The shift from a PC pane position to this recording for ``pin`` (vertical then horizontal)."""
        if len(pin) != 2 or pin[0] not in _VERTICAL or pin[1] not in _HORIZONTAL:
            raise ValueError(f'unknown pin {pin!r}')
        extra_height = self.pane_height - REFERENCE_HEIGHT
        dy = {'t': self.top,
              'b': extra_height - self.bottom,
              'm': (extra_height + self.top - self.bottom) // 2,
              's': extra_height // 2}[pin[0]]
        extra_width = self.pane_width - REFERENCE_WIDTH
        dx = {'l': 0, 'c': extra_width // 2, 'r': extra_width}[pin[1]]
        return dx, dy

    @property
    def is_reference(self):
        return (self.pane_width, self.pane_height, self.top, self.bottom) == (REFERENCE_WIDTH, REFERENCE_HEIGHT, 0, 0)

    def place(self, box, *pins):
        """``box`` (left, top, right, bottom) as it sits in this recording; the same object when nothing moves.

        With several pins the box covers every place they put it, for a
        region where parts pinned differently share the PC pane's space (the
        receipt box and the popup that also lists receipts). A moved box is
        kept inside the game area: the game draws nothing beyond it, and a
        centred part that reaches the PC pane's edge meets a narrower
        screen's edge.
        """
        shifts = {self.offset(pin) for pin in pins}
        if shifts == {(0, 0)}:
            return box
        left, top, right, bottom = self.pane_box
        moved = (max(left, box[0] + min(dx for dx, _ in shifts)), max(top, box[1] + min(dy for _, dy in shifts)),
                 min(right, box[2] + max(dx for dx, _ in shifts)), min(bottom, box[3] + max(dy for _, dy in shifts)))
        return list(moved) if isinstance(box, list) else moved

    def place_x(self, value, pin='c'):
        """A PC pane x coordinate as it sits in this recording, for a horizontal pin."""
        return value + self.offset('t' + pin)[0]

    def place_y(self, value, pin='t'):
        """A PC pane y coordinate as it sits in this recording, for a vertical pin."""
        return value + self.offset(pin + 'c')[1]

    def place_rows(self, top, bottom, *pins):
        """PC pane rows ``top`` to ``bottom`` as they sit in this recording, covering every place the vertical ``pins`` put them."""
        shifts = [self.offset(pin + 'c')[1] for pin in pins]
        return top + min(shifts), bottom + max(shifts)

    def to_frame(self, box):
        """A box in reader coordinates as pixels of the working frame."""
        dx, dy = self.pane[0] - ORIGIN_X, self.pane[1]
        if not dx and not dy:
            return box
        moved = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
        return list(moved) if isinstance(box, list) else moved

    def to_dict(self):
        out = dict(frame=list(self.frame), pane=list(self.pane), top=self.top, bottom=self.bottom)
        if self.crop is not None:
            out['crop'] = list(self.crop)
        return out

    @classmethod
    def from_dict(cls, value):
        if not value:
            return PC
        crop = value.get('crop')
        return cls(tuple(value['frame']), tuple(value['pane']), int(value.get('top', 0)), int(value.get('bottom', 0)),
                   tuple(crop) if crop is not None else None)


PC = Layout((1920, 1080), REFERENCE_PANE)
_current = PC


def current():
    """The layout of the recording this process is reading."""
    return _current


def use(layout):
    """Make ``layout`` (a Layout, its dict, or None for the PC layout) the current one; returns it."""
    global _current
    _current = layout if isinstance(layout, Layout) else Layout.from_dict(layout)
    return _current


@contextlib.contextmanager
def using(layout):
    """Read with ``layout`` for the length of a block."""
    global _current
    previous = _current
    use(layout)
    try:
        yield _current
    finally:
        _current = previous


def place(box, *pins):
    return _current.place(box, *pins)


def place_x(value, pin='c'):
    return _current.place_x(value, pin)


def place_y(value, pin='t'):
    return _current.place_y(value, pin)


def place_rows(top, bottom, *pins):
    return _current.place_rows(top, bottom, *pins)


def pane_box():
    """The game area in reader coordinates: x from ``ORIGIN_X``, y from 0."""
    return _current.pane_box


def pane_size():
    """The game area's width and height in working pixels."""
    return (_current.pane_width, _current.pane_height)


def frame_box():
    """The whole working frame in reader coordinates."""
    return _current.frame_box


def inside_pane(box):
    """Whether ``box`` (reader coordinates) lies within the game area, edges included."""
    left, top, right, bottom = _current.pane_box
    return left <= box[0] < box[2] <= right and top <= box[1] < box[3] <= bottom


def clamp(box):
    """``box`` cut to the game area; the same object when it already lies inside."""
    if inside_pane(box):
        return box
    left, top, right, bottom = _current.pane_box
    cut = (max(left, box[0]), max(top, box[1]), min(right, box[2]), min(bottom, box[3]))
    return list(cut) if isinstance(box, list) else cut


def even(value):
    """The nearest even whole number of pixels, at least 2; video frames need even sides."""
    return max(2, int(round(value / 2)) * 2)


def working_size(width, height):
    """The working frame for a game area of ``width`` x ``height`` recording pixels: its scale and size.

    The scale gives a design unit ``UNIT`` pixels; the size keeps the area's
    shape, rounded to even sides.
    """
    native = min(width / 1080, height / 1920)
    scale = UNIT / native
    return scale, (even(width * scale), even(height * scale))


