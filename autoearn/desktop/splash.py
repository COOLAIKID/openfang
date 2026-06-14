"""
Animated splash screen shown on startup while the backend loads.
Uses PyQt6 QSplashScreen with a progress bar and status messages.
"""

from __future__ import annotations

import time
from typing import Callable


def _try_import():
    try:
        from PyQt6.QtWidgets import QSplashScreen, QLabel, QProgressBar, QVBoxLayout, QWidget
        from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QPen
        from PyQt6.QtCore import Qt, QTimer, QByteArray
        from PyQt6.QtSvg import QSvgRenderer
        return True
    except ImportError:
        return False


_qt_available = _try_import()

STARTUP_STEPS = [
    (5,  "Initializing database..."),
    (12, "Loading configuration..."),
    (20, "Starting AI client..."),
    (30, "Loading tool registry..."),
    (40, "Connecting to message bus..."),
    (50, "Starting agent manager..."),
    (60, "Loading council agents..."),
    (70, "Loading team agents..."),
    (78, "Starting QC agents..."),
    (85, "Initializing scheduler..."),
    (90, "Starting web dashboard..."),
    (95, "Final checks..."),
    (100, "Ready!"),
]


class SplashScreen:
    """
    Manages the application splash screen shown during startup.

    Usage::

        splash = SplashScreen()
        splash.show()
        splash.set_progress(25, "Loading agents...")
        splash.finish(main_window)
    """

    def __init__(self):
        self._splash = None
        self._progress = None
        self._status_label = None
        self._current_progress = 0
        self._step_index = 0
        self._timer = None
        self._on_complete: Callable | None = None

    def show(self) -> bool:
        """Show the splash screen. Returns False if Qt unavailable."""
        if not _qt_available:
            return False

        from PyQt6.QtWidgets import QSplashScreen
        from PyQt6.QtGui import QPixmap, QImage, QPainter
        from PyQt6.QtCore import Qt, QByteArray
        from PyQt6.QtSvg import QSvgRenderer

        from .icon_generator import get_splash_svg

        svg_data = QByteArray(get_splash_svg().encode())
        renderer = QSvgRenderer(svg_data)

        img = QImage(600, 300, QImage.Format.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        renderer.render(painter)
        painter.end()

        pixmap = QPixmap.fromImage(img)
        self._splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
        self._splash.setWindowTitle("AutoEarn — Starting...")

        self._splash.showMessage(
            "Initializing...",
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        )
        self._splash.show()

        return True

    def set_progress(self, percent: int, message: str = "") -> None:
        """Update progress percentage and status message."""
        self._current_progress = min(100, max(0, percent))
        if self._splash:
            from PyQt6.QtCore import Qt
            from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QFont
            from PyQt6.QtSvg import QSvgRenderer
            from PyQt6.QtCore import QByteArray

            from .icon_generator import get_splash_svg

            svg_data = QByteArray(get_splash_svg().encode())
            renderer = QSvgRenderer(svg_data)
            img = QImage(600, 300, QImage.Format.Format_ARGB32)
            img.fill(0)
            painter = QPainter(img)
            renderer.render(painter)

            # Draw progress bar
            bar_width = int(600 * self._current_progress / 100)
            painter.setBrush(QColor("#58a6ff"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(0, 286, bar_width, 4, 2, 2)

            # Draw percentage text
            painter.setPen(QColor("#8b949e"))
            font = QFont("Arial", 11)
            painter.setFont(font)
            painter.drawText(0, 270, 600, 20, Qt.AlignmentFlag.AlignCenter,
                             f"{self._current_progress}%  {message}")
            painter.end()

            pixmap = QPixmap.fromImage(img)
            self._splash.setPixmap(pixmap)
            self._splash.showMessage(
                message,
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            )

    def animate_startup(self, on_complete: Callable | None = None) -> None:
        """
        Animate through all startup steps with timed delays.
        Calls on_complete() when all steps finish.
        """
        if not _qt_available:
            if on_complete:
                on_complete()
            return

        self._on_complete = on_complete
        self._step_index = 0
        self._advance_step()

    def _advance_step(self) -> None:
        """Internal: advance to the next startup step."""
        if not _qt_available:
            return
        from PyQt6.QtCore import QTimer

        if self._step_index >= len(STARTUP_STEPS):
            if self._on_complete:
                self._on_complete()
            return

        pct, msg = STARTUP_STEPS[self._step_index]
        self.set_progress(pct, msg)
        self._step_index += 1

        delay = 80 if pct < 90 else 150
        QTimer.singleShot(delay, self._advance_step)

    def finish(self, main_window=None) -> None:
        """Hide the splash and reveal the main window."""
        if self._splash:
            if main_window:
                self._splash.finish(main_window)
            else:
                self._splash.close()
            self._splash = None

    def close(self) -> None:
        """Close the splash screen immediately."""
        if self._splash:
            self._splash.close()
            self._splash = None

    @property
    def is_visible(self) -> bool:
        return self._splash is not None and self._splash.isVisible()


class ConsoleSplash:
    """
    Fallback splash for terminal (no Qt). Prints startup progress to stdout.
    """

    LOGO = r"""
  ╔═══════════════════════════════════════════════════╗
  ║                                                   ║
  ║    $$$$  AutoEarn v1.0  $$$$                      ║
  ║    Autonomous AI Money-Making Organization        ║
  ║                                                   ║
  ╚═══════════════════════════════════════════════════╝
"""

    def __init__(self):
        self._step_index = 0

    def show(self) -> bool:
        print(self.LOGO)
        return True

    def set_progress(self, percent: int, message: str = "") -> None:
        filled = int(percent / 5)
        bar = "█" * filled + "░" * (20 - filled)
        print(f"\r  [{bar}] {percent:3d}%  {message:<40}", end="", flush=True)
        if percent == 100:
            print()

    def animate_startup(self, on_complete: Callable | None = None) -> None:
        for pct, msg in STARTUP_STEPS:
            self.set_progress(pct, msg)
            time.sleep(0.05)
        if on_complete:
            on_complete()

    def finish(self, main_window=None) -> None:
        pass

    def close(self) -> None:
        pass


def make_splash(use_gui: bool = True) -> "SplashScreen | ConsoleSplash":
    """Factory: return Qt splash if available and requested, else console splash."""
    if use_gui and _qt_available:
        return SplashScreen()
    return ConsoleSplash()
