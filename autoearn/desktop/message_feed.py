"""
Live message bus feed widget — shows agent-to-agent messages in real time.
Auto-scrolls to the latest message and highlights by message type.
"""

from __future__ import annotations

import json
from datetime import datetime

try:
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
        QListWidgetItem, QPushButton, QComboBox, QLineEdit, QSplitter,
        QTextEdit, QGroupBox,
    )
    from PyQt6.QtGui import QColor, QBrush, QFont
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal
    _QT_OK = True
except ImportError:
    _QT_OK = False

TYPE_COLORS = {
    "directive":   "#58a6ff",
    "work_item":   "#d29922",
    "output":      "#3fb950",
    "review":      "#bc8cff",
    "approval":    "#3fb950",
    "rejection":   "#f85149",
    "notification":"#8b949e",
}

TYPE_ICONS = {
    "directive":   "📋",
    "work_item":   "📝",
    "output":      "📤",
    "review":      "🔍",
    "approval":    "✅",
    "rejection":   "❌",
    "notification":"🔔",
}


if _QT_OK:

    class MessageItem(QListWidgetItem):
        """A styled list item representing one message bus entry."""

        def __init__(self, msg: dict):
            icon = TYPE_ICONS.get(msg.get("type", ""), "📨")
            from_a = msg.get("from_agent", "?")
            to_a = msg.get("to_agent", "?")
            subject = msg.get("subject", "")
            msg_type = msg.get("type", "")

            ts = msg.get("timestamp", 0)
            if ts:
                dt = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
            else:
                dt = "--:--:--"

            text = f"{icon} [{dt}] {from_a} → {to_a}  |  {subject[:60]}"
            super().__init__(text)

            color_hex = TYPE_COLORS.get(msg_type, "#8b949e")
            self.setForeground(QBrush(QColor(color_hex)))
            self.setData(Qt.ItemDataRole.UserRole, msg)

    class MessageFeed(QWidget):
        """
        Live message bus feed panel.

        Left: scrolling list of recent messages.
        Right: detail view of the selected message.
        """

        def __init__(self, parent=None):
            super().__init__(parent)
            self._messages: list[dict] = []
            self._last_id: int = 0
            self._auto_scroll = True
            self._setup_ui()

            self._timer = QTimer(self)
            self._timer.timeout.connect(self._poll_messages)
            self._timer.start(3_000)

            self._poll_messages()

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            # Toolbar
            toolbar = QHBoxLayout()

            self._filter_type = QComboBox()
            self._filter_type.addItems(["All Types"] + list(TYPE_ICONS.keys()))
            self._filter_type.currentTextChanged.connect(self._apply_filter)
            toolbar.addWidget(QLabel("Type:"))
            toolbar.addWidget(self._filter_type)

            self._filter_agent = QLineEdit()
            self._filter_agent.setPlaceholderText("Filter by agent...")
            self._filter_agent.textChanged.connect(self._apply_filter)
            toolbar.addWidget(self._filter_agent)

            toolbar.addStretch()

            self._count_label = QLabel("0 messages")
            self._count_label.setProperty("class", "muted")
            toolbar.addWidget(self._count_label)

            clear_btn = QPushButton("🗑 Clear")
            clear_btn.clicked.connect(self._clear)
            toolbar.addWidget(clear_btn)

            pause_btn = QPushButton("⏸ Pause")
            pause_btn.setCheckable(True)
            pause_btn.toggled.connect(self._on_pause_toggled)
            toolbar.addWidget(pause_btn)

            layout.addLayout(toolbar)

            # Splitter: list + detail
            splitter = QSplitter(Qt.Orientation.Horizontal)

            self._list = QListWidget()
            self._list.setFont(QFont("Courier New", 10))
            self._list.itemClicked.connect(self._on_item_clicked)
            splitter.addWidget(self._list)

            detail_widget = QGroupBox("Message Detail")
            detail_layout = QVBoxLayout(detail_widget)

            self._detail_from  = QLabel("—")
            self._detail_to    = QLabel("—")
            self._detail_type  = QLabel("—")
            self._detail_subj  = QLabel("—")
            self._detail_subj.setWordWrap(True)

            for lbl, val in [("From:", self._detail_from), ("To:", self._detail_to),
                               ("Type:", self._detail_type), ("Subject:", self._detail_subj)]:
                row = QHBoxLayout()
                row.addWidget(QLabel(lbl))
                row.addWidget(val)
                row.addStretch()
                detail_layout.addLayout(row)

            self._detail_body = QTextEdit()
            self._detail_body.setReadOnly(True)
            self._detail_body.setFont(QFont("Courier New", 10))
            detail_layout.addWidget(self._detail_body)

            splitter.addWidget(detail_widget)
            splitter.setSizes([500, 300])

            layout.addWidget(splitter)

            # Stats bar
            stats = QHBoxLayout()
            self._stats_labels: dict[str, QLabel] = {}
            for msg_type in list(TYPE_ICONS.keys()):
                lbl = QLabel(f"{TYPE_ICONS[msg_type]} 0")
                lbl.setStyleSheet(f"color: {TYPE_COLORS.get(msg_type, '#8b949e')}; font-size: 11px;")
                self._stats_labels[msg_type] = lbl
                stats.addWidget(lbl)
            stats.addStretch()
            layout.addLayout(stats)

        def _poll_messages(self) -> None:
            """Fetch new messages from the message bus."""
            try:
                from ..core.message_bus import recent_messages
                msgs = recent_messages(limit=200)
                new_msgs = [m for m in msgs if m.get("id", 0) > self._last_id]
                if new_msgs:
                    self._add_messages(new_msgs)
            except Exception:
                pass

        def _add_messages(self, msgs: list[dict]) -> None:
            """Add new messages to the list."""
            for msg in msgs:
                mid = msg.get("id", 0)
                if mid > self._last_id:
                    self._last_id = mid
                self._messages.append(msg)
                self._add_list_item(msg)

            self._update_stats()
            self._count_label.setText(f"{len(self._messages)} messages")

            if self._auto_scroll:
                self._list.scrollToBottom()

        def _add_list_item(self, msg: dict) -> None:
            """Apply filter before adding."""
            filter_type = self._filter_type.currentText()
            filter_agent = self._filter_agent.text().lower()

            msg_type = msg.get("type", "")
            from_a = msg.get("from_agent", "")
            to_a = msg.get("to_agent", "")

            if filter_type != "All Types" and msg_type != filter_type:
                return
            if filter_agent and filter_agent not in from_a.lower() and filter_agent not in to_a.lower():
                return

            item = MessageItem(msg)
            self._list.addItem(item)

        def _apply_filter(self) -> None:
            """Re-render the list applying current filters."""
            self._list.clear()
            for msg in self._messages[-500:]:
                self._add_list_item(msg)
            if self._auto_scroll:
                self._list.scrollToBottom()

        def _on_item_clicked(self, item: QListWidgetItem) -> None:
            """Show message detail when an item is clicked."""
            msg = item.data(Qt.ItemDataRole.UserRole)
            if not msg:
                return
            self._detail_from.setText(msg.get("from_agent", "—"))
            self._detail_to.setText(msg.get("to_agent", "—"))
            msg_type = msg.get("type", "—")
            color = TYPE_COLORS.get(msg_type, "#8b949e")
            self._detail_type.setText(msg_type)
            self._detail_type.setStyleSheet(f"color: {color}; font-weight: 600;")
            self._detail_subj.setText(msg.get("subject", "—"))
            body = msg.get("body", "")
            try:
                parsed = json.loads(body)
                body = json.dumps(parsed, indent=2)
            except Exception:
                pass
            self._detail_body.setPlainText(body)

        def _update_stats(self) -> None:
            """Update the type-count stats bar."""
            counts: dict[str, int] = {}
            for msg in self._messages:
                t = msg.get("type", "")
                counts[t] = counts.get(t, 0) + 1
            for msg_type, lbl in self._stats_labels.items():
                n = counts.get(msg_type, 0)
                lbl.setText(f"{TYPE_ICONS[msg_type]} {n}")

        def _clear(self) -> None:
            self._messages.clear()
            self._list.clear()
            self._count_label.setText("0 messages")

        def _on_pause_toggled(self, paused: bool) -> None:
            if paused:
                self._timer.stop()
            else:
                self._timer.start(3_000)

else:
    class MessageFeed:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass
