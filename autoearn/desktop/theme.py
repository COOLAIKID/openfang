"""
QSS stylesheets and palette constants for AutoEarn desktop app.
Supports dark (default) and light modes.
"""

from __future__ import annotations

DARK_PALETTE = {
    "bg_primary":    "#0d1117",
    "bg_secondary":  "#161b22",
    "bg_tertiary":   "#21262d",
    "bg_card":       "#1c2128",
    "border":        "#30363d",
    "border_focus":  "#58a6ff",
    "text_primary":  "#e6edf3",
    "text_secondary":"#8b949e",
    "text_muted":    "#6e7681",
    "accent_blue":   "#58a6ff",
    "accent_green":  "#3fb950",
    "accent_orange": "#d29922",
    "accent_red":    "#f85149",
    "accent_purple": "#bc8cff",
    "accent_cyan":   "#39c5cf",
    "success":       "#238636",
    "warning":       "#9e6a03",
    "error":         "#8b1a1a",
    "scrollbar":     "#30363d",
    "scrollbar_hover":"#484f58",
    "button_bg":     "#21262d",
    "button_hover":  "#30363d",
    "button_pressed":"#161b22",
    "input_bg":      "#0d1117",
    "tab_active":    "#1c2128",
    "tab_inactive":  "#161b22",
    "chart_line1":   "#58a6ff",
    "chart_line2":   "#3fb950",
    "chart_line3":   "#d29922",
    "chart_line4":   "#bc8cff",
    "chart_grid":    "#30363d",
    "chart_bg":      "#0d1117",
    "revenue_up":    "#3fb950",
    "revenue_down":  "#f85149",
}

LIGHT_PALETTE = {
    "bg_primary":    "#ffffff",
    "bg_secondary":  "#f6f8fa",
    "bg_tertiary":   "#eaeef2",
    "bg_card":       "#ffffff",
    "border":        "#d0d7de",
    "border_focus":  "#0969da",
    "text_primary":  "#1f2328",
    "text_secondary":"#656d76",
    "text_muted":    "#848d97",
    "accent_blue":   "#0969da",
    "accent_green":  "#1a7f37",
    "accent_orange": "#9a6700",
    "accent_red":    "#cf222e",
    "accent_purple": "#8250df",
    "accent_cyan":   "#0a69da",
    "success":       "#1a7f37",
    "warning":       "#9a6700",
    "error":         "#cf222e",
    "scrollbar":     "#d0d7de",
    "scrollbar_hover":"#afb8c1",
    "button_bg":     "#f6f8fa",
    "button_hover":  "#eaeef2",
    "button_pressed":"#d0d7de",
    "input_bg":      "#ffffff",
    "tab_active":    "#ffffff",
    "tab_inactive":  "#f6f8fa",
    "chart_line1":   "#0969da",
    "chart_line2":   "#1a7f37",
    "chart_line3":   "#9a6700",
    "chart_line4":   "#8250df",
    "chart_grid":    "#d0d7de",
    "chart_bg":      "#ffffff",
    "revenue_up":    "#1a7f37",
    "revenue_down":  "#cf222e",
}

_current_palette = DARK_PALETTE


def set_theme(mode: str) -> None:
    """Switch between 'dark' and 'light' themes."""
    global _current_palette
    _current_palette = DARK_PALETTE if mode == "dark" else LIGHT_PALETTE


def p(key: str) -> str:
    """Get a palette color value."""
    return _current_palette.get(key, "#ffffff")


