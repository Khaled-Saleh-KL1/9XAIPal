"""Render the PWA icons from the brand mark (see public/favicon.svg).

Usage: python3 frontend/scripts/generate-pwa-icons.py
Needs Pillow and Georgia (macOS). Output is committed; rerun only if the mark changes.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "public" / "icons"
FONT = "/System/Library/Fonts/Supplemental/Georgia.ttf"
FG, BG = (26, 21, 16), (252, 250, 246)  # favicon.svg: #1a1510 square, #fcfaf6 glyph
SS = 4  # supersample


def render(size: int, maskable: bool) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:
        d.rectangle([0, 0, s, s], fill=FG)  # full bleed; OS applies the mask
        glyph = 0.55 * s * 0.62  # keeps the "9" inside the 80% safe zone
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.25), fill=FG)
        glyph = 0.55 * s
    font = ImageFont.truetype(FONT, int(glyph))
    d.text((s / 2, s / 2), "9", font=font, fill=BG, anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


for name, size, maskable in [
    ("icon-192.png", 192, False),
    ("icon-512.png", 512, False),
    ("icon-maskable-512.png", 512, True),
]:
    render(size, maskable).save(OUT / name, optimize=True)
    print("wrote", name)
