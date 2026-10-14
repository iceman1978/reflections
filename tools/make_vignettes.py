"""Make the small theme drawings shown at the top of a reflection.

    uv run --with pillow python tools/make_vignettes.py

Reads every drawing in brand-source/vignettes/ (dark pencil on white, named after
its theme: "family.png", "grief-and-loss.jpg") and writes a web-ready copy to
static/vignettes/<theme>.png: cropped to the drawing, padded to the same wide 5:2
shape, and turned into a cut-out (white becomes transparent, pencil stays), so
the page can paint it in the reader's theme color, the way it does the logo.

Run it again whenever you add or change a drawing, then push.
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand-source" / "vignettes"
OUT = ROOT / "static" / "vignettes"
WIDTH, HEIGHT = 1000, 400         # 5:2, a wide strip: the full width of the reflection (up to about 460 x 184 px)
MARGIN = 0.03                     # white space kept around the drawing, as a share of its size
INK = 236                         # brightness below which a pixel counts as part of the drawing


def vignette(path):
    gray = Image.open(path).convert("L")
    box = gray.point(lambda v: 255 if v < INK else 0).getbbox()
    if not box:
        raise SystemExit(f"{path.name}: no drawing found (is the background white?)")
    left, top, right, bottom = box
    pad = int(max(right - left, bottom - top) * MARGIN)
    drawing = gray.crop((max(0, left - pad), max(0, top - pad),
                         min(gray.width, right + pad), min(gray.height, bottom + pad)))
    # Fit it into the 5:2 frame, centered, keeping its proportions.
    scale = min(WIDTH / drawing.width, HEIGHT / drawing.height)
    drawing = drawing.resize((max(1, round(drawing.width * scale)), max(1, round(drawing.height * scale))), Image.LANCZOS)
    frame = Image.new("L", (WIDTH, HEIGHT), 255)
    frame.paste(drawing, ((WIDTH - drawing.width) // 2, (HEIGHT - drawing.height) // 2))
    # The darkest pencil becomes fully opaque and paper white fully clear, so faint
    # strokes stay faint. A little paper grain or off-white tint is removed too.
    counts = frame.histogram()
    total = WIDTH * HEIGHT
    seen, dark = 0, 0
    for value, n in enumerate(counts):
        seen += n
        if seen >= 0.005 * total:
            dark = value
            break
    paper = 246
    span = max(1, paper - dark)
    alpha = frame.point(lambda v: max(0, min(255, round((paper - v) * 255 / span))))
    out = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    out.putalpha(alpha)
    return out


def slug(name):
    """'Grief & loss' -> 'grief-and-loss' (the same rule the page uses)."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", " and ")).strip("-")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in SRC.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    if not files:
        raise SystemExit(f"No drawings in {SRC.relative_to(ROOT)}")
    for path in files:
        target = OUT / f"{slug(path.stem)}.png"
        vignette(path).save(target, optimize=True)
        print(f"{path.name} -> {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
