"""
AutoEarn icon generator — creates the app icon programmatically.

Generates SVG source and can render it to QIcon (requires PyQt6 + cairosvg or
falls back to a pure-Qt painter path). Also writes PNG files at multiple
resolutions for desktop integration (.desktop file, taskbar, etc.).
"""

from __future__ import annotations

import math
from pathlib import Path

ICON_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg width="512" height="512" viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0d1117;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#161b22;stop-opacity:1" />
    </linearGradient>
    <linearGradient id="circleGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#58a6ff;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#3fb950;stop-opacity:1" />
    </linearGradient>
    <linearGradient id="arrowGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#3fb950;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#58a6ff;stop-opacity:1" />
    </linearGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="8" result="coloredBlur"/>
      <feMerge>
        <feMergeNode in="coloredBlur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>

  <!-- Background rounded rectangle -->
  <rect width="512" height="512" rx="96" ry="96" fill="url(#bgGrad)"/>

  <!-- Outer ring -->
  <circle cx="256" cy="256" r="200" fill="none"
          stroke="url(#circleGrad)" stroke-width="3" opacity="0.4"/>
  <circle cx="256" cy="256" r="180" fill="none"
          stroke="url(#circleGrad)" stroke-width="1.5" opacity="0.2"/>

  <!-- Rising bar chart (money growing) -->
  <!-- Bar 1 (shortest, leftmost) -->
  <rect x="96" y="340" width="52" height="80" rx="6"
        fill="#58a6ff" opacity="0.7"/>
  <!-- Bar 2 -->
  <rect x="164" y="300" width="52" height="120" rx="6"
        fill="#58a6ff" opacity="0.8"/>
  <!-- Bar 3 -->
  <rect x="232" y="248" width="52" height="172" rx="6"
        fill="#58a6ff" opacity="0.9"/>
  <!-- Bar 4 (tallest, rightmost) -->
  <rect x="300" y="188" width="52" height="232" rx="6"
        fill="url(#arrowGrad)" filter="url(#glow)" opacity="1"/>

  <!-- Upward trend arrow -->
  <polyline points="96,380 160,320 230,268 310,200 380,140"
            fill="none" stroke="url(#arrowGrad)"
            stroke-width="6" stroke-linecap="round" stroke-linejoin="round"
            opacity="0.9"/>

  <!-- Arrow head -->
  <polygon points="380,140 348,148 356,176"
           fill="#3fb950" opacity="0.9"/>

  <!-- Dollar sign overlay -->
  <text x="256" y="148" text-anchor="middle"
        font-family="Arial, Helvetica, sans-serif"
        font-size="72" font-weight="900"
        fill="url(#circleGrad)" opacity="0.95"
        filter="url(#glow)">$</text>

  <!-- Small dots at bar tops (data points) -->
  <circle cx="122" cy="338" r="7" fill="#58a6ff" opacity="0.9"/>
  <circle cx="190" cy="298" r="7" fill="#58a6ff" opacity="0.9"/>
  <circle cx="258" cy="246" r="7" fill="#58a6ff" opacity="0.9"/>
  <circle cx="326" cy="186" r="8" fill="#3fb950" filter="url(#glow)"/>

  <!-- AE monogram bottom-right -->
  <text x="440" y="460" text-anchor="middle"
        font-family="Arial, Helvetica, sans-serif"
        font-size="32" font-weight="700"
        fill="#8b949e" opacity="0.6">AE</text>
