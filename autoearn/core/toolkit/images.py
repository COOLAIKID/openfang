"""Image generation and processing toolkit.

Covers DALL-E 3, Stability AI, Unsplash/picsum, and Pillow-based manipulation.
Pillow (PIL) is optional; every function falls back gracefully when unavailable.
Config is read from [openai], [stability], and [unsplash] sections of config.toml.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import time
from pathlib import Path

import requests

from ..config import get, section

# ---------------------------------------------------------------------------
# Output directory — created lazily in each function
# ---------------------------------------------------------------------------

OUTPUT: Path = Path(__file__).resolve().parent.parent.parent / "output" / "images"

# ---------------------------------------------------------------------------
# Optional Pillow import
# ---------------------------------------------------------------------------

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_output() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)


def _timestamp() -> str:
    return str(int(time.time() * 1000))


def _openai_key() -> str:
    return get("openai", "api_key", "") or os.environ.get("OPENAI_API_KEY", "")


def _stability_key() -> str:
    return get("stability", "api_key", "") or os.environ.get("STABILITY_API_KEY", "")


def _unsplash_key() -> str:
    key = get("unsplash", "api_key", "") or os.environ.get("UNSPLASH_ACCESS_KEY", "")
    return key


def _pil_open(path: str) -> "Image.Image":
    """Open an image with PIL; raise ImportError if not available."""
    if not _PIL_AVAILABLE:
        raise ImportError("Pillow is not installed. Run: pip install Pillow")
    return Image.open(path)


def _safe_font(size: int) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """Return a PIL font, falling back to the default if no truetype fonts are found."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Convert a named color or hex string to an (R, G, B) tuple."""
    named: dict[str, tuple[int, int, int]] = {
        "white": (255, 255, 255),
        "black": (0, 0, 0),
        "red": (220, 38, 38),
        "green": (22, 163, 74),
        "blue": (37, 99, 235),
        "gray": (107, 114, 128),
        "grey": (107, 114, 128),
        "yellow": (234, 179, 8),
        "orange": (249, 115, 22),
        "purple": (147, 51, 234),
        "pink": (236, 72, 153),
    }
    lower = color.strip().lower()
    if lower in named:
        return named[lower]
    h = lower.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return (255, 255, 255)


def _wrap_text(
    draw: "ImageDraw.ImageDraw",
    text: str,
    font: "ImageFont.FreeTypeFont | ImageFont.ImageFont",
    max_width: int,
) -> list[str]:
    """Word-wrap text so each line fits within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        try:
            bbox = draw.textbbox((0, 0), candidate, font=font)
            tw = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = draw.textsize(candidate, font=font)  # type: ignore[attr-defined]
        if tw <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def _textsize(
    draw: "ImageDraw.ImageDraw",
    text: str,
    font: "ImageFont.FreeTypeFont | ImageFont.ImageFont",
) -> tuple[int, int]:
    """Return (width, height) of text, compatible across Pillow versions."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return draw.textsize(text, font=font)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 1. generate_dalle
# ---------------------------------------------------------------------------


def generate_dalle(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "standard",
    model: str = "dall-e-3",
) -> str:
    """
    Generate an image via OpenAI DALL-E API.

    Downloads the returned image and saves it under OUTPUT/.
    Returns the saved file path or an ERROR string.
    """
    _ensure_output()
    api_key = _openai_key()
    if not api_key:
        return "ERROR: [openai] api_key not configured"

    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "response_format": "url",
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            return f"ERROR: OpenAI API {resp.status_code}: {resp.text[:300]}"

        data = resp.json()
        image_url = data["data"][0]["url"]

        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200:
            return f"ERROR: Failed to download generated image: {img_resp.status_code}"

        out_path = OUTPUT / f"dalle_{_timestamp()}.png"
        out_path.write_bytes(img_resp.content)
        return str(out_path)

    except Exception as exc:
        return f"ERROR: generate_dalle: {exc}"


# ---------------------------------------------------------------------------
# 2. generate_stability
# ---------------------------------------------------------------------------


