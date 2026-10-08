"""Make every logo and icon size from one master image.

    uv run --with pillow python tools/make_icons.py [path/to/master.png]

The master (default: brand-source/logo-original.png) should be square, at
least 1024 x 1024. It's kept out of static/ because it's large; this writes
small, web-ready copies into static/brand/:

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
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "brand"


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
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "brand-source" / "logo-original.png"
    img = Image.open(src).convert("RGB")
    if img.width != img.height:
        raise SystemExit(f"The master image should be square; this one is {img.width} x {img.height}.")
    OUT.mkdir(parents=True, exist_ok=True)
    save = dict(optimize=True)
    resized(img, 384).save(OUT / "logo.png", **save)
    resized(img, 32).save(OUT / "favicon-32.png", **save)
    resized(img, 48).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    resized(img, 180).save(OUT / "apple-touch-icon.png", **save)
    resized(img, 192).save(OUT / "icon-192.png", **save)
    resized(img, 512).save(OUT / "icon-512.png", **save)
    maskable(img).save(OUT / "icon-maskable-512.png", **save)
    for p in sorted(OUT.glob("*")):
        if p.suffix in (".png", ".ico"):
            print(f"  {p.name:24} {p.stat().st_size // 1024:>4} KB")


if __name__ == "__main__":
    main()
