"""
AutoEarn Desktop Application entry point.

Starts the Qt application, shows a splash screen, initialises the AutoEarn
backend, then shows the main window with a system-tray icon.

Usage::

    python -m autoearn.desktop.app          # GUI mode
    python -m autoearn.desktop.app --no-gui # headless (tray only)
    python -m autoearn.desktop.app --cli    # console splash, no GUI
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AutoEarn — Autonomous AI Money-Making Organization"
    )
    p.add_argument("--no-gui",    action="store_true", help="Run headless (tray only)")
    p.add_argument("--cli",       action="store_true", help="Console mode (no Qt)")
    p.add_argument("--no-tray",   action="store_true", help="Disable system tray")
    p.add_argument("--no-splash", action="store_true", help="Skip splash screen")
    p.add_argument("--theme",     default="dark", choices=["dark", "light"])
    p.add_argument("--port",      type=int, default=4200, help="Dashboard port")
    p.add_argument("--debug",     action="store_true", help="Enable debug logging")
    return p.parse_args()


def _start_backend(port: int = 4200, debug: bool = False) -> threading.Thread:
    """Start the FastAPI dashboard in a background daemon thread."""
    import uvicorn
    from autoearn.dashboard.app import app as fastapi_app

    def _run() -> None:
        uvicorn.run(
            fastapi_app,
            host="127.0.0.1",
            port=port,
            log_level="debug" if debug else "warning",
        )

    t = threading.Thread(target=_run, daemon=True, name="autoearn-dashboard")
    t.start()
    return t


def _start_scheduler() -> threading.Thread:
    """Start the agent scheduler in a background daemon thread."""
    def _run() -> None:
        try:
            from autoearn.core.scheduler import start_scheduler
            start_scheduler()
        except Exception as exc:
            print(f"[scheduler] Error: {exc}", file=sys.stderr)

    t = threading.Thread(target=_run, daemon=True, name="autoearn-scheduler")
    t.start()
    return t


def _setup_signal_handlers(app=None) -> None:
    """Ensure Ctrl-C cleanly quits the Qt app."""
    def _handler(signum, frame):
        print("\nInterrupt received — shutting down AutoEarn...", flush=True)
        if app:
            app.quit()
        else:
            sys.exit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def run_gui(args: argparse.Namespace) -> int:
    """Launch the full Qt GUI."""
    try:
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
        from PyQt6.QtCore import Qt
    except ImportError:
        print(
            "PyQt6 is not installed. Install it with:\n"
            "  pip install PyQt6 PyQt6-Qt6 PyQt6-sip PyQt6-WebEngine",
            file=sys.stderr,
        )
        return 1

    from .theme import build_stylesheet, set_theme
    from .splash import make_splash
    from .tray_icon import TrayIconManager
    from .main_window import MainWindow
    from .icon_generator import make_qicon, write_all_assets

    # Write icon assets if not present
    assets_dir = ROOT / "desktop" / "assets"
    if not (assets_dir / "icon.svg").exists():
        write_all_assets(assets_dir)

    app = QApplication(sys.argv)
    app.setApplicationName("AutoEarn")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("AutoEarn")
    app.setQuitOnLastWindowClosed(False)  # keep alive in tray

    # Apply theme
    set_theme(args.theme)
    app.setStyleSheet(build_stylesheet())

    # Set app icon
    icon = make_qicon()
    if icon:
        app.setWindowIcon(icon)

    # Splash screen
    splash = None
    if not args.no_splash:
        splash = make_splash(use_gui=True)
        splash.show()

    # System tray
    tray = None
    if not args.no_tray and QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayIconManager(app)
        if tray.setup():
            tray.show()
            tray.update_status("loading")

    # Create main window (hidden initially)
    window = MainWindow(tray=tray)
    if tray:
        tray._window = window

    def _on_backend_ready() -> None:
        """Called after the splash finishes animating."""
        if splash:
            splash.finish(window)
        if not args.no_gui:
            window.show()
        if tray:
            tray.update_status("running")
            tray.notify(
                "AutoEarn Started",
                "Agents are running. Revenue tracking is active.",
                icon_type="success",
            )

    _setup_signal_handlers(app)

    # Start backend threads
    _start_backend(args.port, args.debug)
    _start_scheduler()

    # Animate splash then show window
    if splash and not args.no_splash:
        splash.animate_startup(on_complete=_on_backend_ready)
    else:
        _on_backend_ready()

    return app.exec()


def run_cli(args: argparse.Namespace) -> int:
    """Console mode — splash + backend only, no Qt window."""
    from .splash import ConsoleSplash

    splash = ConsoleSplash()
    splash.show()

    _start_backend(args.port, args.debug)

    def _on_done() -> None:
        print("\n  AutoEarn is running!")
        print(f"  Dashboard: http://localhost:{args.port}")
        print("  Press Ctrl+C to stop.\n")

    splash.animate_startup(_on_done)
    _start_scheduler()

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)

    return 0


def main() -> int:
    """Main entry point dispatching to GUI or CLI mode."""
    args = _parse_args()

    if args.debug:
        os.environ["AUTOEARN_DEBUG"] = "1"

    if args.cli or (not args.no_gui and "DISPLAY" not in os.environ
                    and sys.platform != "darwin" and sys.platform != "win32"):
        return run_cli(args)

    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