</svg>"""

TRAY_ICON_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg width="64" height="64" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="tg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#58a6ff"/>
      <stop offset="100%" style="stop-color:#3fb950"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="12" fill="#0d1117"/>
  <rect x="8"  y="42" width="9"  height="12" rx="2" fill="#58a6ff" opacity="0.7"/>
  <rect x="20" y="35" width="9"  height="19" rx="2" fill="#58a6ff" opacity="0.8"/>
  <rect x="32" y="26" width="9"  height="28" rx="2" fill="#58a6ff" opacity="0.9"/>
  <rect x="44" y="16" width="9"  height="38" rx="2" fill="url(#tg)"/>
  <polyline points="12,46 24,38 36,30 48,20"
            fill="none" stroke="url(#tg)" stroke-width="2.5"
            stroke-linecap="round" stroke-linejoin="round"/>
  <text x="32" y="14" text-anchor="middle"
        font-family="Arial" font-size="12" font-weight="900"
        fill="url(#tg)">$</text>
</svg>"""

SPLASH_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg width="600" height="300" viewBox="0 0 600 300" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0d1117"/>
      <stop offset="100%" style="stop-color:#161b22"/>
    </linearGradient>
    <linearGradient id="textGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#58a6ff"/>
      <stop offset="50%" style="stop-color:#3fb950"/>
      <stop offset="100%" style="stop-color:#58a6ff"/>
    </linearGradient>
  </defs>
  <rect width="600" height="300" fill="url(#bg)"/>

  <!-- Grid lines (subtle) -->
  <line x1="0" y1="50"  x2="600" y2="50"  stroke="#30363d" stroke-width="0.5"/>
  <line x1="0" y1="100" x2="600" y2="100" stroke="#30363d" stroke-width="0.5"/>
  <line x1="0" y1="150" x2="600" y2="150" stroke="#30363d" stroke-width="0.5"/>
  <line x1="0" y1="200" x2="600" y2="200" stroke="#30363d" stroke-width="0.5"/>
  <line x1="0" y1="250" x2="600" y2="250" stroke="#30363d" stroke-width="0.5"/>

  <!-- Mini chart on right -->
  <polyline points="400,220 440,180 480,190 520,140 560,120"
            fill="none" stroke="#3fb950" stroke-width="2" opacity="0.6"/>
  <polygon points="560,120 540,128 546,148" fill="#3fb950" opacity="0.6"/>

  <!-- Logo bars (left side) -->
  <rect x="40"  y="200" width="18" height="60" rx="3" fill="#58a6ff" opacity="0.6"/>
  <rect x="64"  y="175" width="18" height="85" rx="3" fill="#58a6ff" opacity="0.7"/>
  <rect x="88"  y="148" width="18" height="112" rx="3" fill="#58a6ff" opacity="0.85"/>
  <rect x="112" y="118" width="18" height="142" rx="3" fill="#3fb950" opacity="0.95"/>

  <!-- App name -->
  <text x="160" y="170" font-family="Arial, Helvetica, sans-serif"
        font-size="56" font-weight="900" fill="url(#textGrad)">AutoEarn</text>

  <!-- Tagline -->
  <text x="160" y="200" font-family="Arial, Helvetica, sans-serif"
        font-size="16" fill="#8b949e">Autonomous AI Money-Making Organization</text>

  <!-- Version and status -->
  <text x="300" y="260" text-anchor="middle"
        font-family="Arial, Helvetica, sans-serif"
        font-size="12" fill="#6e7681">v1.0.0  ·  Loading...</text>

  <!-- Bottom bar -->
  <rect x="0" y="285" width="0" height="4" rx="2" fill="url(#textGrad)"
        id="progress-bar"/>
</svg>"""


def get_icon_svg() -> str:
    """Return the main app icon as an SVG string."""
    return ICON_SVG


def get_tray_svg() -> str:
    """Return the system tray icon as a small SVG string."""
    return TRAY_ICON_SVG


def get_splash_svg() -> str:
    """Return the splash screen SVG."""
    return SPLASH_SVG


def save_svg_files(assets_dir: Path) -> None:
    """Write SVG files to the assets directory."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "icon.svg").write_text(ICON_SVG, encoding="utf-8")
    (assets_dir / "tray.svg").write_text(TRAY_ICON_SVG, encoding="utf-8")
    (assets_dir / "splash.svg").write_text(SPLASH_SVG, encoding="utf-8")


