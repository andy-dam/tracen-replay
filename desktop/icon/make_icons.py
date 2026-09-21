"""Builds every icon from the logo: python desktop/icon/make_icons.py [logo.png]

The logo (a transparent PNG, kept here as logo.png) is set on a squircle in the
application's own green. Outputs: appicon.png and icon.ico for the desktop
application and its installer, and the website's logo and favicons.
"""
from pathlib import Path
import sys

from PIL import Image, ImageChops, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOP, BOTTOM, EDGE = (147, 224, 74), (58, 158, 31), (255, 255, 255, 110)


def squircle_mask(size, inset, power=5.0):
    """A superellipse, drawn large and scaled down for a smooth edge."""
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    half = big / 2 - inset * 4
    points = []
    import math
    for step in range(720):
        angle = 2 * math.pi * step / 720
        c, s = math.cos(angle), math.sin(angle)
        x = half * (abs(c) ** (2 / power)) * (1 if c >= 0 else -1)
        y = half * (abs(s) ** (2 / power)) * (1 if s >= 0 else -1)
        points.append((big / 2 + x, big / 2 + y))
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def tile(size, logo):
    gradient = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    outer = squircle_mask(size, size * 0.03)
    inner = squircle_mask(size, size * 0.03 + max(1, size * 0.012))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(gradient, (0, 0), outer)
    rim = Image.new("RGBA", (size, size), EDGE)
    out.paste(rim, (0, 0), ImageChops.subtract(outer, inner))
    art = logo.crop(logo.getbbox())
    scale = size * 0.80 / max(art.size)
    art = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.LANCZOS)
    out.alpha_composite(art, ((size - art.width) // 2, (size - art.height) // 2))
    return out


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "logo.png"
    logo = Image.open(source).convert("RGBA")
    if source != HERE / "logo.png":
        logo.save(HERE / "logo.png", optimize=True)
    master = tile(1024, logo)
    master.save(HERE / "appicon.png", optimize=True)
    sizes = [256, 128, 64, 48, 32, 24, 16]
    master.save(HERE / "icon.ico", sizes=[(s, s) for s in sizes])
    public = ROOT / "web" / "public"
    master.resize((192, 192), Image.LANCZOS).save(ROOT / "web" / "src" / "assets" / "logo.png", optimize=True)
    master.resize((64, 64), Image.LANCZOS).save(public / "favicon.png", optimize=True)
    master.resize((180, 180), Image.LANCZOS).save(public / "apple-touch-icon.png", optimize=True)
    master.save(public / "favicon.ico", sizes=[(48, 48), (32, 32), (16, 16)])


if __name__ == "__main__":
    main()