def generate_stability(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 30,
) -> str:
    """
    Generate an image via Stability AI (stable-diffusion-xl-1024-v1-0).

    Returns the saved file path or an ERROR string.
    """
    _ensure_output()
    api_key = _stability_key()
    if not api_key:
        return "ERROR: [stability] api_key not configured"

    url = (
        "https://api.stability.ai/v1/generation/"
        "stable-diffusion-xl-1024-v1-0/text-to-image"
    )
    payload = {
        "text_prompts": [{"text": prompt, "weight": 1.0}],
        "cfg_scale": 7,
        "width": width,
        "height": height,
        "steps": steps,
        "samples": 1,
    }
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=120,
        )
        if resp.status_code != 200:
            return f"ERROR: Stability AI {resp.status_code}: {resp.text[:300]}"

        data = resp.json()
        artifacts = data.get("artifacts", [])
        if not artifacts:
            return "ERROR: No artifacts returned by Stability AI"

        img_bytes = base64.b64decode(artifacts[0]["base64"])
        out_path = OUTPUT / f"stability_{_timestamp()}.png"
        out_path.write_bytes(img_bytes)
        return str(out_path)

    except Exception as exc:
        return f"ERROR: generate_stability: {exc}"


# ---------------------------------------------------------------------------
# 3. fetch_unsplash
# ---------------------------------------------------------------------------


def fetch_unsplash(query: str, count: int = 3) -> str:
    """
    Fetch stock photos from Unsplash for a search query.

    Falls back to picsum.photos placeholder images when no API key is configured.
    Returns a JSON list of saved file paths.
    """
    _ensure_output()
    api_key = _unsplash_key()
    saved_paths: list[str] = []

    if api_key:
        try:
            resp = requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": min(count, 30), "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {api_key}"},
                timeout=20,
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for i, photo in enumerate(results[:count]):
                    img_url = photo.get("urls", {}).get("regular", "")
                    if not img_url:
                        continue
                    img_resp = requests.get(img_url, timeout=30)
                    if img_resp.status_code == 200:
                        out_path = OUTPUT / f"unsplash_{_timestamp()}_{i}.jpg"
                        out_path.write_bytes(img_resp.content)
                        saved_paths.append(str(out_path))
                if saved_paths:
                    return json.dumps(saved_paths)
        except Exception:
            pass  # Fall through to picsum fallback

    # Picsum fallback — no API key required
    for i in range(count):
        try:
            seed = abs(hash(query + str(i))) % 10000
            img_resp = requests.get(
                f"https://picsum.photos/seed/{seed}/1200/630",
                timeout=20,
                allow_redirects=True,
            )
            if img_resp.status_code == 200:
                out_path = OUTPUT / f"picsum_{_timestamp()}_{i}.jpg"
                out_path.write_bytes(img_resp.content)
                saved_paths.append(str(out_path))
        except Exception as exc:
            saved_paths.append(f"ERROR: {exc}")

    return json.dumps(saved_paths)


# ---------------------------------------------------------------------------
# 4. resize_image
# ---------------------------------------------------------------------------


def resize_image(path: str, width: int, height: int, output_path: str = "") -> str:
    """
    Resize an image to exact (width, height) dimensions using PIL LANCZOS resampling.

    Returns the new file path or an ERROR string.
    """
    _ensure_output()
    try:
        img = _pil_open(path)
        resized = img.resize((width, height), Image.LANCZOS)
        if output_path:
            out = Path(output_path)
        else:
            p = Path(path)
            out = OUTPUT / f"{p.stem}_resized_{width}x{height}{p.suffix}"
        out.parent.mkdir(parents=True, exist_ok=True)
        resized.save(str(out))
        return str(out)
    except ImportError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: resize_image: {exc}"


# ---------------------------------------------------------------------------
# 5. create_thumbnail
# ---------------------------------------------------------------------------


