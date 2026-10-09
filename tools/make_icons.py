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
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EDITION = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDITION", "philosophy")).strip().lower()
OUT = ROOT / "static" / "brands" / EDITION

# The browser-tab icon is simpler than the logo, so it's readable at 16 px:
# the first letter of the edition's name (or its "tab_letter") in the
# handwriting font, white on the app's blue.
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
    tab = letter_icon()
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
