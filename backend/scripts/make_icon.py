
# backend/scripts/make_icon.py

"""
Build the raster app icons from the same geometry as frontend/img/okt-logo.svg ("OKT" in a speech bubble on a dark
tile): frontend/img/okt-256.png, okt-512.png and okt.ico (16-256 px, for a future Windows .exe / shortcut).

Why a script and not an exported file only: the icon is drawn with Pillow (already a dependency) instead of an SVG
renderer, so anyone can regenerate it after a change without extra tools. Keep the coordinates in step with the SVG
(both use a 64 x 64 grid).

    python scripts/make_icon.py
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "frontend", "img")
TILE = (0x1B, 0x1F, 0x2A, 255)
WHITE = (255, 255, 255, 255)
K = 16                                   # supersampling: draw at 64*K px, then downscale (smooth edges)


def _line(d: ImageDraw.ImageDraw, pts, w):
    """Polyline with round caps and joins (Pillow lines have square ends)."""
    pts = [(x * K, y * K) for x, y in pts]
    d.line(pts, fill=TILE, width=round(w * K), joint="curve")
    r = w * K / 2
    for x, y in pts:
        d.ellipse((x - r, y - r, x + r, y + r), fill=TILE)


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (64 * K, 64 * K), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 64 * K - 1, 64 * K - 1), radius=14 * K, fill=TILE)
    # bubble: a rounded body + the tail at the bottom left
    d.rounded_rectangle((6 * K, 8 * K, 58 * K, 43 * K), radius=17.5 * K, fill=WHITE)
    d.polygon([(19 * K, 40 * K), (16 * K, 53 * K), (29 * K, 42.5 * K)], fill=WHITE)
    # O
    w = 3.6
    d.ellipse(((17.5 - 5 - w / 2) * K, (25.5 - 7 - w / 2) * K, (17.5 + 5 + w / 2) * K, (25.5 + 7 + w / 2) * K), fill=TILE)
    d.ellipse(((17.5 - 5 + w / 2) * K, (25.5 - 7 + w / 2) * K, (17.5 + 5 - w / 2) * K, (25.5 + 7 - w / 2) * K), fill=WHITE)
    # K
    _line(d, [(28, 18.5), (28, 32.5)], w)
    _line(d, [(36, 18.5), (28.8, 25.5), (36, 32.5)], w)
    # T
    _line(d, [(40, 18.5), (51, 18.5)], w)
    _line(d, [(45.5, 18.5), (45.5, 32.5)], w)
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    big = draw(512)
    big.save(os.path.join(OUT, "okt-512.png"))
    draw(256).save(os.path.join(OUT, "okt-256.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    # each size drawn on its own (not one image scaled by the ICO writer): sharper strokes at 16 / 24 px
    frames = [draw(s) for s in sizes]
    frames[-1].save(os.path.join(OUT, "okt.ico"), sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print(f"written: {OUT}\\okt-512.png, okt-256.png, okt.ico")


if __name__ == "__main__":
    main()
