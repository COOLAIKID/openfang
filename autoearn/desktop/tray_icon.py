"""
System tray icon and context menu for AutoEarn.

Provides a persistent system-tray presence so the app keeps running in the
background while agents work. Left-click shows/hides the main window; the
right-click menu gives quick access to key actions.
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main_window import MainWindow


def _try_import_qt():
    try:
        from PyQt6.QtWidgets import (
            QSystemTrayIcon, QMenu, QApplication, QAction, QMessageBox,
        )
        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QTimer, pyqtSignal, QObject
        return True, (QSystemTrayIcon, QMenu, QApplication, QAction,
                      QMessageBox, QIcon, QTimer, pyqtSignal, QObject)
    except ImportError:
        return False, None


_qt_ok, _qt = _try_import_qt()


class TrayIconManager:
    """
    Manages the system tray icon, tooltip, balloon notifications, and context menu.

    Usage::

        tray = TrayIconManager(app, main_window)
        tray.setup()
        tray.show()
    """

    def __init__(self, app=None, main_window=None):
        self._app = app
        self._window = main_window
        self._tray = None
        self._menu = None
        self._revenue_action = None
        self._status_action = None
        self._agent_count_action = None
        self._notifications_enabled = True
        self._notification_queue: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self) -> bool:
        """Initialize the tray icon. Returns False if Qt not available."""
        if not _qt_ok:
            return False

        QSystemTrayIcon, QMenu, QApplication, QAction, QMessageBox, QIcon, QTimer, _, _ = _qt

        from .icon_generator import make_tray_qicon

        icon = make_tray_qicon()
        if icon is None:
            icon = QIcon()

        self._tray = QSystemTrayIcon(icon)
        self._tray.setToolTip("AutoEarn — Autonomous AI Organization")
        self._tray.activated.connect(self._on_tray_activated)

        self._build_menu()
        self._tray.setContextMenu(self._menu)

        return True

    def _build_menu(self) -> None:
        if not _qt_ok:
            return
        QSystemTrayIcon, QMenu, QApplication, QAction, QMessageBox, QIcon, QTimer, _, _ = _qt

        self._menu = QMenu()

        # -- Header (non-clickable title) --
        title_action = QAction("🤖 AutoEarn")
        title_action.setEnabled(False)
        self._menu.addAction(title_action)
        self._menu.addSeparator()

        # -- Live stats (updated periodically) --
        self._revenue_action = QAction("💰 Revenue: $0.00")
        self._revenue_action.setEnabled(False)
        self._menu.addAction(self._revenue_action)

        self._agent_count_action = QAction("🤖 Agents: 0 active")
        self._agent_count_action.setEnabled(False)
        self._menu.addAction(self._agent_count_action)

        self._status_action = QAction("⚪ Status: Idle")
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._menu.addSeparator()

        # -- Navigation --
        show_action = QAction("📊 Show Dashboard")
        show_action.triggered.connect(self.show_window)
        self._menu.addAction(show_action)

        agents_action = QAction("🤖 Manage Agents")
        agents_action.triggered.connect(lambda: self._show_tab("agents"))
        self._menu.addAction(agents_action)

        revenue_action = QAction("💰 Revenue Report")
        revenue_action.triggered.connect(lambda: self._show_tab("revenue"))
        self._menu.addAction(revenue_action)

        self._menu.addSeparator()

        # -- Quick actions --
        web_dash_action = QAction("🌐 Open Web Dashboard")
        web_dash_action.triggered.connect(self._open_web_dashboard)
        self._menu.addAction(web_dash_action)

        notif_action = QAction("🔔 Notifications: On")
        notif_action.setCheckable(True)
        notif_action.setChecked(True)
        notif_action.triggered.connect(self._toggle_notifications)
        self._menu.addAction(notif_action)
        self._notif_action = notif_action

        self._menu.addSeparator()

        # -- Control --
        pause_action = QAction("⏸ Pause All Agents")
        pause_action.triggered.connect(self._pause_agents)
        self._menu.addAction(pause_action)

        restart_action = QAction("🔄 Restart Agents")
        restart_action.triggered.connect(self._restart_agents)
        self._menu.addAction(restart_action)

        self._menu.addSeparator()

        # -- Quit --
        quit_action = QAction("✕ Quit AutoEarn")
        quit_action.triggered.connect(self._quit_app)
        self._menu.addAction(quit_action)

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Show the tray icon."""
        if self._tray:
            self._tray.show()

    def hide(self) -> None:
        """Hide the tray icon."""
        if self._tray:
            self._tray.hide()

    def show_window(self) -> None:
        """Bring the main window to focus."""
        if self._window:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()

    def hide_window(self) -> None:
        """Hide the main window to tray."""
        if self._window:
            self._window.hide()

    # ------------------------------------------------------------------
    # Stats updates (called by a timer in the main window)
    # ------------------------------------------------------------------

    def update_revenue(self, amount: float, period: str = "today") -> None:
        """Update the revenue display in the tray menu."""
        if self._revenue_action:
            self._revenue_action.setText(f"💰 Revenue ({period}): ${amount:,.2f}")

    def update_agent_count(self, active: int, total: int) -> None:
        """Update the active agent count display."""
        if self._agent_count_action:
            self._agent_count_action.setText(f"🤖 Agents: {active}/{total} active")

    def update_status(self, status: str) -> None:
        """Update the status line in the tray menu."""
        icons = {"running": "🟢", "idle": "⚪", "paused": "🟡",
                 "error": "🔴", "loading": "🔵"}
        icon = icons.get(status.lower(), "⚪")
        if self._status_action:
            self._status_action.setText(f"{icon} Status: {status.title()}")

    def set_tooltip(self, text: str) -> None:
        """Update the tray icon tooltip."""
        if self._tray:
            self._tray.setToolTip(text)

    # ------------------------------------------------------------------
    # Balloon/notification
    # ------------------------------------------------------------------

    def notify(
        self,
        title: str,
        message: str,
        icon_type: str = "info",
        duration_ms: int = 4000,
    ) -> None:
        """Show a system tray balloon notification."""
        if not self._notifications_enabled or not _qt_ok:
            return
        if self._tray is None:
            return

        QSystemTrayIcon = _qt[0]
        icon_map = {
            "info":    QSystemTrayIcon.MessageIcon.Information,
            "warning": QSystemTrayIcon.MessageIcon.Warning,
            "error":   QSystemTrayIcon.MessageIcon.Critical,
            "success": QSystemTrayIcon.MessageIcon.Information,
        }
        msg_icon = icon_map.get(icon_type, QSystemTrayIcon.MessageIcon.Information)
        self._tray.showMessage(title, message, msg_icon, duration_ms)

    def notify_revenue(self, amount: float, source: str) -> None:
        """Convenience: show a revenue notification."""
        self.notify(
            "💰 Revenue Logged",
            f"${amount:,.2f} from {source}",
            icon_type="success",
        )

    def notify_agent_error(self, agent_name: str, error: str) -> None:
        """Convenience: show an agent error notification."""
        self.notify(
            f"⚠️ Agent Error: {agent_name}",
            error[:200],
            icon_type="error",
        )

    def notify_qc_approval(self, content_title: str) -> None:
        """Convenience: QC approved a content piece."""
        self.notify(
            "✅ Content Approved",
            f"'{content_title}' passed QC and is ready to publish.",
            icon_type="success",
        )

    def notify_directive(self, from_agent: str, directive: str) -> None:
        """Convenience: council issued a new directive."""
        self.notify(
            f"📋 New Directive from {from_agent}",
            directive[:200],
            icon_type="info",
        )

    # ------------------------------------------------------------------
    # Slots / handlers
    # ------------------------------------------------------------------

    def _on_tray_activated(self, reason) -> None:
        """Handle clicks on the tray icon."""
        if not _qt_ok:
            return
        QSystemTrayIcon = _qt[0]
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Left click — toggle window visibility
            if self._window:
                if self._window.isVisible():
                    self.hide_window()
                else:
                    self.show_window()

    def _show_tab(self, tab_name: str) -> None:
        """Show a specific tab in the main window."""
        self.show_window()
        if self._window and hasattr(self._window, "switch_tab"):
            self._window.switch_tab(tab_name)

    def _open_web_dashboard(self) -> None:
        """Open the FastAPI web dashboard in the default browser."""
        webbrowser.open("http://localhost:4200")

    def _toggle_notifications(self, checked: bool) -> None:
        """Enable or disable tray notifications."""
        self._notifications_enabled = checked
        label = "On" if checked else "Off"
        if self._notif_action:
            self._notif_action.setText(f"🔔 Notifications: {label}")

    def _pause_agents(self) -> None:
        """Pause all running agents via the agent manager."""
        try:
            from ..core import agent_manager
            if hasattr(agent_manager, "pause_all"):
                agent_manager.pause_all()
                self.update_status("paused")
                self.notify("⏸ Agents Paused", "All agents have been paused.")
        except Exception as exc:
            self.notify("Error", f"Could not pause agents: {exc}", "error")

    def _restart_agents(self) -> None:
        """Restart all agents."""
        try:
            from ..core import agent_manager
            if hasattr(agent_manager, "restart_all"):
                agent_manager.restart_all()
                self.update_status("running")
                self.notify("🔄 Agents Restarted", "All agents have been restarted.")
        except Exception as exc:
            self.notify("Error", f"Could not restart agents: {exc}", "error")

    def _quit_app(self) -> None:
        """Quit the application completely."""
        if _qt_ok:
            QApplication = _qt[2]
            app = QApplication.instance()
            if app:
                app.quit()
        else:
            sys.exit(0)
