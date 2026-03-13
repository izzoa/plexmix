#!/usr/bin/env python3
"""Generate composite screenshot images with browser chrome and gradient backgrounds."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SCREENSHOTS_DIR = Path("docs/screenshots")
OUTPUT_DIR = SCREENSHOTS_DIR

# Browser chrome config
TITLEBAR_HEIGHT = 40
DOT_RADIUS = 7
DOT_GAP = 10
DOT_Y_OFFSET = TITLEBAR_HEIGHT // 2
DOT_X_START = 20
DOT_COLORS = [(255, 95, 87), (255, 189, 46), (40, 200, 64)]

# URL bar config
URL_BAR_HEIGHT = 22
URL_BAR_RADIUS = 6
URL_BAR_COLOR_DARK = (30, 30, 30)
URL_BAR_COLOR_LIGHT = (230, 230, 230)
URL_TEXT_COLOR_DARK = (140, 140, 140)
URL_TEXT_COLOR_LIGHT = (120, 120, 120)

# Frame config
CORNER_RADIUS = 14
SHADOW_SIZE = 30
PADDING = 60  # space around browser on gradient
BORDER_WIDTH = 1

# Gradient configs per screenshot
GRADIENTS = {
    "dashboard-light": {
        "colors": [(245, 230, 255), (210, 230, 255)],  # soft lavender → sky blue
        "titlebar": (228, 228, 228),
        "titlebar_bottom": (210, 210, 210),
        "border": (192, 192, 192),
        "url_bar": URL_BAR_COLOR_LIGHT,
        "url_text_color": URL_TEXT_COLOR_LIGHT,
        "url": "localhost:3000/dashboard",
        "dark": False,
    },
    "dashboard-dark": {
        "colors": [(30, 20, 50), (20, 40, 70)],  # deep purple → navy
        "titlebar": (58, 58, 58),
        "titlebar_bottom": (42, 42, 42),
        "border": (68, 68, 68),
        "url_bar": URL_BAR_COLOR_DARK,
        "url_text_color": URL_TEXT_COLOR_DARK,
        "url": "localhost:3000/dashboard",
        "dark": True,
    },
    "generator": {
        "colors": [(20, 30, 55), (45, 20, 55)],  # dark blue → purple
        "titlebar": (58, 58, 58),
        "titlebar_bottom": (42, 42, 42),
        "border": (68, 68, 68),
        "url_bar": URL_BAR_COLOR_DARK,
        "url_text_color": URL_TEXT_COLOR_DARK,
        "url": "localhost:3000/generator",
        "dark": True,
    },
    "library": {
        "colors": [(20, 40, 40), (15, 30, 50)],  # dark teal → navy
        "titlebar": (58, 58, 58),
        "titlebar_bottom": (42, 42, 42),
        "border": (68, 68, 68),
        "url_bar": URL_BAR_COLOR_DARK,
        "url_text_color": URL_TEXT_COLOR_DARK,
        "url": "localhost:3000/library",
        "dark": True,
    },
    "settings": {
        "colors": [(35, 25, 50), (20, 35, 55)],  # plum → slate blue
        "titlebar": (58, 58, 58),
        "titlebar_bottom": (42, 42, 42),
        "border": (68, 68, 68),
        "url_bar": URL_BAR_COLOR_DARK,
        "url_text_color": URL_TEXT_COLOR_DARK,
        "url": "localhost:3000/settings",
        "dark": True,
    },
}


def make_gradient(width: int, height: int, color_top: tuple, color_bottom: tuple) -> Image.Image:
    """Create a vertical linear gradient."""
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return img


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners using an alpha mask."""
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius, fill=255)
    result = img.copy()
    result.putalpha(mask)
    return result


def draw_shadow(canvas: Image.Image, box: tuple, radius: int, shadow_size: int) -> Image.Image:
    """Draw a soft drop shadow behind the browser frame."""
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    sx, sy, sw, sh = box
    draw.rounded_rectangle(
        [sx + 2, sy + 4, sx + sw - 2, sy + sh + shadow_size // 2],
        radius,
        fill=(0, 0, 0, 80),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_size))
    # Composite shadow under canvas
    result = Image.alpha_composite(shadow, canvas.convert("RGBA"))
    return result


