"""Make every logo and icon size from one master image.

    uv run --with pillow python tools/make_icons.py [edition] [path/to/master.png]

The edition defaults to the EDITION setting (or philosophy). The master
(default: brand-source/<edition>/logo-original.png) should be square, at
least 1024 x 1024. It's kept out of static/ because it's large; this writes
small, web-ready copies into static/brands/<edition>/:

    logo.png                welcome page (shown at up to 96 px, so 384 px for sharp screens)
    favicon.ico             browser tab (16, 32 and 48 px inside one file)
    favicon-32.png          browser tab, PNG version
    apple-touch-icon.png    iPhone / iPad "Add to Home Screen" (180 px, no transparency)
    icon-192.png            Android / desktop "Install app"
    icon-512.png            Android / desktop "Install app", large
    icon-maskable-512.png   Android's circle / rounded-square crops: the image is
                            shrunk to the safe middle 80% and the edges extended

Run it again whenever the logo changes, then push.
"""
import json
import math
import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EDITION = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDITION", "philosophy")).strip().lower()
OUT = ROOT / "static" / "brands" / EDITION

# The browser-tab icon is simpler than the logo, so it's readable at 16 px:
# the edition's "tab_drawing" (a line drawing, e.g. In His Steps' sandal), or
# else the first letter of its name (or its "tab_letter"), white on its blue.
_edition = json.loads((ROOT / "editions" / EDITION / "edition.json").read_text(encoding="utf-8"))
TAB_LETTER = _edition.get("tab_letter") or _edition["app_name"].removeprefix("The ")[0]
TAB_FONT = ROOT / "static" / "fonts" / _edition.get("tab_font", "HomemadeApple-Regular.ttf")   # or EBGaramond-Variable.ttf
TAB_BLUE = tuple(bytes.fromhex(_edition.get("tab_colour", "#2A4B71").lstrip("#")))   # the background colour


def letter_icon(size=512, fill=0.74, radius=0.2, weight=0.03):
    """The letter centred on a rounded blue square, drawn large and scaled
    down by the caller (smoother than drawing tiny)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), int(size * radius), fill=TAB_BLUE + (255,))
    # Find the font size at which the letter's actual ink fills `fill` of the square.
    probe = ImageFont.truetype(str(TAB_FONT), 1000)
    l, t, r, b = probe.getbbox(TAB_LETTER)
    scale = fill * size / max(r - l, b - t)
    font = ImageFont.truetype(str(TAB_FONT), int(1000 * scale))
    l, t, r, b = font.getbbox(TAB_LETTER)
    # A white outline thickens the fine pen strokes so they survive at 16 px.
    stroke = max(1, int(size * weight))
    draw.text(((size - (r - l)) / 2 - l, (size - (b - t)) / 2 - t), TAB_LETTER, font=font, fill="white",
              stroke_width=stroke, stroke_fill="white")
    return img


def svg_points(d, steps=24):
    """Flatten a simple SVG path (M, L, C, S and Z, absolute or relative) into
    lists of (x, y) points, one list per sub-path. Enough for line icons."""
    tokens = re.findall(r"[MmLlCcSsZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    shapes, pts, i, cmd = [], [], 0, None
    x = y = start_x = start_y = 0.0
    last_ctrl = None

    def num():
        nonlocal i
        i += 1
        return float(tokens[i - 1])

    def curve(p0, p1, p2, p3):
        for k in range(1, steps + 1):
            s = k / steps
            pts.append(tuple((1 - s) ** 3 * a + 3 * (1 - s) ** 2 * s * b + 3 * (1 - s) * s * s * c + s ** 3 * e
                             for a, b, c, e in zip(p0, p1, p2, p3)))

    while i < len(tokens):
        if re.fullmatch(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                pts.append((start_x, start_y))
                x, y = start_x, start_y
                continue
        rel = cmd.islower()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if cmd in "Mm":
            if pts:
                shapes.append(pts)
            x, y = ox + num(), oy + num()
            start_x, start_y, pts, last_ctrl = x, y, [(x, y)], None
            cmd = "l" if rel else "L"      # further pairs after a move are lines
        elif cmd in "Ll":
            x, y = ox + num(), oy + num()
            pts.append((x, y))
            last_ctrl = None
        elif cmd in "Cc":
            c1 = (ox + num(), oy + num())
            c2 = (ox + num(), oy + num())
            end = (ox + num(), oy + num())
            curve((x, y), c1, c2, end)
            last_ctrl, (x, y) = c2, end
        elif cmd in "Ss":
            c1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl else (x, y)
            c2 = (ox + num(), oy + num())
            end = (ox + num(), oy + num())
            curve((x, y), c1, c2, end)
            last_ctrl, (x, y) = c2, end
    if pts:
        shapes.append(pts)
    return shapes


def drawing_icon(spec, size=512, fill=0.8, radius=0.2):
    """A line drawing (the same paths as a welcome-page icon), white on the
    rounded blue square: e.g. In His Steps' sandal. spec = {"paths": [...],
    "rotate": degrees, "stroke": width in the drawing's 24-unit grid}."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), int(size * radius), fill=TAB_BLUE + (255,))
    angle = math.radians(spec.get("rotate", 0))
    turn = lambda px, py: (12 + (px - 12) * math.cos(angle) - (py - 12) * math.sin(angle),
                           12 + (px - 12) * math.sin(angle) + (py - 12) * math.cos(angle))
    shapes = [[turn(*pt) for pt in shape] for d in spec["paths"] for shape in svg_points(d)]
    xs = [pt[0] for s in shapes for pt in s]
    ys = [pt[1] for s in shapes for pt in s]
    stroke = spec.get("stroke", 2.0)
    scale = fill * size / (max(max(xs) - min(xs), max(ys) - min(ys)) + stroke)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    width = max(1, round(stroke * scale))
    for shape in shapes:
        pts = [(size / 2 + (px - cx) * scale, size / 2 + (py - cy) * scale) for px, py in shape]
        draw.line(pts, fill="white", width=width, joint="curve")
        for px, py in (pts[0], pts[-1]):            # round ends
            draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill="white")
    return img