def make_qicon():
    """
    Return a QIcon built from the SVG. Requires PyQt6.
    Falls back gracefully if PyQt6 is not installed.
    """
    try:
        from PyQt6.QtGui import QIcon, QPixmap
        from PyQt6.QtCore import QByteArray
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QPainter, QImage
        from PyQt6.QtCore import Qt

        data = QByteArray(ICON_SVG.encode())
        renderer = QSvgRenderer(data)
        sizes = [16, 32, 48, 64, 128, 256, 512]
        icon = QIcon()
        for size in sizes:
            img = QImage(size, size, QImage.Format.Format_ARGB32)
            img.fill(0)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(QPixmap.fromImage(img))
        return icon
    except ImportError:
        return None


def make_tray_qicon():
    """Return a QIcon for the system tray."""
    try:
        from PyQt6.QtGui import QIcon, QPixmap, QImage, QPainter
        from PyQt6.QtCore import QByteArray
        from PyQt6.QtSvg import QSvgRenderer

        data = QByteArray(TRAY_ICON_SVG.encode())
        renderer = QSvgRenderer(data)
        icon = QIcon()
        for size in [16, 22, 32, 64]:
            img = QImage(size, size, QImage.Format.Format_ARGB32)
            img.fill(0)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(QPixmap.fromImage(img))
        return icon
    except ImportError:
        return None


def generate_favicon_ico_bytes() -> bytes:
    """
    Generate a minimal 32x32 ICO file from a pixel-art render of the tray SVG.
    Returns raw bytes — write to autoearn.ico for Windows compatibility.
    This is a pure-Python ICO encoder (no external libs needed).
    """
    width = height = 32
    # Minimal ICO header (1 icon, 32x32, 32-bit BGRA BMP)
    import struct

    def make_bmp_dib(w: int, h: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
        """Pixels is list of (R,G,B,A) top-to-bottom, left-to-right."""
        bpp = 32
        row_size = w * 4
        pixel_data_size = row_size * h
        dib_size = 40 + pixel_data_size
        dib = struct.pack(
            "<IIIHHIIIIII",
            40, w, h * 2, 1, bpp, 0, pixel_data_size, 2835, 2835, 0, 0,
        )
        rows = []
        for y in range(h - 1, -1, -1):  # ICO BMPs are bottom-up
            for x in range(w):
                r, g, b, a = pixels[y * w + x]
                rows.append(struct.pack("BBBB", b, g, r, a))
        return dib + b"".join(rows)

    # Simple pixel art: dark bg with green/blue diagonal stripes
    pixels = []
    for y in range(height):
        for x in range(width):
            # Background
            r, g, b, a = 13, 17, 23, 255
            # Rising bar chart simplified
            bar_positions = [(4, 20, 26), (10, 17, 30), (16, 13, 30), (22, 8, 30)]
            for bx, by, bh in bar_positions:
                if bx <= x < bx + 5 and by <= y < by + bh:
                    if x >= 22:
                        r, g, b = 63, 185, 80
                    else:
                        r, g, b = 88, 166, 255
            # Trend line
            trend_y = int(28 - (x / width) * 20)
            if abs(y - trend_y) <= 1:
                r, g, b = 63, 185, 80
            pixels.append((r, g, b, a))

    bmp = make_bmp_dib(width, height, pixels)
    ico_header = struct.pack("<HHH", 0, 1, 1)
    img_dir = struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(bmp), 22)
    return ico_header + img_dir + bmp


def write_all_assets(base_dir: Path | None = None) -> Path:
    """
    Write all icon assets to the assets directory.
    Returns the assets directory path.
    """
    if base_dir is None:
        base_dir = Path(__file__).parent / "assets"
    base_dir.mkdir(parents=True, exist_ok=True)

    save_svg_files(base_dir)

    ico_bytes = generate_favicon_ico_bytes()
    (base_dir / "autoearn.ico").write_bytes(ico_bytes)

    return base_dir
