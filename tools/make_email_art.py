"""Makes the pictures the invitation email uses, for each edition:

  static/brands/<edition>/email-wordmark.png   the app's name, handwritten
                                               (Homemade Apple) in the cover's
                                               cream, on a clear background
  static/brands/<edition>/email-vignette.png   the edition's email_vignette
                                               drawing, in the theme's blue
  static/email-vignettes/<theme>.png           every theme's drawing, the same
                                               way (a shared passage's drawing)
  static/brands/<edition>/og/<theme>.jpg       a passage link's preview card
                                               (and _default.jpg)

Email programs can't load the app's fonts or recolour a drawing (the app paints
its drawings in the theme colour through a mask), so these go in as finished
pictures. Made at twice the size they're shown, for sharp screens.

Run after changing an edition's name or its email drawing:
  .venv/Scripts/python.exe tools/make_email_art.py
"""
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "static" / "fonts" / "HomemadeApple-Regular.ttf"
CREAM = (243, 239, 228)       # the cover's lettering: hsl(paper-hue 40% 94%)
BLUE = (44, 81, 128)          # the theme's accent, #2c5180: hsl(212 48% 33%)
VIGNETTE_WIDTH = 600          # shown at 300px
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


def vignette(slug, out):
    """The drawing (a mask: the lines are its opaque parts) in blue, 85% strong as in the app."""
    mask = Image.open(ROOT / "static" / "vignettes" / f"{slug}.png").convert("RGBA").getchannel("A")
    mask = mask.resize((VIGNETTE_WIDTH, round(mask.height * VIGNETTE_WIDTH / mask.width)), Image.LANCZOS)
    img = Image.new("RGBA", mask.size, BLUE + (0,))
    img.putalpha(mask.point(lambda a: round(a * 0.85)))
    img.save(out, optimize=True)
    return img.size


PAGE = (241, 245, 249)        # the email's page colour, #f1f5f9
COVER = (34, 54, 77)          # the cover, #22364d


def og_image(slug, out, logo, wordmark):
    """The picture for a passage link's preview card (Messages, WhatsApp...): 1200x630,
    the theme's drawing on the page, and the cover's band with the logo and name."""
    img = Image.new("RGB", (1200, 630), PAGE)
    mask = Image.open(ROOT / "static" / "vignettes" / f"{slug}.png").convert("RGBA").getchannel("A")
    mask = mask.resize((880, 440), Image.LANCZOS).point(lambda a: round(a * 0.85))
    img.paste(Image.new("RGB", mask.size, BLUE), (160, 20), mask)
    band = 150
    img.paste(Image.new("RGB", (1200, band), COVER), (0, 630 - band))
    mark = logo.resize((96, 96), Image.LANCZOS)
    words = wordmark.resize((round(wordmark.width * 100 / wordmark.height), 100), Image.LANCZOS)
    left = (1200 - (96 + 24 + words.width)) // 2
    img.paste(mark, (left, 630 - band + (band - 96) // 2))
    img.paste(words, (left + 96 + 24, 630 - band + (band - 100) // 2), words)
    img.save(out, quality=82, optimize=True)


def og_images(folder, edition):
    """One preview picture per theme of the edition, and one for the edition's own drawing."""
    brand = ROOT / "static" / "brands" / folder.name
    out = brand / "og"
    out.mkdir(exist_ok=True)
    logo = Image.open(brand / "logo.png").convert("RGB")
    wordmark = Image.open(brand / "email-wordmark.png").convert("RGBA")
    names = [l.strip() for l in (folder / "themes.txt").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")] if (folder / "themes.txt").exists() else []
    made = 0
    for name in names:
        slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’]", "", name.lower()).replace("&", " and ")).strip("-")
        if (ROOT / "static" / "vignettes" / f"{slug}.png").exists():
            og_image(slug, out / f"{slug}.jpg", logo, wordmark)
            made += 1
    if edition.get("email_vignette"):
        og_image(edition["email_vignette"], out / "_default.jpg", logo, wordmark)
    return made


out_dir = ROOT / "static" / "email-vignettes"
out_dir.mkdir(exist_ok=True)
for drawing in sorted((ROOT / "static" / "vignettes").glob("*.png")):
    vignette(drawing.stem, out_dir / drawing.name)
print("themes", len(list(out_dir.glob("*.png"))), "drawings in", out_dir.relative_to(ROOT))

for folder in sorted((ROOT / "editions").iterdir()):
    edition = json.loads((folder / "edition.json").read_text(encoding="utf-8"))
    brand = ROOT / "static" / "brands" / folder.name
    print(folder.name, "name", wordmark(edition["app_name"], brand / "email-wordmark.png"))
    if edition.get("email_vignette"):
        print(folder.name, edition["email_vignette"], vignette(edition["email_vignette"], brand / "email-vignette.png"))
    print(folder.name, "link previews", og_images(folder, edition))
