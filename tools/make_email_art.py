"""Makes the pictures the invitation email uses, for each edition:

  static/brands/<edition>/email-wordmark.png   the app's name, handwritten
                                               (Homemade Apple) in the cover's
                                               cream, on a clear background

Email programs can't load the app's fonts, so the name goes in as a picture,
like the logo. Made at twice the size it's shown, for sharp screens.

Run after changing an edition's name:
  .venv/Scripts/python.exe tools/make_email_art.py
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "static" / "fonts" / "HomemadeApple-Regular.ttf"
CREAM = (243, 239, 228)       # the cover's lettering: hsl(paper-hue 40% 94%)
SIZE = 76                     # shown at half: about 38px
PAD = 24
GAP = 0.32                    # between words, as a share of the size: the swashes otherwise close the gaps


def wordmark(name, out):
    font = ImageFont.truetype(str(FONT), SIZE)
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    words = [(w, measure.textbbox((0, 0), w, font=font)) for w in name.split()]
    gap = round(SIZE * GAP)
    top = min(b[1] for _, b in words)
    width = sum(b[2] - b[0] for _, b in words) + gap * (len(words) - 1)
    img = Image.new("RGBA", (width + 2 * PAD, max(b[3] for _, b in words) - top + 2 * PAD), (0, 0, 0, 0))
    draw, x = ImageDraw.Draw(img), PAD
    for word, (left, _, right, _) in words:
        draw.text((x - left, PAD - top), word, font=font, fill=CREAM + (255,))
        x += right - left + gap
    img.save(out, optimize=True)
    return img.size


for folder in sorted((ROOT / "editions").iterdir()):
    edition = json.loads((folder / "edition.json").read_text(encoding="utf-8"))
    out = ROOT / "static" / "brands" / folder.name / "email-wordmark.png"
    print(folder.name, wordmark(edition["app_name"], out), out.relative_to(ROOT))