def get_font(size: int):
    """Try to load a system font, fall back to default."""
    font_paths = [
        "/System/Library/Fonts/SFCompact.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in font_paths:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def create_hero(name: str, config: dict) -> None:
    """Create a hero screenshot composite for the given name."""
    src_path = SCREENSHOTS_DIR / f"{name}.png"
    if not src_path.exists():
        print(f"  SKIP {name} (source not found)")
        return

    screenshot = Image.open(src_path).convert("RGBA")
    sw, sh = screenshot.size

    # Browser frame dimensions
    frame_w = sw
    frame_h = sh + TITLEBAR_HEIGHT

    # Canvas dimensions (gradient background)
    canvas_w = frame_w + PADDING * 2
    canvas_h = frame_h + PADDING * 2

    # Draw gradient background
    bg = make_gradient(canvas_w, canvas_h, config["colors"][0], config["colors"][1])
    canvas = bg.convert("RGBA")

    # Build browser frame
    frame = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    frame_draw = ImageDraw.Draw(frame)

    # Titlebar background
    frame_draw.rounded_rectangle(
        [0, 0, frame_w - 1, TITLEBAR_HEIGHT + CORNER_RADIUS],
        radius=CORNER_RADIUS,
        fill=config["titlebar"],
    )
    # Overwrite the bottom portion that shouldn't be rounded
    frame_draw.rectangle(
        [0, CORNER_RADIUS, frame_w - 1, TITLEBAR_HEIGHT],
        fill=config["titlebar"],
    )

    # Traffic light dots
    for i, color in enumerate(DOT_COLORS):
        cx = DOT_X_START + i * (DOT_RADIUS * 2 + DOT_GAP)
        cy = DOT_Y_OFFSET
        frame_draw.ellipse(
            [cx - DOT_RADIUS, cy - DOT_RADIUS, cx + DOT_RADIUS, cy + DOT_RADIUS],
            fill=color,
        )

    # URL bar
    url_bar_x = DOT_X_START + 3 * (DOT_RADIUS * 2 + DOT_GAP) + 20
    url_bar_y = (TITLEBAR_HEIGHT - URL_BAR_HEIGHT) // 2
    url_bar_w = frame_w - url_bar_x - 20
    frame_draw.rounded_rectangle(
        [url_bar_x, url_bar_y, url_bar_x + url_bar_w, url_bar_y + URL_BAR_HEIGHT],
        radius=URL_BAR_RADIUS,
        fill=config["url_bar"],
    )

    # URL text
    font = get_font(12)
    url_text = f"  {config['url']}"
    bbox = font.getbbox(url_text)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = url_bar_x + (url_bar_w - text_w) // 2
    text_y = url_bar_y + (URL_BAR_HEIGHT - text_h) // 2 - 1
    frame_draw.text((text_x, text_y), url_text, fill=config["url_text_color"], font=font)

    # Paste screenshot below titlebar
    frame.paste(screenshot, (0, TITLEBAR_HEIGHT))

    # Bottom rounded corners for the entire frame
    # Create a mask for the full frame with rounded bottom corners
    mask = Image.new("L", (frame_w, frame_h), 255)
    mask_draw = ImageDraw.Draw(mask)
    # Draw rounded rect for the full frame
    full_mask = Image.new("L", (frame_w, frame_h), 0)
    full_mask_draw = ImageDraw.Draw(full_mask)
    full_mask_draw.rounded_rectangle(
        [0, 0, frame_w - 1, frame_h - 1], radius=CORNER_RADIUS, fill=255
    )
    frame.putalpha(full_mask)

    # Border
    border_frame = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_frame)
    border_draw.rounded_rectangle(
        [0, 0, frame_w - 1, frame_h - 1],
        radius=CORNER_RADIUS,
        outline=config["border"],
        width=BORDER_WIDTH,
    )
    frame = Image.alpha_composite(frame, border_frame)

    # Add shadow to canvas
    canvas = draw_shadow(canvas, (PADDING, PADDING, frame_w, frame_h), CORNER_RADIUS, SHADOW_SIZE)

    # Paste frame onto canvas
    canvas.paste(frame, (PADDING, PADDING), frame)

    # Save as RGB PNG
    out_path = OUTPUT_DIR / f"hero-{name}.png"
    canvas_rgb = canvas.convert("RGB")
    canvas_rgb.save(out_path, "PNG", optimize=True)
    print(f"  OK {out_path} ({canvas_w}x{canvas_h})")


def main():
    print("Generating hero screenshots...")
    for name, config in GRADIENTS.items():
        create_hero(name, config)
    print("Done!")


if __name__ == "__main__":
    main()