def create_thumbnail(path: str, max_size: int = 300) -> str:
    """
    Create a thumbnail that fits within max_size x max_size, preserving aspect ratio.

    Returns the new file path (with _thumb suffix) or an ERROR string.
    """
    _ensure_output()
    try:
        img = _pil_open(path)
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        p = Path(path)
        out = OUTPUT / f"{p.stem}_thumb{p.suffix}"
        img.save(str(out))
        return str(out)
    except ImportError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: create_thumbnail: {exc}"


# ---------------------------------------------------------------------------
# 6. add_text_overlay
# ---------------------------------------------------------------------------


def add_text_overlay(
    path: str,
    text: str,
    position: str = "bottom",
    font_size: int = 36,
    color: str = "white",
) -> str:
    """
    Draw a text overlay onto an image with a semi-transparent background strip.

    position: 'top' | 'center' | 'bottom'
    Returns new path with _text suffix or an ERROR string.
    """
    _ensure_output()
    try:
        img = _pil_open(path).convert("RGBA")
        draw = ImageDraw.Draw(img)
        font = _safe_font(font_size)
        iw, ih = img.size

        text_w, text_h = _textsize(draw, text, font)
        x = (iw - text_w) // 2
        margin = 20

        pos_lower = position.lower()
        if pos_lower == "top":
            y = margin
        elif pos_lower == "center":
            y = (ih - text_h) // 2
        else:  # bottom
            y = ih - text_h - margin

        # Semi-transparent dark strip behind text
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        strip_draw = ImageDraw.Draw(overlay)
        strip_draw.rectangle(
            [0, max(0, y - 10), iw, min(ih, y + text_h + 10)],
            fill=(0, 0, 0, 140),
        )
        img = Image.alpha_composite(img, overlay)
        final_draw = ImageDraw.Draw(img)

        rgb = _hex_to_rgb(color)
        final_draw.text((x, y), text, font=font, fill=rgb + (255,))

        p = Path(path)
        out = OUTPUT / f"{p.stem}_text{p.suffix}"
        img.convert("RGB").save(str(out))
        return str(out)
    except ImportError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: add_text_overlay: {exc}"


# ---------------------------------------------------------------------------
# 7. convert_format
# ---------------------------------------------------------------------------


def convert_format(path: str, to_format: str = "webp") -> str:
    """
    Convert an image to a different format (e.g., png → webp, jpg → png).

    Returns the new file path or an ERROR string.
    """
    _ensure_output()
    try:
        img = _pil_open(path)
        fmt = to_format.lower().lstrip(".")
        pil_fmt_map = {
            "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
            "webp": "WEBP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF",
        }
        pil_fmt = pil_fmt_map.get(fmt, fmt.upper())
        ext = "jpg" if fmt in ("jpeg", "jpg") else fmt

        if pil_fmt in ("JPEG", "WEBP") and img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = bg
        elif img.mode == "P":
            img = img.convert("RGB")

        p = Path(path)
        out = OUTPUT / f"{p.stem}.{ext}"
        img.save(str(out), format=pil_fmt)
        return str(out)
    except ImportError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: convert_format: {exc}"


# ---------------------------------------------------------------------------
# 8. compress_image
# ---------------------------------------------------------------------------


def compress_image(path: str, quality: int = 85) -> str:
    """
    Re-save a JPEG or WebP at lower quality to reduce file size.

    Returns JSON {new_path, original_bytes, new_bytes, reduction_pct}.
    """
    _ensure_output()
    try:
        original_bytes = Path(path).stat().st_size
        img = _pil_open(path)

        pil_fmt = "JPEG"
        ext = "jpg"
        src_fmt = (img.format or "").upper()
        if src_fmt == "WEBP":
            pil_fmt, ext = "WEBP", "webp"

        if img.mode in ("RGBA", "P", "LA") and pil_fmt == "JPEG":
            img = img.convert("RGB")
        elif img.mode == "P":
            img = img.convert("RGB")

        p = Path(path)
        out = OUTPUT / f"{p.stem}_compressed.{ext}"
        img.save(str(out), format=pil_fmt, quality=quality, optimize=True)
        new_bytes = out.stat().st_size
        reduction = round((1 - new_bytes / max(original_bytes, 1)) * 100, 2)

        return json.dumps({
            "new_path": str(out),
            "original_bytes": original_bytes,
            "new_bytes": new_bytes,
            "reduction_pct": reduction,
        })
    except ImportError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: compress_image: {exc}"


