"""
AutoEarn main application window.

A QMainWindow with:
- Tab bar: Dashboard | Agents | Revenue | Messages | Logs | Settings
- Persistent status bar showing last update time + active agent count
- Live revenue ticker in the title bar
- Menu bar with File / View / Agents / Help menus
- Keyboard shortcuts for all major actions
"""

from __future__ import annotations

import json
import webbrowser
from datetime import datetime
from pathlib import Path

try:
    from PyQt6.QtWidgets import (
        QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QStatusBar, QMenuBar, QMenu, QApplication,
        QSplitter, QFrame, QMessageBox, QToolBar,
    )
    from PyQt6.QtGui import QAction, QKeySequence, QFont, QIcon
    from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
    _QT_OK = True
except ImportError:
    _QT_OK = False

if _QT_OK:
    from .theme import build_stylesheet, set_theme
    from .tray_icon import TrayIconManager
    from .agent_panel import AgentPanel
    from .message_feed import MessageFeed
    from .log_viewer import LogViewer
    from .revenue_widget import RevenuePanel
    from .settings_dialog import SettingsDialog
    from .icon_generator import make_qicon

    TAB_INDICES = {
        "dashboard": 0,
        "agents":    1,
        "revenue":   2,
        "messages":  3,
        "logs":      4,
    }

    class DashboardTab(QWidget):
        """Overview tab with KPI cards and a mini org chart."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._setup_ui()
            self._refresh_timer = QTimer(self)
            self._refresh_timer.timeout.connect(self.refresh)
            self._refresh_timer.start(30_000)
            self.refresh()

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(16)

            # Heading row
            heading_row = QHBoxLayout()
            title = QLabel("🤖 AutoEarn Organization")
            title.setProperty("class", "heading")
            heading_row.addWidget(title)
            heading_row.addStretch()

            self._last_update_lbl = QLabel("Last update: —")
            self._last_update_lbl.setProperty("class", "muted")
            heading_row.addWidget(self._last_update_lbl)
            layout.addLayout(heading_row)

            # KPI row
            kpi_row = QHBoxLayout()
            kpi_row.setSpacing(12)

            self._kpi_labels: dict[str, QLabel] = {}
            kpis = [
                ("total_revenue",    "Total Revenue",    "$0.00", "#58a6ff"),
                ("active_agents",    "Active Agents",    "0",     "#3fb950"),
                ("messages_today",   "Messages Today",   "0",     "#d29922"),
                ("content_pipeline", "In Pipeline",      "0",     "#bc8cff"),
                ("leads",            "Active Leads",     "0",     "#39c5cf"),
            ]

            for key, label, default, color in kpis:
                card = self._make_kpi_card(label, default, color)
                kpi_row.addWidget(card["frame"])
                self._kpi_labels[key] = card["value_lbl"]

            layout.addLayout(kpi_row)

            # Org chart + activity split
            splitter = QSplitter(Qt.Orientation.Horizontal)

            # Org chart
            org_group = QFrame()
            org_group.setProperty("class", "card")
            org_layout = QVBoxLayout(org_group)
            org_title = QLabel("Organization Structure")
            org_title.setProperty("class", "subheading")
            org_layout.addWidget(org_title)
            self._org_chart = QLabel(self._build_org_text([]))
            self._org_chart.setFont(QFont("Courier New", 10))
            self._org_chart.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._org_chart.setWordWrap(False)
            org_layout.addWidget(self._org_chart)
            org_layout.addStretch()
            splitter.addWidget(org_group)

            # Recent activity
            activity_group = QFrame()
            activity_group.setProperty("class", "card")
            act_layout = QVBoxLayout(activity_group)
            act_title = QLabel("Recent Activity")
            act_title.setProperty("class", "subheading")
            act_layout.addWidget(act_title)
            self._activity_lbl = QLabel("Loading...")
            self._activity_lbl.setFont(QFont("Courier New", 10))
            self._activity_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
            self._activity_lbl.setWordWrap(True)
            act_layout.addWidget(self._activity_lbl)
            act_layout.addStretch()
            splitter.addWidget(activity_group)

            splitter.setSizes([400, 350])
            layout.addWidget(splitter)

        def _make_kpi_card(self, label: str, default: str, color: str) -> dict:
            frame = QFrame()
            frame.setProperty("class", "card")
            frame.setFixedHeight(90)
            vl = QVBoxLayout(frame)
            vl.setContentsMargins(12, 10, 12, 10)

            lbl = QLabel(label.upper())
            lbl.setProperty("class", "metric-label")
            vl.addWidget(lbl)

            val = QLabel(default)
            val.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {color};")
            vl.addWidget(val)

            return {"frame": frame, "value_lbl": val}

        def _build_org_text(self, agents: list[dict]) -> str:
            """Build a text-based org chart."""
            council = [a for a in agents if a.get("role") == "council"]
            teams: dict[str, list] = {}
            for a in agents:
                if a.get("role") == "team":
                    team = a.get("team", "misc")
                    teams.setdefault(team, []).append(a)

            lines = []
            lines.append("┌─────────────── COUNCIL ───────────────┐")
            if council:
                c_names = "  ".join(f"{a['name'].upper()}" for a in council)
                lines.append(f"│  {c_names:<36} │")
            else:
                lines.append("│  (no council agents loaded)           │")
            lines.append("└───────────────────────────────────────┘")
            lines.append("              │")

            if teams:
                team_list = list(teams.items())
                for team_name, members in team_list:
                    member_str = " | ".join(a["name"] for a in members[:3])
                    lines.append(f"  ┌── {team_name.upper():<12} [{member_str}]")
            else:
                lines.append("  (teams loading...)")

            return "\n".join(lines)

        def refresh(self) -> None:
            """Reload KPI data from the database."""
            try:
                from ..core.database import get_all_agents, revenue_summary
                agents = get_all_agents()
                active = sum(1 for a in agents if a.get("status") == "running")
                rev = revenue_summary()
                total_rev = sum(r.get("total", 0) for r in rev) if isinstance(rev, list) else 0

                self._kpi_labels["total_revenue"].setText(f"${total_rev:,.2f}")
                self._kpi_labels["active_agents"].setText(f"{active}/{len(agents)}")
                self._org_chart.setText(self._build_org_text(agents))
            except Exception:
                pass

            try:
                from ..core.message_bus import recent_messages
                msgs = recent_messages(limit=5)
                lines = []
                for m in msgs:
                    ts = datetime.fromtimestamp(float(m.get("timestamp", 0))).strftime("%H:%M")
                    lines.append(
                        f"[{ts}] {m.get('from_agent','?')} → {m.get('to_agent','?')}  "
                        f"{m.get('subject','')[:40]}"
                    )
                self._activity_lbl.setText("\n".join(lines) or "No recent messages.")
            except Exception:
                pass

            self._last_update_lbl.setText(
                f"Last update: {datetime.now().strftime('%H:%M:%S')}"
            )

    class MainWindow(QMainWindow):
        """
        AutoEarn main application window.

        Created once; hidden to system tray rather than closed.
        """

        closed = pyqtSignal()

        def __init__(self, tray: "TrayIconManager | None" = None):
            super().__init__()
            self._tray = tray
            self._setup_window()
            self._setup_menu()
            self._setup_toolbar()
            self._setup_tabs()
            self._setup_statusbar()
            self._setup_timers()
            self._apply_theme("dark")

        # ------------------------------------------------------------------
        # Setup
        # ------------------------------------------------------------------

        def _setup_window(self) -> None:
            self.setWindowTitle("AutoEarn — Autonomous AI Organization")
            self.setMinimumSize(960, 640)
            self.resize(1200, 750)

            icon = make_qicon()
            if icon:
                self.setWindowIcon(icon)

            central = QWidget()
            self.setCentralWidget(central)
            self._main_layout = QVBoxLayout(central)
            self._main_layout.setContentsMargins(0, 0, 0, 0)

        def _setup_menu(self) -> None:
            menubar = self.menuBar()

            # File menu
            file_menu = menubar.addMenu("&File")

            settings_act = QAction("⚙ Settings…", self)
            settings_act.setShortcut(QKeySequence("Ctrl+,"))
            settings_act.triggered.connect(self._open_settings)
            file_menu.addAction(settings_act)

            file_menu.addSeparator()

            export_act = QAction("💾 Export Data…", self)
            export_act.triggered.connect(self._export_data)
            file_menu.addAction(export_act)

            file_menu.addSeparator()

            hide_act = QAction("Hide to Tray", self)
            hide_act.setShortcut(QKeySequence("Ctrl+W"))
            hide_act.triggered.connect(self.hide)
            file_menu.addAction(hide_act)

            quit_act = QAction("✕ Quit", self)
            quit_act.setShortcut(QKeySequence("Ctrl+Q"))
            quit_act.triggered.connect(QApplication.instance().quit)
            file_menu.addAction(quit_act)

            # View menu
            view_menu = menubar.addMenu("&View")

            for name, idx in TAB_INDICES.items():
                act = QAction(name.title(), self)
                act.setShortcut(QKeySequence(f"Ctrl+{idx + 1}"))
                act.triggered.connect(lambda checked, i=idx: self._tabs.setCurrentIndex(i))
                view_menu.addAction(act)

            view_menu.addSeparator()

            web_dash = QAction("🌐 Open Web Dashboard", self)
            web_dash.triggered.connect(lambda: webbrowser.open("http://localhost:4200"))
            view_menu.addAction(web_dash)

            dark_act = QAction("🌙 Dark Theme", self)
            dark_act.triggered.connect(lambda: self._apply_theme("dark"))
            view_menu.addAction(dark_act)

            light_act = QAction("☀ Light Theme", self)
            light_act.triggered.connect(lambda: self._apply_theme("light"))
            view_menu.addAction(light_act)

            # Agents menu
            agents_menu = menubar.addMenu("&Agents")

            trigger_all = QAction("▶ Trigger All Agents", self)
            trigger_all.triggered.connect(self._trigger_all_agents)
            agents_menu.addAction(trigger_all)

            pause_all = QAction("⏸ Pause All Agents", self)
            pause_all.triggered.connect(self._pause_all_agents)
            agents_menu.addAction(pause_all)

            restart_all = QAction("🔄 Restart All Agents", self)
            restart_all.triggered.connect(self._restart_all_agents)
            agents_menu.addAction(restart_all)

            agents_menu.addSeparator()

            council_act = QAction("👑 Convene Council Now", self)
            council_act.triggered.connect(self._convene_council)
            agents_menu.addAction(council_act)

            # Help menu
            help_menu = menubar.addMenu("&Help")

            about_act = QAction("ℹ About AutoEarn", self)
            about_act.triggered.connect(self._show_about)
            help_menu.addAction(about_act)

            docs_act = QAction("📚 Documentation", self)
            docs_act.triggered.connect(lambda: webbrowser.open("https://github.com/coolaikid/openfang"))
            help_menu.addAction(docs_act)

        def _setup_toolbar(self) -> None:
            toolbar = self.addToolBar("Main")
            toolbar.setMovable(False)
            toolbar.setIconSize(QSize(16, 16))

            for label, slot in [
                ("▶ Trigger All", self._trigger_all_agents),
                ("⏸ Pause",       self._pause_all_agents),
                ("🔄 Restart",     self._restart_all_agents),
                ("👑 Convene",     self._convene_council),
            ]:
                act = QAction(label, self)
                act.triggered.connect(slot)
                toolbar.addAction(act)

            toolbar.addSeparator()

            self._revenue_badge = QLabel("  💰 $0.00  ")
            self._revenue_badge.setStyleSheet(
                "color: #3fb950; font-weight: 700; font-size: 13px; padding: 0 8px;"
            )
            toolbar.addWidget(self._revenue_badge)

            toolbar.addSeparator()

            self._agent_badge = QLabel("  🤖 0/0  ")
            self._agent_badge.setStyleSheet("color: #58a6ff; font-size: 13px; padding: 0 8px;")
            toolbar.addWidget(self._agent_badge)

        def _setup_tabs(self) -> None:
            self._tabs = QTabWidget()
            self._tabs.setTabPosition(QTabWidget.TabPosition.North)
            self._main_layout.addWidget(self._tabs)

            # Dashboard
            self._dashboard_tab = DashboardTab()
            self._tabs.addTab(self._dashboard_tab, "📊 Dashboard")

            # Agents
            self._agent_panel = AgentPanel()
            self._tabs.addTab(self._agent_panel, "🤖 Agents")

            # Revenue
            self._revenue_panel = RevenuePanel()
            self._tabs.addTab(self._revenue_panel, "💰 Revenue")

            # Messages
            self._message_feed = MessageFeed()
            self._tabs.addTab(self._message_feed, "📨 Messages")

            # Logs
            self._log_viewer = LogViewer()
            self._tabs.addTab(self._log_viewer, "📋 Logs")

        def _setup_statusbar(self) -> None:
            status = QStatusBar()
            self.setStatusBar(status)

            self._status_msg = QLabel("Ready")
            status.addWidget(self._status_msg)

            status.addPermanentWidget(QLabel("  "))  # spacer

            self._status_time = QLabel(datetime.now().strftime("%H:%M:%S"))
            status.addPermanentWidget(self._status_time)

        def _setup_timers(self) -> None:
            # Status bar clock
            clock = QTimer(self)
            clock.timeout.connect(self._update_clock)
            clock.start(1_000)

            # Revenue ticker update
            rev_timer = QTimer(self)
            rev_timer.timeout.connect(self._update_revenue_badge)
            rev_timer.start(15_000)
            self._update_revenue_badge()

        # ------------------------------------------------------------------
        # Theme
        # ------------------------------------------------------------------

        def _apply_theme(self, mode: str) -> None:
            set_theme(mode)
            app = QApplication.instance()
            if app:
                app.setStyleSheet(build_stylesheet())

        # ------------------------------------------------------------------
        # Timers / live updates
        # ------------------------------------------------------------------

        def _update_clock(self) -> None:
            self._status_time.setText(datetime.now().strftime("%H:%M:%S"))

        def _update_revenue_badge(self) -> None:
            try:
                from ..core.database import revenue_summary
                rows = revenue_summary()
                total = sum(r.get("total", 0) for r in rows) if isinstance(rows, list) else 0
                self._revenue_badge.setText(f"  💰 ${total:,.2f}  ")
                if self._tray:
                    self._tray.update_revenue(total)
            except Exception:
                pass
            try:
                from ..core.database import get_all_agents
                agents = get_all_agents()
                active = sum(1 for a in agents if a.get("status") == "running")
                total = len(agents)
                self._agent_badge.setText(f"  🤖 {active}/{total}  ")
                if self._tray:
                    self._tray.update_agent_count(active, total)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Tab switching
        # ------------------------------------------------------------------

        def switch_tab(self, name: str) -> None:
            idx = TAB_INDICES.get(name)
            if idx is not None:
                self._tabs.setCurrentIndex(idx)

        # ------------------------------------------------------------------
        # Actions
        # ------------------------------------------------------------------

        def _open_settings(self) -> None:
            dlg = SettingsDialog(self)
            dlg.exec()

        def _export_data(self) -> None:
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Data", "autoearn_export.json", "JSON files (*.json)"
            )
            if path:
                try:
                    from ..core.database import revenue_summary, get_all_agents, recent_activity
                    data = {
                        "exported_at": datetime.now().isoformat(),
                        "revenue": revenue_summary(),
                        "agents": get_all_agents(),
                        "activity": recent_activity(limit=100),
                    }
                    Path(path).write_text(json.dumps(data, indent=2, default=str))
                    self._status_msg.setText(f"Exported to {path}")
                except Exception as exc:
                    QMessageBox.warning(self, "Export Error", str(exc))

        def _trigger_all_agents(self) -> None:
            try:
                from ..core.agent_manager import trigger_all
                trigger_all()
                self._status_msg.setText("Triggered all agents.")
            except Exception as exc:
                self._status_msg.setText(f"Error: {exc}")

        def _pause_all_agents(self) -> None:
            try:
                from ..core.agent_manager import pause_all
                pause_all()
                self._status_msg.setText("All agents paused.")
                if self._tray:
                    self._tray.update_status("paused")
            except Exception as exc:
                self._status_msg.setText(f"Error: {exc}")

        def _restart_all_agents(self) -> None:
            try:
                from ..core.agent_manager import restart_all
                restart_all()
                self._status_msg.setText("All agents restarted.")
            except Exception as exc:
                self._status_msg.setText(f"Error: {exc}")

        def _convene_council(self) -> None:
            try:
                from ..core.agent_manager import trigger_agent
                for name in ["ceo", "cfo", "cmo", "cto", "strategist"]:
                    trigger_agent(name)
                self._status_msg.setText("Council convened — all council agents triggered.")
            except Exception as exc:
                self._status_msg.setText(f"Error: {exc}")

        def _show_about(self) -> None:
            QMessageBox.about(
                self,
                "About AutoEarn",
                "<h2>AutoEarn v1.0</h2>"
                "<p>Autonomous AI Money-Making Organization</p>"
                "<p>A hierarchical multi-agent system with Council, Teams, and QC "
                "agents that autonomously generate revenue through content, "
                "affiliate marketing, trading signals, and outreach.</p>"
                "<p><b>Stack:</b> Python · PyQt6 · FastAPI · SQLite · Groq · Gemini</p>"
                "<p><small>© 2026 AutoEarn — All rights reserved</small></p>",
            )

        # ------------------------------------------------------------------
        # Window events
        # ------------------------------------------------------------------

        def closeEvent(self, event) -> None:
            """Hide to tray instead of closing."""
            event.ignore()
            self.hide()
            if self._tray:
                self._tray.notify(
                    "AutoEarn",
                    "Still running in the background. Click the tray icon to show.",
                    icon_type="info",
                )

else:
    class MainWindow:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass
        def show(self):
            pass
