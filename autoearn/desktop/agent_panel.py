"""
Agent management panel for the AutoEarn desktop dashboard.

Displays all agents in a table with status, last-run time, message counts,
and controls to enable/disable, trigger, or edit individual agents.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
        QLineEdit, QSplitter, QTextEdit, QGroupBox, QCheckBox,
        QMessageBox, QDialog, QDialogButtonBox, QFormLayout,
        QSpinBox, QFrame, QScrollArea,
    )
    from PyQt6.QtGui import QColor, QFont, QBrush, QIcon
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject

    _QT_OK = True
except ImportError:
    _QT_OK = False


# Status color map
STATUS_COLORS = {
    "running":  "#3fb950",
    "idle":     "#8b949e",
    "waiting":  "#d29922",
    "error":    "#f85149",
    "disabled": "#6e7681",
    "paused":   "#bc8cff",
}

ROLE_ICONS = {
    "council": "👑",
    "team":    "🤖",
    "qc":      "✅",
}


def _status_color(status: str) -> str:
    return STATUS_COLORS.get(status.lower(), "#8b949e")


if _QT_OK:

    class AgentWorker(QObject):
        """Background worker that polls agent status without blocking the UI."""

        data_ready = pyqtSignal(list)
        error = pyqtSignal(str)

        def __init__(self):
            super().__init__()

        def fetch(self) -> None:
            """Fetch agent status data from the database/manager."""
            try:
                from ..core.database import get_all_agents
                agents = get_all_agents()
                self.data_ready.emit(agents)
            except Exception as exc:
                self.error.emit(str(exc))

    class AgentEditDialog(QDialog):
        """Dialog for editing an agent's JSON definition."""

        def __init__(self, agent_def: dict, parent=None):
            super().__init__(parent)
            self.setWindowTitle(f"Edit Agent: {agent_def.get('name', 'unknown')}")
            self.setMinimumSize(600, 500)
            self._agent_def = agent_def.copy()
            self._setup_ui()

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)

            form = QFormLayout()

            self._name_edit = QLineEdit(self._agent_def.get("name", ""))
            form.addRow("Name:", self._name_edit)

            self._goal_edit = QLineEdit(self._agent_def.get("goal", ""))
            form.addRow("Goal:", self._goal_edit)

            self._model_edit = QLineEdit(self._agent_def.get("model_preference", ""))
            form.addRow("Model:", self._model_edit)

            self._interval_spin = QSpinBox()
            self._interval_spin.setRange(1, 1440)
            self._interval_spin.setValue(int(self._agent_def.get("interval_minutes", 60)))
            form.addRow("Interval (min):", self._interval_spin)

            self._enabled_check = QCheckBox("Enabled")
            self._enabled_check.setChecked(bool(self._agent_def.get("enabled", True)))
            form.addRow("", self._enabled_check)

            layout.addLayout(form)

            # Full JSON editor
            lbl = QLabel("Full JSON definition (advanced):")
            lbl.setProperty("class", "muted")
            layout.addWidget(lbl)

            self._json_edit = QTextEdit()
            self._json_edit.setPlainText(json.dumps(self._agent_def, indent=2))
            self._json_edit.setFont(QFont("Courier New", 11))
            layout.addWidget(self._json_edit)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save |
                QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self._on_save)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _on_save(self) -> None:
            try:
                json_text = self._json_edit.toPlainText()
                updated = json.loads(json_text)
                # Apply simple-field overrides
                updated["name"] = self._name_edit.text()
                updated["goal"] = self._goal_edit.text()
                updated["model_preference"] = self._model_edit.text()
                updated["interval_minutes"] = self._interval_spin.value()
                updated["enabled"] = self._enabled_check.isChecked()
                self._agent_def = updated
                self.accept()
            except json.JSONDecodeError as exc:
                QMessageBox.warning(self, "JSON Error", f"Invalid JSON: {exc}")

        def get_updated_def(self) -> dict:
            return self._agent_def

    class AgentDetailPanel(QWidget):
        """Right-side panel showing details for the selected agent."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._setup_ui()

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            self._name_label = QLabel("Select an agent")
            self._name_label.setProperty("class", "heading")
            layout.addWidget(self._name_label)

            self._status_label = QLabel("")
            layout.addWidget(self._status_label)

            # Stats group
            stats_group = QGroupBox("Statistics")
            stats_layout = QFormLayout(stats_group)
            self._stat_labels: dict[str, QLabel] = {}
            for field in ["Role", "Team", "Model", "Interval", "Last Run",
                           "Messages In", "Messages Out", "Runs Today", "Budget"]:
                lbl = QLabel("—")
                self._stat_labels[field] = lbl
                stats_layout.addRow(f"{field}:", lbl)
            layout.addWidget(stats_group)

            # Goal
            goal_group = QGroupBox("Goal")
            goal_layout = QVBoxLayout(goal_group)
            self._goal_label = QLabel("—")
            self._goal_label.setWordWrap(True)
            goal_layout.addWidget(self._goal_label)
            layout.addWidget(goal_group)

            # Tools
            tools_group = QGroupBox("Tools")
            tools_layout = QVBoxLayout(tools_group)
            self._tools_label = QLabel("—")
            self._tools_label.setWordWrap(True)
            tools_layout.addWidget(self._tools_label)
            layout.addWidget(tools_group)

            # Memory
            mem_group = QGroupBox("Memory")
            mem_layout = QVBoxLayout(mem_group)
            self._memory_edit = QTextEdit()
            self._memory_edit.setReadOnly(True)
            self._memory_edit.setMaximumHeight(100)
            mem_layout.addWidget(self._memory_edit)
            layout.addWidget(mem_group)

            # Recent activity
            log_group = QGroupBox("Recent Activity")
            log_layout = QVBoxLayout(log_group)
            self._log_edit = QTextEdit()
            self._log_edit.setReadOnly(True)
            self._log_edit.setFont(QFont("Courier New", 10))
            log_layout.addWidget(self._log_edit)
            layout.addWidget(log_group)

            layout.addStretch()

        def show_agent(self, agent: dict) -> None:
            """Populate the detail panel with agent data."""
            name = agent.get("name", "unknown")
            role = agent.get("role", "team")
            status = agent.get("status", "idle")

            icon = ROLE_ICONS.get(role, "🤖")
            self._name_label.setText(f"{icon} {name}")

            color = _status_color(status)
            self._status_label.setText(f"● {status.upper()}")
            self._status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

            stats = {
                "Role":         role.title(),
                "Team":         agent.get("team", "—"),
                "Model":        agent.get("model_preference", "auto"),
                "Interval":     f"{agent.get('interval_minutes', 60)} min",
                "Last Run":     agent.get("last_run", "Never"),
                "Messages In":  str(agent.get("messages_in", 0)),
                "Messages Out": str(agent.get("messages_out", 0)),
                "Runs Today":   str(agent.get("runs_today", 0)),
                "Budget":       f"${float(agent.get('budget_usd', 0)):.2f}",
            }
            for field, val in stats.items():
                if field in self._stat_labels:
                    self._stat_labels[field].setText(val)

            self._goal_label.setText(agent.get("goal", "—"))

            tools = agent.get("tools", [])
            self._tools_label.setText(", ".join(tools) if tools else "none")

            memory = agent.get("memory", {})
            self._memory_edit.setPlainText(
                json.dumps(memory, indent=2) if memory else "{}"
            )

            # Load recent activity from DB
            try:
                from ..core.database import recent_activity
                logs = recent_activity(agent=name, limit=20)
                lines = [f"[{l['ts']:.0f}] {l['action']}: {l['detail']}" for l in logs]
                self._log_edit.setPlainText("\n".join(lines) or "No activity yet.")
            except Exception:
                self._log_edit.setPlainText("Could not load activity log.")

    class AgentPanel(QWidget):
        """
        Full agent management tab.

        Left: table of all agents with status indicators.
        Right: detail panel for the selected agent.
        Controls: trigger, enable/disable, edit, refresh.
        """

        agent_triggered = pyqtSignal(str)
        agent_toggled   = pyqtSignal(str, bool)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._agents: list[dict] = []
            self._setup_ui()

            self._refresh_timer = QTimer(self)
            self._refresh_timer.timeout.connect(self.refresh)
            self._refresh_timer.start(15_000)

            self.refresh()

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            # Toolbar
            toolbar = QHBoxLayout()
            self._filter_edit = QLineEdit()
            self._filter_edit.setPlaceholderText("Filter agents...")
            self._filter_edit.textChanged.connect(self._apply_filter)
            toolbar.addWidget(self._filter_edit)

            self._role_combo = QComboBox()
            self._role_combo.addItems(["All Roles", "council", "team", "qc"])
            self._role_combo.currentTextChanged.connect(self._apply_filter)
            toolbar.addWidget(self._role_combo)

            self._status_combo = QComboBox()
            self._status_combo.addItems(["All Status", "running", "idle", "error", "disabled"])
            self._status_combo.currentTextChanged.connect(self._apply_filter)
            toolbar.addWidget(self._status_combo)

            toolbar.addStretch()

            trigger_all_btn = QPushButton("▶ Trigger All")
            trigger_all_btn.clicked.connect(self._trigger_all)
            toolbar.addWidget(trigger_all_btn)

            refresh_btn = QPushButton("↻ Refresh")
            refresh_btn.clicked.connect(self.refresh)
            toolbar.addWidget(refresh_btn)

            layout.addLayout(toolbar)

            # Main splitter
            splitter = QSplitter(Qt.Orientation.Horizontal)

            # Left: agent table
            left_widget = QWidget()
            left_layout = QVBoxLayout(left_widget)
            left_layout.setContentsMargins(0, 0, 0, 0)

            self._table = QTableWidget()
            self._table.setColumnCount(6)
            self._table.setHorizontalHeaderLabels(
                ["", "Name", "Role", "Status", "Last Run", "Msgs"]
            )
            self._table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )
            self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self._table.verticalHeader().setVisible(False)
            self._table.setAlternatingRowColors(True)
            self._table.itemSelectionChanged.connect(self._on_selection_changed)
            left_layout.addWidget(self._table)

            # Per-agent action buttons
            agent_btns = QHBoxLayout()
            self._trigger_btn = QPushButton("▶ Trigger")
            self._trigger_btn.clicked.connect(self._trigger_selected)
            agent_btns.addWidget(self._trigger_btn)

            self._toggle_btn = QPushButton("⏸ Disable")
            self._toggle_btn.clicked.connect(self._toggle_selected)
            agent_btns.addWidget(self._toggle_btn)

            self._edit_btn = QPushButton("✏ Edit")
            self._edit_btn.clicked.connect(self._edit_selected)
            agent_btns.addWidget(self._edit_btn)

            for btn in [self._trigger_btn, self._toggle_btn, self._edit_btn]:
                btn.setEnabled(False)

            left_layout.addLayout(agent_btns)
            splitter.addWidget(left_widget)

            # Right: detail panel
            self._detail = AgentDetailPanel()
            scroll = QScrollArea()
            scroll.setWidget(self._detail)
            scroll.setWidgetResizable(True)
            scroll.setMinimumWidth(300)
            splitter.addWidget(scroll)

            splitter.setSizes([500, 320])
            layout.addWidget(splitter)

        def refresh(self) -> None:
            """Reload agent data and repopulate the table."""
            try:
                from ..core.database import get_all_agents
                self._agents = get_all_agents()
            except Exception:
                self._agents = self._dummy_agents()

            self._populate_table(self._agents)

        def _dummy_agents(self) -> list[dict]:
            """Return placeholder data when DB is unavailable."""
            roles = ["council", "council", "council", "team", "team", "qc"]
            names = ["ceo", "cfo", "cmo", "writer", "researcher", "content_qc"]
            statuses = ["idle", "idle", "running", "idle", "running", "idle"]
            return [
                {"name": n, "role": r, "status": s, "team": r,
                 "model_preference": "groq/llama-3.3-70b-versatile",
                 "interval_minutes": 60, "enabled": True, "goal": "—",
                 "tools": [], "memory": {}}
                for n, r, s in zip(names, roles, statuses)
            ]

        def _populate_table(self, agents: list[dict]) -> None:
            self._table.setRowCount(0)
            filter_text = self._filter_edit.text().lower()
            role_filter = self._role_combo.currentText()
            status_filter = self._status_combo.currentText()

            for agent in agents:
                name = agent.get("name", "")
                role = agent.get("role", "team")
                status = agent.get("status", "idle")
                enabled = agent.get("enabled", True)

                if filter_text and filter_text not in name.lower():
                    continue
                if role_filter != "All Roles" and role != role_filter:
                    continue
                if status_filter != "All Status" and status != status_filter:
                    continue

                row = self._table.rowCount()
                self._table.insertRow(row)

                # Status dot
                dot = QTableWidgetItem("●")
                color = _status_color(status if enabled else "disabled")
                dot.setForeground(QBrush(QColor(color)))
                dot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, 0, dot)

                # Name
                name_item = QTableWidgetItem(name)
                if not enabled:
                    name_item.setForeground(QBrush(QColor("#6e7681")))
                self._table.setItem(row, 1, name_item)

                # Role with icon
                icon = ROLE_ICONS.get(role, "🤖")
                role_item = QTableWidgetItem(f"{icon} {role}")
                self._table.setItem(row, 2, role_item)

                # Status
                status_item = QTableWidgetItem(status.upper() if enabled else "DISABLED")
                status_item.setForeground(QBrush(QColor(color)))
                self._table.setItem(row, 3, status_item)

                # Last run
                last_run = agent.get("last_run", "—")
                self._table.setItem(row, 4, QTableWidgetItem(str(last_run)))

                # Message count
                msgs = agent.get("messages_in", 0) + agent.get("messages_out", 0)
                self._table.setItem(row, 5, QTableWidgetItem(str(msgs)))

                # Store agent data in first item
                self._table.item(row, 0).setData(Qt.ItemDataRole.UserRole, agent)

            self._table.resizeColumnsToContents()
            self._table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )

        def _apply_filter(self) -> None:
            self._populate_table(self._agents)

        def _on_selection_changed(self) -> None:
            rows = self._table.selectedItems()
            has_sel = len(rows) > 0
            for btn in [self._trigger_btn, self._toggle_btn, self._edit_btn]:
                btn.setEnabled(has_sel)

            if has_sel:
                row = self._table.selectedItems()[0].row()
                agent = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                if agent:
                    self._detail.show_agent(agent)
                    enabled = agent.get("enabled", True)
                    self._toggle_btn.setText("⏸ Disable" if enabled else "▶ Enable")

        def _get_selected_agent(self) -> dict | None:
            rows = self._table.selectedItems()
            if not rows:
                return None
            row = rows[0].row()
            item = self._table.item(row, 0)
            return item.data(Qt.ItemDataRole.UserRole) if item else None

        def _trigger_selected(self) -> None:
            agent = self._get_selected_agent()
            if agent:
                name = agent.get("name", "")
                try:
                    from ..core.agent_manager import trigger_agent
                    trigger_agent(name)
                except Exception as exc:
                    QMessageBox.warning(self, "Error", f"Could not trigger agent: {exc}")
                self.agent_triggered.emit(name)
                self.refresh()

        def _toggle_selected(self) -> None:
            agent = self._get_selected_agent()
            if agent:
                name = agent.get("name", "")
                enabled = agent.get("enabled", True)
                try:
                    from ..core.self_tools import kill_agent, update_goal
                    if enabled:
                        kill_agent(name)
                    else:
                        pass  # re-enable
                except Exception:
                    pass
                self.agent_toggled.emit(name, not enabled)
                self.refresh()

        def _edit_selected(self) -> None:
            agent = self._get_selected_agent()
            if not agent:
                return
            dlg = AgentEditDialog(agent, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                updated = dlg.get_updated_def()
                try:
                    name = updated["name"]
                    path = Path(__file__).parent.parent
                    role = updated.get("role", "team")
                    team = updated.get("team", "")
                    if role == "council":
                        json_path = path / "council" / f"{name}.json"
                    elif role == "qc":
                        json_path = path / "qc" / f"{name}.json"
                    else:
                        json_path = path / "teams" / team / f"{name}.json"
                    json_path.write_text(json.dumps(updated, indent=2))
                except Exception as exc:
                    QMessageBox.warning(self, "Error", f"Could not save: {exc}")
                self.refresh()

        def _trigger_all(self) -> None:
            reply = QMessageBox.question(
                self, "Trigger All Agents",
                "Trigger all enabled agents now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    from ..core.agent_manager import trigger_all
                    trigger_all()
                except Exception as exc:
                    QMessageBox.warning(self, "Error", str(exc))
                self.refresh()

else:
    # Stubs when Qt is not installed
    class AgentPanel:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass

    class AgentEditDialog:  # type: ignore[no-redef]
        pass