# ---------------------------------------------------------------------------
# 9. get_image_info
# ---------------------------------------------------------------------------


def get_image_info(path: str) -> str:
    """
    Return metadata about an image file as JSON.

    Fields: width, height, format, size_bytes, mode, aspect_ratio.
    """
    try:
        p = Path(path)
        if not p.exists():
            return f"ERROR: File not found: {path}"
        size_bytes = p.stat().st_size

        if _PIL_AVAILABLE:
            img = Image.open(path)
            w, h = img.size
            fmt = img.format or p.suffix.lstrip(".").upper()
            mode = img.mode
        else:
            w, h, fmt, mode = 0, 0, p.suffix.lstrip(".").upper(), "unknown"
            # Minimal header parsing without PIL
            raw = p.read_bytes()
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                import struct
                w, h = struct.unpack(">II", raw[16:24])
                fmt, mode = "PNG", "RGB"
            elif raw[:2] == b"\xff\xd8":
                fmt, mode = "JPEG", "RGB"

        gcd = math.gcd(w, h) if w and h else 1
        aspect = f"{w // gcd}:{h // gcd}" if gcd else f"{w}:{h}"

        return json.dumps({
            "width": w,
            "height": h,
            "format": fmt,
            "size_bytes": size_bytes,
            "mode": mode,
            "aspect_ratio": aspect,
        })
    except Exception as exc:
        return f"ERROR: get_image_info: {exc}"


# ---------------------------------------------------------------------------
# 10. create_og_image
# ---------------------------------------------------------------------------


def create_og_image(
    title: str,
    subtitle: str = "",
    background_color: str = "#1a1d23",
    text_color: str = "white",
    width: int = 1200,
    height: int = 630,
) -> str:
    """
    Create an Open Graph image (default 1200x630) with title and optional subtitle.

    Uses PIL when available; falls back to a plain SVG text file.
    Returns the saved file path or an ERROR string.
    """
    _ensure_output()
    ts = _timestamp()

    if _PIL_AVAILABLE:
        try:
            bg = _hex_to_rgb(background_color)
            fg = _hex_to_rgb(text_color)
            img = Image.new("RGB", (width, height), bg)
            draw = ImageDraw.Draw(img)

            # Accent bar along the bottom
            accent = (37, 99, 235)
            draw.rectangle([0, height - 8, width, height], fill=accent)

            # Title — word-wrapped
            title_font = _safe_font(68)
            max_w = int(width * 0.82)
            title_lines = _wrap_text(draw, title, title_font, max_w)
            line_h = 68 + 14  # font size + leading

            sub_h = 0
            sub_font = _safe_font(36)
            if subtitle:
                sub_lines = _wrap_text(draw, subtitle, sub_font, max_w)[:2]
                sub_h = len(sub_lines) * 50 + 20
            else:
                sub_lines = []

            total_h = len(title_lines) * line_h + sub_h
            y = (height - total_h) // 2

            for line in title_lines:
                tw, _ = _textsize(draw, line, title_font)
                x = (width - tw) // 2
                # Subtle shadow
                draw.text((x + 2, y + 2), line, font=title_font, fill=(0, 0, 0, 80))
                draw.text((x, y), line, font=title_font, fill=fg)
                y += line_h

            if subtitle:
                y += 16
                muted = tuple(min(255, c + 80) for c in bg)
                for line in sub_lines:
                    tw, _ = _textsize(draw, line, sub_font)
                    x = (width - tw) // 2
                    draw.text((x, y), line, font=sub_font, fill=muted)  # type: ignore[arg-type]
                    y += 50

            out_path = OUTPUT / f"og_{ts}.png"
            img.save(str(out_path))
            return str(out_path)

        except Exception as exc:
            return f"ERROR: create_og_image: {exc}"

    # SVG fallback
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    svg = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">\n'
        f'  <rect width="{width}" height="{height}" fill="{background_color}"/>\n'
        f'  <rect y="{height - 8}" width="{width}" height="8" fill="#2563EB"/>\n'
        f'  <text x="{width // 2}" y="{height // 2 - 20}" font-family="Arial,sans-serif" '
        f'font-size="64" font-weight="bold" fill="{text_color}" '
        f'text-anchor="middle" dominant-baseline="middle">{_esc(title)}</text>\n'
    )
    if subtitle:
        svg += (
            f'  <text x="{width // 2}" y="{height // 2 + 60}" font-family="Arial,sans-serif" '
            f'font-size="34" fill="#9CA3AF" '
            f'text-anchor="middle" dominant-baseline="middle">{_esc(subtitle)}</text>\n'
        )
    svg += "</svg>\n"
    out_path = OUTPUT / f"og_{ts}.svg"
    out_path.write_text(svg, encoding="utf-8")
    return str(out_path)