def colour_mask(img, size=160):
    """For logos drawn in one dark colour on white (both of ours): a white image
    whose transparency says where the dark colour goes. The header lays it over
    a square in the current theme's colour, so the logo follows the theme."""
    small = resized(img, size).convert("L")
    counts, total = small.histogram(), size * size

    def level(share):                    # the brightness below which `share` of the pixels fall
        seen = 0
        for value, n in enumerate(counts):
            seen += n
            if seen >= share * total:
                return value
        return 255
    dark, light = level(0.02), level(0.98)   # ignore the odd stray pixel
    span = max(1, light - dark)
    alpha = small.point(lambda v: max(0, min(255, round((light - v) * 255 / span))))
    mask = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    mask.putalpha(alpha)
    return mask


def resized(img, size):
    return img.resize((size, size), Image.LANCZOS)


def maskable(img, size=512, safe=0.80):
    """Shrink the art into the safe zone and fill the margin by stretching the
    outermost row/column of pixels, so a solid or split background carries on."""
    inner = int(size * safe)
    art = resized(img, inner)
    pad = (size - inner) // 2
    canvas = Image.new("RGB", (size, size))
    canvas.paste(art, (pad, pad))
    left, right = art.crop((0, 0, 1, inner)), art.crop((inner - 1, 0, inner, inner))
    canvas.paste(left.resize((pad, inner)), (0, pad))
    canvas.paste(right.resize((size - pad - inner, inner)), (pad + inner, pad))
    top = canvas.crop((0, pad, size, pad + 1))
    bottom = canvas.crop((0, pad + inner - 1, size, pad + inner))
    canvas.paste(top.resize((size, pad)), (0, 0))
    canvas.paste(bottom.resize((size, size - pad - inner)), (0, pad + inner))
    return canvas


def main():
    src = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "brand-source" / EDITION / "logo-original.png"
    if not src.exists():
        # No logo yet: a stand-in made from the letter, until a real one is added.
        print(f"No logo at {src.relative_to(ROOT)}; using the letter {TAB_LETTER} as a stand-in.")
        img = letter_icon(1024, radius=0).convert("RGB")
    else:
        img = Image.open(src).convert("RGB")
    if img.width != img.height:
        raise SystemExit(f"The master image should be square; this one is {img.width} x {img.height}.")
    OUT.mkdir(parents=True, exist_ok=True)
    save = dict(optimize=True)
    resized(img, 384).save(OUT / "logo.png", **save)
    colour_mask(img).save(OUT / "logo-mask.png", **save)     # the header's theme-coloured logo
    tab = drawing_icon(_edition["tab_drawing"], size=1536) if _edition.get("tab_drawing") else letter_icon()
    tab.resize((32, 32), Image.LANCZOS).save(OUT / "favicon-32.png", **save)
    tab.resize((48, 48), Image.LANCZOS).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    resized(img, 180).save(OUT / "apple-touch-icon.png", **save)
    resized(img, 192).save(OUT / "icon-192.png", **save)
    resized(img, 512).save(OUT / "icon-512.png", **save)
    maskable(img).save(OUT / "icon-maskable-512.png", **save)
    for p in sorted(OUT.glob("*")):
        if p.suffix in (".png", ".ico"):
            print(f"  {p.name:24} {p.stat().st_size // 1024:>4} KB")


if __name__ == "__main__":
    main()