def build_stylesheet() -> str:
    """Build the full QSS stylesheet from the current palette."""
    c = _current_palette
    return f"""
/* ── Global ── */
QWidget {{
    background-color: {c['bg_primary']};
    color: {c['text_primary']};
    font-family: "Inter", "Segoe UI", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}}

QMainWindow {{
    background-color: {c['bg_primary']};
}}

/* ── Labels ── */
QLabel {{
    color: {c['text_primary']};
    background-color: transparent;
}}
QLabel[class="heading"] {{
    font-size: 18px;
    font-weight: 700;
    color: {c['text_primary']};
}}
QLabel[class="subheading"] {{
    font-size: 14px;
    font-weight: 600;
    color: {c['text_secondary']};
}}
QLabel[class="muted"] {{
    color: {c['text_muted']};
    font-size: 11px;
}}
QLabel[class="metric-value"] {{
    font-size: 28px;
    font-weight: 700;
    color: {c['accent_blue']};
}}
QLabel[class="metric-label"] {{
    font-size: 11px;
    color: {c['text_muted']};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QLabel[class="success"] {{
    color: {c['accent_green']};
    font-weight: 600;
}}
QLabel[class="error"] {{
    color: {c['accent_red']};
    font-weight: 600;
}}
QLabel[class="warning"] {{
    color: {c['accent_orange']};
    font-weight: 600;
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {c['button_bg']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
    outline: none;
}}
QPushButton:hover {{
    background-color: {c['button_hover']};
    border-color: {c['border_focus']};
}}
QPushButton:pressed {{
    background-color: {c['button_pressed']};
}}
QPushButton:disabled {{
    color: {c['text_muted']};
    border-color: {c['border']};
}}
QPushButton[class="primary"] {{
    background-color: {c['accent_blue']};
    color: #ffffff;
    border: none;
    font-weight: 600;
}}
QPushButton[class="primary"]:hover {{
    background-color: {c['accent_blue']}cc;
}}
QPushButton[class="danger"] {{
    background-color: {c['accent_red']};
    color: #ffffff;
    border: none;
}}
QPushButton[class="success"] {{
    background-color: {c['accent_green']};
    color: #ffffff;
    border: none;
}}
QPushButton[class="icon-btn"] {{
    background: transparent;
    border: none;
    padding: 4px;
    border-radius: 4px;
}}
QPushButton[class="icon-btn"]:hover {{
    background-color: {c['button_hover']};
}}

/* ── Tabs ── */
QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 6px;
    background-color: {c['bg_secondary']};
    margin-top: -1px;
}}
QTabBar::tab {{
    background-color: {c['tab_inactive']};
    color: {c['text_secondary']};
    padding: 8px 16px;
    border: 1px solid {c['border']};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    background-color: {c['tab_active']};
    color: {c['text_primary']};
    border-bottom-color: {c['tab_active']};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{
    background-color: {c['button_hover']};
    color: {c['text_primary']};
}}

/* ── Input fields ── */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {c['input_bg']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: {c['accent_blue']}55;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {c['border_focus']};
    outline: none;
}}

QComboBox {{
    background-color: {c['input_bg']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 5px 10px;
    min-width: 80px;
}}
QComboBox:hover {{
    border-color: {c['border_focus']};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {c['bg_tertiary']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    selection-background-color: {c['accent_blue']}44;
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {c['input_bg']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 5px 8px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c['border_focus']};
}}

/* ── Scroll bars ── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {c['scrollbar']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {c['scrollbar_hover']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background-color: {c['scrollbar']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {c['scrollbar_hover']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Table / Tree ── */
QTableWidget, QTreeWidget, QListWidget {{
    background-color: {c['bg_secondary']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    gridline-color: {c['border']};
    outline: none;
}}
QHeaderView::section {{
    background-color: {c['bg_tertiary']};
    color: {c['text_secondary']};
    padding: 8px 10px;
    border: none;
    border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QTableWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {c['border']};
}}
QTableWidget::item:selected {{
    background-color: {c['accent_blue']}22;
    color: {c['text_primary']};
}}
QTableWidget::item:hover {{
    background-color: {c['button_hover']};
}}
QListWidget::item {{
    padding: 6px 10px;
    border-radius: 4px;
    margin: 1px 4px;
}}
QListWidget::item:selected {{
    background-color: {c['accent_blue']}33;
    color: {c['text_primary']};
}}
QListWidget::item:hover {{
    background-color: {c['button_hover']};
}}

/* ── Progress bar ── */
QProgressBar {{
    background-color: {c['bg_tertiary']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    text-align: center;
    color: {c['text_primary']};
    height: 12px;
}}
QProgressBar::chunk {{
    background-color: {c['accent_blue']};
    border-radius: 3px;
}}

/* ── Group box ── */
QGroupBox {{
    background-color: {c['bg_card']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: {c['text_secondary']};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

/* ── Splitter ── */
QSplitter::handle {{
    background-color: {c['border']};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}

/* ── Status bar ── */
QStatusBar {{
    background-color: {c['bg_secondary']};
    color: {c['text_muted']};
    border-top: 1px solid {c['border']};
    font-size: 11px;
    padding: 2px 8px;
}}

/* ── Tool tip ── */
QToolTip {{
    background-color: {c['bg_tertiary']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}}

/* ── Menu ── */
QMenu {{
    background-color: {c['bg_tertiary']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px 6px 12px;
    border-radius: 4px;
    margin: 1px;
}}
QMenu::item:selected {{
    background-color: {c['accent_blue']}33;
}}
QMenu::separator {{
    height: 1px;
    background-color: {c['border']};
    margin: 4px 8px;
}}

/* ── Check box ── */
QCheckBox {{
    spacing: 8px;
    color: {c['text_primary']};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c['border']};
    border-radius: 3px;
    background-color: {c['input_bg']};
}}
QCheckBox::indicator:checked {{
    background-color: {c['accent_blue']};
    border-color: {c['accent_blue']};
}}

/* ── Slider ── */
QSlider::groove:horizontal {{
    height: 4px;
    background-color: {c['bg_tertiary']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background-color: {c['accent_blue']};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background-color: {c['accent_blue']};
    border-radius: 2px;
}}

/* ── Frame / card ── */
QFrame[class="card"] {{
    background-color: {c['bg_card']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 12px;
}}
QFrame[class="separator"] {{
    background-color: {c['border']};
    max-height: 1px;
}}
"""