# ---------------------------------------------------------------------------
# 11. image_to_base64
# ---------------------------------------------------------------------------


def image_to_base64(path: str) -> str:
    """
    Read an image file and return it as a base64 data URI.

    Returns "data:image/png;base64,..." or an ERROR string.
    """
    try:
        p = Path(path)
        if not p.exists():
            return f"ERROR: File not found: {path}"
        ext = p.suffix.lower().lstrip(".")
        mime_map = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif",
            "webp": "image/webp", "svg": "image/svg+xml", "bmp": "image/bmp",
        }
        mime = mime_map.get(ext, "image/png")
        encoded = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception as exc:
        return f"ERROR: image_to_base64: {exc}"


# ---------------------------------------------------------------------------
# 12. batch_resize
# ---------------------------------------------------------------------------


def batch_resize(paths: list[str], width: int, height: int) -> str:
    """
    Resize multiple images to the same dimensions.

    Returns a JSON list of new file paths (or ERROR strings for failed items).
    """
    results: list[str] = [resize_image(p, width, height) for p in paths]
    return json.dumps(results)


# ---------------------------------------------------------------------------
# 13. image_grid
# ---------------------------------------------------------------------------


def image_grid(paths: list[str], cols: int = 3, output_path: str = "") -> str:
    """
    Combine multiple images into a grid layout using PIL.

    Images are scaled to equal cell sizes (derived from the first image).
    Returns the output file path or an ERROR string.
    """
    _ensure_output()
    if not paths:
        return "ERROR: image_grid: no paths provided"
    try:
        if not _PIL_AVAILABLE:
            raise ImportError("Pillow is not installed. Run: pip install Pillow")

        images = [Image.open(p).convert("RGB") for p in paths]
        rows = math.ceil(len(images) / cols)
        cell_w, cell_h = images[0].size

        grid_img = Image.new("RGB", (cols * cell_w, rows * cell_h), color=(255, 255, 255))
        for idx, img in enumerate(images):
            row, col = divmod(idx, cols)
            cell = img.resize((cell_w, cell_h), Image.LANCZOS)
            grid_img.paste(cell, (col * cell_w, row * cell_h))

        out = Path(output_path) if output_path else OUTPUT / f"grid_{_timestamp()}.jpg"
        grid_img.save(str(out))
        return str(out)

    except ImportError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: image_grid: {exc}"


# ---------------------------------------------------------------------------
# 14. screenshot_to_thumbnail
# ---------------------------------------------------------------------------


def screenshot_to_thumbnail(screenshot_path: str, max_width: int = 400) -> str:
    """
    Resize a screenshot to a web-friendly thumbnail width, preserving aspect ratio.

    Returns the new file path or an ERROR string.
    """
    _ensure_output()
    try:
        img = _pil_open(screenshot_path)
        w, h = img.size
        if w > max_width:
            new_h = int(h * (max_width / w))
            img = img.resize((max_width, new_h), Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        p = Path(screenshot_path)
        out = OUTPUT / f"{p.stem}_web_thumb.jpg"
        img.save(str(out), format="JPEG", quality=88, optimize=True)
        return str(out)
    except ImportError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: screenshot_to_thumbnail: {exc}"
