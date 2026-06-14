"""
Log viewer widget — shows live application and agent logs with
filtering, search, level highlighting, and export.
"""

from __future__ import annotations

import time
from datetime import datetime

try:
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QComboBox, QLineEdit, QTextEdit, QCheckBox, QFileDialog,
        QSplitter, QPlainTextEdit,
    )
    from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont, QBrush
    from PyQt6.QtCore import Qt, QTimer
    _QT_OK = True
except ImportError:
    _QT_OK = False

LEVEL_COLORS = {
    "DEBUG":    "#6e7681",
    "INFO":     "#8b949e",
    "SUCCESS":  "#3fb950",
    "WARNING":  "#d29922",
    "ERROR":    "#f85149",
    "CRITICAL": "#bc8cff",
    "REVENUE":  "#58a6ff",
    "AGENT":    "#39c5cf",
    "QC":       "#3fb950",
    "COUNCIL":  "#bc8cff",
}

MAX_LOG_LINES = 5000


if _QT_OK:

    class LogViewer(QWidget):
        """
        Scrolling log viewer with:
        - Live polling from DB activity log
        - Level filter (DEBUG / INFO / WARNING / ERROR)
        - Agent filter
        - Text search with highlight
        - Export to file
        - Auto-scroll toggle
        - Line count badge
        """

        def __init__(self, parent=None):
            super().__init__(parent)
            self._lines: list[dict] = []
            self._last_id: int = 0
            self._auto_scroll = True
            self._paused = False
            self._setup_ui()

            self._timer = QTimer(self)
            self._timer.timeout.connect(self._poll_logs)
            self._timer.start(2_000)

            self._poll_logs()

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            # Toolbar
            toolbar = QHBoxLayout()

            self._level_combo = QComboBox()
            self._level_combo.addItems(
                ["All Levels", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]
            )
            self._level_combo.currentTextChanged.connect(self._redraw)
            toolbar.addWidget(QLabel("Level:"))
            toolbar.addWidget(self._level_combo)

            self._agent_combo = QComboBox()
            self._agent_combo.addItems(["All Agents"])
            self._agent_combo.currentTextChanged.connect(self._redraw)
            toolbar.addWidget(QLabel("Agent:"))
            toolbar.addWidget(self._agent_combo)

            self._search_edit = QLineEdit()
            self._search_edit.setPlaceholderText("Search logs...")
            self._search_edit.textChanged.connect(self._redraw)
            toolbar.addWidget(self._search_edit)

            toolbar.addStretch()

            self._count_label = QLabel("0 lines")
            self._count_label.setProperty("class", "muted")
            toolbar.addWidget(self._count_label)

            self._auto_scroll_cb = QCheckBox("Auto-scroll")
            self._auto_scroll_cb.setChecked(True)
            self._auto_scroll_cb.toggled.connect(self._on_auto_scroll_toggled)
            toolbar.addWidget(self._auto_scroll_cb)

            pause_btn = QPushButton("⏸")
            pause_btn.setProperty("class", "icon-btn")
            pause_btn.setCheckable(True)
            pause_btn.setToolTip("Pause live updates")
            pause_btn.toggled.connect(self._on_pause_toggled)
            toolbar.addWidget(pause_btn)

            clear_btn = QPushButton("🗑")
            clear_btn.setProperty("class", "icon-btn")
            clear_btn.setToolTip("Clear log view")
            clear_btn.clicked.connect(self._clear)
            toolbar.addWidget(clear_btn)

            export_btn = QPushButton("💾")
            export_btn.setProperty("class", "icon-btn")
            export_btn.setToolTip("Export logs to file")
            export_btn.clicked.connect(self._export)
            toolbar.addWidget(export_btn)

            layout.addLayout(toolbar)

            # Log text area
            self._log_text = QPlainTextEdit()
            self._log_text.setReadOnly(True)
            self._log_text.setFont(QFont("Courier New", 10))
            self._log_text.setMaximumBlockCount(MAX_LOG_LINES)
            layout.addWidget(self._log_text)

            # Status bar
            status_row = QHBoxLayout()
            self._status_label = QLabel("Ready")
            self._status_label.setProperty("class", "muted")
            status_row.addWidget(self._status_label)
            status_row.addStretch()

            self._rate_label = QLabel("")
            self._rate_label.setProperty("class", "muted")
            status_row.addWidget(self._rate_label)
            layout.addLayout(status_row)

        def _poll_logs(self) -> None:
            if self._paused:
                return
            try:
                from ..core.database import recent_activity
                entries = recent_activity(limit=100)
                new = [e for e in entries if e.get("id", 0) > self._last_id]
                if new:
                    new.reverse()  # oldest first
                    for entry in new:
                        eid = entry.get("id", 0)
                        if eid > self._last_id:
                            self._last_id = eid
                        self._lines.append(entry)
                        # Keep agent combo up-to-date
                        agent = entry.get("agent", "")
                        if agent and self._agent_combo.findText(agent) < 0:
                            self._agent_combo.addItem(agent)
                    self._append_lines(new)
            except Exception:
                pass

        def _append_lines(self, entries: list[dict]) -> None:
            """Append new log entries to the text widget."""
            level_filter = self._level_combo.currentText()
            agent_filter = self._agent_combo.currentText()
            search = self._search_edit.text().lower()

            cursor = self._log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)

            for entry in entries:
                action = entry.get("action", "")
                agent = entry.get("agent", "")
                detail = entry.get("detail", "")
                ts = entry.get("ts", time.time())

                level = self._infer_level(action, detail)

                if level_filter != "All Levels" and level != level_filter:
                    continue
                if agent_filter != "All Agents" and agent != agent_filter:
                    continue

                dt = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
                line = f"[{dt}] [{level:<8}] [{agent:<16}] {action}: {detail}"

                if search and search not in line.lower():
                    continue

                fmt = QTextCharFormat()
                color = LEVEL_COLORS.get(level, "#8b949e")
                fmt.setForeground(QBrush(QColor(color)))
                if level in ("ERROR", "CRITICAL"):
                    fmt.setFontWeight(700)

                cursor.insertText(line + "\n", fmt)

            self._count_label.setText(f"{self._log_text.document().blockCount()} lines")

            if self._auto_scroll:
                self._log_text.ensureCursorVisible()
                sb = self._log_text.verticalScrollBar()
                sb.setValue(sb.maximum())

        def _redraw(self) -> None:
            """Re-render all stored log lines with current filters."""
            self._log_text.clear()
            self._append_lines(self._lines[-MAX_LOG_LINES:])

        def _infer_level(self, action: str, detail: str) -> str:
            """Infer log level from action/detail text."""
            combined = f"{action} {detail}".lower()
            if "error" in combined or "failed" in combined or "exception" in combined:
                return "ERROR"
            if "warn" in combined:
                return "WARNING"
            if "revenue" in combined or "earned" in combined or "$" in combined:
                return "REVENUE"
            if "approved" in combined or "published" in combined:
                return "SUCCESS"
            if action in ("council_meeting", "directive"):
                return "COUNCIL"
            if "qc" in combined or "review" in combined:
                return "QC"
            if "agent" in combined:
                return "AGENT"
            return "INFO"

        def append_line(self, level: str, agent: str, message: str) -> None:
            """Manually append a log line (called externally)."""
            entry = {
                "id": self._last_id + 1,
                "action": level,
                "agent": agent,
                "detail": message,
                "ts": time.time(),
            }
            self._last_id += 1
            self._lines.append(entry)
            self._append_lines([entry])

        def _on_auto_scroll_toggled(self, checked: bool) -> None:
            self._auto_scroll = checked

        def _on_pause_toggled(self, paused: bool) -> None:
            self._paused = paused
            self._status_label.setText("⏸ Paused" if paused else "● Live")

        def _clear(self) -> None:
            self._log_text.clear()
            self._lines.clear()
            self._count_label.setText("0 lines")

        def _export(self) -> None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Logs", "autoearn_logs.txt", "Text files (*.txt)"
            )
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._log_text.toPlainText())
                self._status_label.setText(f"Exported to {path}")

else:
    class LogViewer:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass
