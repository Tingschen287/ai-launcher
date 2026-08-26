#!/usr/bin/env python3
"""Build the multi-resolution Windows Terminal icon.

Small frames are drawn directly on the pixel grid so the tab icon stays sharp.
Larger frames use supersampling for smooth curves.
"""

from pathlib import Path

from PIL import Image, ImageDraw


SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
CORAL = (217, 119, 87, 255)
WHITE = (255, 255, 255, 255)


def draw_icon(size: int) -> Image.Image:
    scale = 1 if size <= 24 else 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = max(3, round(size * 0.20)) * scale
    draw.rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=radius, fill=CORAL
    )

    # A compact terminal prompt: chevron plus a solid cursor. Coordinates are
    # deliberately integral at 16/20/24 px to avoid fuzzy half-pixel edges.
    stroke = max(2, round(size * 0.12)) * scale
    x1 = round(size * 0.27) * scale
    x2 = round(size * 0.49) * scale
    y1 = round(size * 0.27) * scale
    ym = round(size * 0.50) * scale
    y2 = round(size * 0.73) * scale
    draw.line((x1, y1, x2, ym, x1, y2), fill=WHITE, width=stroke, joint="curve")

    cursor_left = round(size * 0.70) * scale
    cursor_right = round(size * 0.82) * scale
    draw.rectangle(
        (cursor_left, y1, cursor_right, y2),
        fill=WHITE,
    )

    if scale > 1:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    target = repo / "assets" / "ai-launcher.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw_icon(size) for size in SIZES]
    frames[-1].save(
        target,
        format="ICO",
        append_images=frames[:-1],
        sizes=[(size, size) for size in SIZES],
        bitmap_format="png",
    )
    print(target)


if __name__ == "__main__":
    main()
