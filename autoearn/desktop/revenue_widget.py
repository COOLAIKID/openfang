"""
Revenue chart widget for the AutoEarn desktop dashboard.

Renders a live-updating line chart of daily revenue using PyQt6's QPainter.
No Matplotlib dependency — pure Qt drawing for fast startup.
Also contains metric card widgets for the KPI row at the top of the dashboard.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any


def _qt():
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
        QSizePolicy, QGroupBox, QPushButton, QComboBox,
    )
    from PyQt6.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
        QLinearGradient, QPainterPath,
    )
    from PyQt6.QtCore import Qt, QRect, QPoint, QTimer, QSize, pyqtSignal
    return locals()


class MetricCard(object):
    """
    A card widget displaying a KPI metric with label, value, and change %.
    Created lazily when Qt is available.
    """

    @staticmethod
    def create(label: str, value: str = "$0.00", change_pct: float = 0.0,
               accent_color: str = "#58a6ff") -> Any:
        """Create and return a MetricCard QFrame."""
        try:
            from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel
            from PyQt6.QtGui import QColor
            from PyQt6.QtCore import Qt

            frame = QFrame()
            frame.setProperty("class", "card")
            frame.setFixedHeight(100)

            layout = QVBoxLayout(frame)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(4)

            lbl = QLabel(label.upper())
            lbl.setProperty("class", "metric-label")
            layout.addWidget(lbl)

            val_row = QHBoxLayout()
            val_lbl = QLabel(value)
            val_lbl.setProperty("class", "metric-value")
            val_lbl.setStyleSheet(f"color: {accent_color}; font-size: 24px; font-weight: 700;")
            val_row.addWidget(val_lbl)
            val_row.addStretch()

            if change_pct != 0:
                arrow = "▲" if change_pct > 0 else "▼"
                color = "#3fb950" if change_pct > 0 else "#f85149"
                chg = QLabel(f"{arrow} {abs(change_pct):.1f}%")
                chg.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
                val_row.addWidget(chg)

            layout.addLayout(val_row)
            frame._value_label = val_lbl
            return frame
        except ImportError:
            return None

    @staticmethod
    def update_value(card: Any, new_value: str, change_pct: float = 0.0) -> None:
        """Update the value displayed on an existing MetricCard."""
        if card and hasattr(card, "_value_label"):
            card._value_label.setText(new_value)


class RevenueChart(object):
    """
    Pure-Qt line chart widget for revenue data.
    Renders using QPainter for maximum performance and no external deps.
    """

    @staticmethod
    def create(title: str = "Daily Revenue") -> Any:
        """Create and return a RevenueChartWidget."""
        try:
            from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
            from PyQt6.QtCore import Qt
            widget = _RevenueChartWidget(title)
            return widget
        except ImportError:
            return None


try:
    from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QComboBox, QPushButton
    from PyQt6.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QLinearGradient,
        QPainterPath, QFontMetrics,
    )
    from PyQt6.QtCore import Qt, QRect, QTimer, QSize, pyqtSignal

    class _RevenueChartWidget(QWidget):
        """Internal widget — the actual QPainter chart."""

        period_changed = pyqtSignal(int)

        PERIODS = {"7 days": 7, "14 days": 14, "30 days": 30, "90 days": 90}
        SERIES_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#bc8cff", "#f85149"]

        def __init__(self, title: str = "Revenue", parent=None):
            super().__init__(parent)
            self.title = title
            self._series: dict[str, list[tuple[str, float]]] = {}
            self._period_days = 30
            self._padding = {"top": 40, "right": 20, "bottom": 50, "left": 65}
            self._hover_x: int | None = None
            self._grid_lines = 5
            self.setMinimumSize(400, 220)
            self.setSizePolicy(
                QWidget.sizePolicy(self).horizontalPolicy(),
                QWidget.sizePolicy(self).verticalPolicy(),
            )
            self.setMouseTracking(True)

            self._refresh_timer = QTimer(self)
            self._refresh_timer.timeout.connect(self._load_data)
            self._refresh_timer.start(30_000)

            self._load_data()

        def set_period(self, days: int) -> None:
            self._period_days = days
            self._load_data()
            self.update()

        def _load_data(self) -> None:
            """Pull revenue series from the database."""
            try:
                from ..core.analytics_tracker import daily_revenue_series
                rows = daily_revenue_series(self._period_days)
                self._series["Revenue"] = [(r["day"], float(r["revenue"])) for r in rows]
            except Exception:
                self._series["Revenue"] = self._dummy_data()
            self.update()

        def _dummy_data(self) -> list[tuple[str, float]]:
            import random
            days = self._period_days
            result = []
            for i in range(days):
                day = (datetime.now() - timedelta(days=days - i - 1)).strftime("%Y-%m-%d")
                result.append((day, round(random.uniform(0, 50), 2)))
            return result

        def add_series(self, name: str, data: list[tuple[str, float]]) -> None:
            """Add a named data series."""
            self._series[name] = data
            self.update()

        def clear_series(self) -> None:
            self._series.clear()
            self.update()

        def paintEvent(self, event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw(painter)

        def _draw(self, p: "QPainter") -> None:
            w, h = self.width(), self.height()
            pad = self._padding
            chart_x = pad["left"]
            chart_y = pad["top"]
            chart_w = w - pad["left"] - pad["right"]
            chart_h = h - pad["top"] - pad["bottom"]

            # Background
            p.fillRect(0, 0, w, h, QColor("#0d1117"))

            # Title
            p.setPen(QColor("#e6edf3"))
            font = QFont("Arial", 13, QFont.Weight.Bold)
            p.setFont(font)
            p.drawText(chart_x, 8, chart_w, 28, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)

            if not self._series:
                p.setPen(QColor("#6e7681"))
                p.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, "No data available")
                return

            # Gather all values across series
            all_values: list[float] = []
            for series_data in self._series.values():
                all_values.extend(v for _, v in series_data)

            max_val = max(all_values) if all_values else 1.0
            min_val = 0.0

            if max_val == min_val:
                max_val = min_val + 1.0

            val_range = max_val - min_val

            # Grid lines (horizontal)
            grid_pen = QPen(QColor("#30363d"), 1, Qt.PenStyle.DashLine)
            p.setPen(grid_pen)
            label_font = QFont("Arial", 9)
            p.setFont(label_font)
            p.setPen(QColor("#6e7681"))

            for i in range(self._grid_lines + 1):
                y = chart_y + chart_h - int(i * chart_h / self._grid_lines)
                val = min_val + i * val_range / self._grid_lines
                p.setPen(QColor("#6e7681"))
                label = f"${val:,.0f}" if val >= 100 else f"${val:.2f}"
                p.drawText(0, y - 8, pad["left"] - 4, 16,
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
                p.setPen(QPen(QColor("#30363d"), 1, Qt.PenStyle.DashLine))
                p.drawLine(chart_x, y, chart_x + chart_w, y)

            # Chart border
            p.setPen(QPen(QColor("#30363d"), 1))
            p.drawRect(chart_x, chart_y, chart_w, chart_h)

            # Plot each series
            for idx, (name, series_data) in enumerate(self._series.items()):
                if not series_data:
                    continue

                color = QColor(self.SERIES_COLORS[idx % len(self.SERIES_COLORS)])
                n = len(series_data)

                # Build points
                points = []
                for i, (day, val) in enumerate(series_data):
                    x = chart_x + int(i * chart_w / max(n - 1, 1))
                    y = chart_y + chart_h - int((val - min_val) / val_range * chart_h)
                    points.append((x, y))

                # Filled gradient area
                path = QPainterPath()
                if points:
                    path.moveTo(points[0][0], chart_y + chart_h)
                    for px, py in points:
                        path.lineTo(px, py)
                    path.lineTo(points[-1][0], chart_y + chart_h)
                    path.closeSubpath()

                    grad = QLinearGradient(0, chart_y, 0, chart_y + chart_h)
                    grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 60))
                    grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 5))
                    p.fillPath(path, QBrush(grad))

                # Line
                line_pen = QPen(color, 2, Qt.PenStyle.SolidLine)
                line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(line_pen)
                for i in range(len(points) - 1):
                    p.drawLine(points[i][0], points[i][1], points[i+1][0], points[i+1][1])

                # Dots at each data point
                p.setBrush(QBrush(color))
                p.setPen(QPen(QColor("#0d1117"), 1))
                dot_radius = 3
                for px, py in points:
                    p.drawEllipse(px - dot_radius, py - dot_radius,
                                   dot_radius * 2, dot_radius * 2)

            # X-axis labels (show first and last, plus some in between)
            first_series = next(iter(self._series.values()), [])
            if first_series:
                p.setPen(QColor("#6e7681"))
                p.setFont(label_font)
                n = len(first_series)
                step = max(1, n // 6)
                for i in range(0, n, step):
                    day, _ = first_series[i]
                    x = chart_x + int(i * chart_w / max(n - 1, 1))
                    short_day = day[5:] if len(day) > 5 else day
                    p.drawText(x - 20, chart_y + chart_h + 6, 40, 20,
                                Qt.AlignmentFlag.AlignCenter, short_day)

        def mouseMoveEvent(self, event) -> None:
            self._hover_x = event.position().x()
            self.update()

        def leaveEvent(self, event) -> None:
            self._hover_x = None
            self.update()

        def sizeHint(self) -> QSize:
            return QSize(600, 280)

    class RevenuePanel(QWidget):
        """Full revenue panel: period selector + chart + KPI cards."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._setup_ui()
            self._refresh_timer = QTimer(self)
            self._refresh_timer.timeout.connect(self.refresh)
            self._refresh_timer.start(60_000)

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)

            # -- KPI card row --
            kpi_row = QHBoxLayout()
            kpi_row.setSpacing(12)

            self._card_revenue = MetricCard.create("Total Revenue", "$0.00", 0.0, "#58a6ff")
            self._card_today = MetricCard.create("Today", "$0.00", 0.0, "#3fb950")
            self._card_conv = MetricCard.create("Conversions", "0", 0.0, "#bc8cff")
            self._card_aov = MetricCard.create("Avg Order", "$0.00", 0.0, "#d29922")

            for card in [self._card_revenue, self._card_today, self._card_conv, self._card_aov]:
                if card:
                    kpi_row.addWidget(card)

            layout.addLayout(kpi_row)

            # -- Period selector --
            ctrl_row = QHBoxLayout()
            ctrl_row.addWidget(QLabel("Period:"))
            self._period_combo = QComboBox()
            for label in _RevenueChartWidget.PERIODS:
                self._period_combo.addItem(label)
            self._period_combo.setCurrentText("30 days")
            self._period_combo.currentTextChanged.connect(self._on_period_changed)
            ctrl_row.addWidget(self._period_combo)
            ctrl_row.addStretch()

            refresh_btn = QPushButton("↻ Refresh")
            refresh_btn.clicked.connect(self.refresh)
            ctrl_row.addWidget(refresh_btn)
            layout.addLayout(ctrl_row)

            # -- Chart --
            self._chart = _RevenueChartWidget("Daily Revenue ($)")
            layout.addWidget(self._chart)

        def _on_period_changed(self, text: str) -> None:
            days = _RevenueChartWidget.PERIODS.get(text, 30)
            self._chart.set_period(days)
            self.refresh()

        def refresh(self) -> None:
            """Reload KPI data from the database."""
            try:
                from ..core.analytics_tracker import kpi_summary
                data = kpi_summary(30)
                MetricCard.update_value(
                    self._card_revenue,
                    f"${data.get('total_revenue', 0):,.2f}",
                    data.get("revenue_vs_prev_period_pct", 0),
                )
                MetricCard.update_value(
                    self._card_conv,
                    str(data.get("total_conversions", 0)),
                )
                MetricCard.update_value(
                    self._card_aov,
                    f"${data.get('avg_order_value', 0):,.2f}",
                )
            except Exception:
                pass

            self._chart._load_data()

except ImportError:
    # Qt not installed — provide stub classes
    class _RevenueChartWidget:  # type: ignore[no-redef]
        pass

    class RevenuePanel:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass
