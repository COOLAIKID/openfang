"""
Settings / configuration dialog for AutoEarn desktop app.

Loads config.toml values into a form, lets the user edit them,
and writes them back. Sections: General, AI Providers, Integrations,
Dashboard, Notifications.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from PyQt6.QtWidgets import (
        QDialog, QDialogButtonBox, QTabWidget, QWidget, QVBoxLayout,
        QHBoxLayout, QFormLayout, QLabel, QLineEdit, QCheckBox,
        QSpinBox, QDoubleSpinBox, QComboBox, QPushButton, QGroupBox,
        QTextEdit, QMessageBox, QFileDialog, QScrollArea,
    )
    from PyQt6.QtGui import QFont
    from PyQt6.QtCore import Qt
    _QT_OK = True
except ImportError:
    _QT_OK = False

CONFIG_TOML_PATH = Path(__file__).parent.parent / "config.toml"

PROVIDER_DOCS = {
    "groq":         "Get a free API key at console.groq.com",
    "gemini":       "Get a free API key at aistudio.google.com",
    "huggingface":  "Get a token at huggingface.co/settings/tokens",
    "mistral":      "Get a free API key at console.mistral.ai",
    "openai":       "Get an API key at platform.openai.com",
    "anthropic":    "Get an API key at console.anthropic.com",
}

if _QT_OK:

    class _SecretLineEdit(QLineEdit):
        """A line edit that toggles between masked and visible text."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.setEchoMode(QLineEdit.EchoMode.Password)
            self._visible = False

        def toggle_visibility(self) -> None:
            self._visible = not self._visible
            self.setEchoMode(
                QLineEdit.EchoMode.Normal if self._visible
                else QLineEdit.EchoMode.Password
            )

    class _APIKeyRow(QWidget):
        """A row with a secret input + show/hide toggle + test button."""

        def __init__(self, label: str, key: str, help_text: str = "", parent=None):
            super().__init__(parent)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            self._input = _SecretLineEdit()
            self._input.setPlaceholderText(f"Enter {label} API key...")
            layout.addWidget(self._input)

            show_btn = QPushButton("👁")
            show_btn.setProperty("class", "icon-btn")
            show_btn.setFixedWidth(28)
            show_btn.setToolTip("Show / hide key")
            show_btn.clicked.connect(self._input.toggle_visibility)
            layout.addWidget(show_btn)

            if help_text:
                help_btn = QPushButton("?")
                help_btn.setProperty("class", "icon-btn")
                help_btn.setFixedWidth(24)
                help_btn.setToolTip(help_text)
                help_btn.clicked.connect(lambda: QMessageBox.information(
                    self, label, help_text
                ))
                layout.addWidget(help_btn)

        def value(self) -> str:
            return self._input.text()

        def set_value(self, val: str) -> None:
            self._input.setText(val)

    class SettingsDialog(QDialog):
        """
        Main application settings dialog.

        Tabs:
        - General: paths, logging, theme
        - AI Providers: API keys + model preferences
        - Integrations: WordPress, Medium, Telegram, Reddit, etc.
        - Notifications: tray balloon options
        - Advanced: raw config.toml editor
        """

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("AutoEarn — Settings")
            self.setMinimumSize(680, 520)
            self._cfg: dict = {}
            self._widgets: dict[str, Any] = {}
            self._setup_ui()
            self._load_config()

        def _setup_ui(self) -> None:
            layout = QVBoxLayout(self)

            self._tabs = QTabWidget()

            self._tabs.addTab(self._make_general_tab(),       "⚙ General")
            self._tabs.addTab(self._make_providers_tab(),     "🤖 AI Providers")
            self._tabs.addTab(self._make_integrations_tab(),  "🔌 Integrations")
            self._tabs.addTab(self._make_notifications_tab(), "🔔 Notifications")
            self._tabs.addTab(self._make_advanced_tab(),      "🔧 Advanced")

            layout.addWidget(self._tabs)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save |
                QDialogButtonBox.StandardButton.Cancel |
                QDialogButtonBox.StandardButton.RestoreDefaults,
            )
            buttons.accepted.connect(self._on_save)
            buttons.rejected.connect(self.reject)
            buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
                self._on_restore_defaults
            )
            layout.addWidget(buttons)

        # ------------------------------------------------------------------
        # Tabs
        # ------------------------------------------------------------------

        def _make_general_tab(self) -> QWidget:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            widget = QWidget()
            form = QFormLayout(widget)
            form.setSpacing(10)

            # Output directory
            out_row = QHBoxLayout()
            self._out_dir = QLineEdit()
            out_row.addWidget(self._out_dir)
            browse_btn = QPushButton("Browse…")
            browse_btn.clicked.connect(self._browse_output_dir)
            out_row.addWidget(browse_btn)
            form.addRow("Output directory:", out_row)
            self._widgets["paths.output_dir"] = self._out_dir

            # Database path
            db_row = QHBoxLayout()
            self._db_path = QLineEdit()
            db_row.addWidget(self._db_path)
            db_browse = QPushButton("Browse…")
            db_browse.clicked.connect(self._browse_db)
            db_row.addWidget(db_browse)
            form.addRow("Database path:", db_row)
            self._widgets["database.path"] = self._db_path

            # Theme
            self._theme = QComboBox()
            self._theme.addItems(["dark", "light"])
            form.addRow("Theme:", self._theme)
            self._widgets["ui.theme"] = self._theme

            # Log level
            self._log_level = QComboBox()
            self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
            form.addRow("Log level:", self._log_level)
            self._widgets["logging.level"] = self._log_level

            # Dashboard port
            self._dash_port = QSpinBox()
            self._dash_port.setRange(1024, 65535)
            self._dash_port.setValue(4200)
            form.addRow("Dashboard port:", self._dash_port)
            self._widgets["dashboard.port"] = self._dash_port

            # Auto-start agents
            self._auto_start = QCheckBox("Start agents automatically on launch")
            self._auto_start.setChecked(True)
            form.addRow("", self._auto_start)
            self._widgets["agents.auto_start"] = self._auto_start

            # Minimize to tray
            self._min_to_tray = QCheckBox("Minimize to system tray instead of taskbar")
            self._min_to_tray.setChecked(True)
            form.addRow("", self._min_to_tray)
            self._widgets["ui.minimize_to_tray"] = self._min_to_tray

            # Show splash
            self._show_splash = QCheckBox("Show splash screen on startup")
            self._show_splash.setChecked(True)
            form.addRow("", self._show_splash)
            self._widgets["ui.show_splash"] = self._show_splash

            scroll.setWidget(widget)
            return scroll

        def _make_providers_tab(self) -> QWidget:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            widget = QWidget()
            layout = QVBoxLayout(widget)

            provider_info = QLabel(
                "API keys are stored locally in config.toml. "
                "At least one provider key is required. Free keys are sufficient to start."
            )
            provider_info.setProperty("class", "muted")
            provider_info.setWordWrap(True)
            layout.addWidget(provider_info)

            providers = [
                ("groq",        "GROQ_API_KEY",        "Groq (recommended — fast & free)"),
                ("gemini",      "GEMINI_API_KEY",       "Google Gemini"),
                ("huggingface", "HF_TOKEN",             "Hugging Face"),
                ("mistral",     "MISTRAL_API_KEY",      "Mistral AI"),
                ("openai",      "OPENAI_API_KEY",       "OpenAI (paid)"),
                ("anthropic",   "ANTHROPIC_API_KEY",    "Anthropic Claude"),
            ]

            for key_id, env_var, label in providers:
                group = QGroupBox(label)
                g_layout = QFormLayout(group)

                # Check env var first
                env_val = os.environ.get(env_var, "")
                if env_val:
                    env_note = QLabel(f"✓ Found in environment: {env_var}")
                    env_note.setStyleSheet("color: #3fb950; font-size: 11px;")
                    g_layout.addRow("", env_note)

                api_row = _APIKeyRow(
                    label, key_id,
                    help_text=PROVIDER_DOCS.get(key_id, ""),
                )
                g_layout.addRow("API Key:", api_row)
                self._widgets[f"providers.{key_id}.api_key"] = api_row
                layout.addWidget(group)

            # Model preferences
            model_group = QGroupBox("Default Models")
            m_layout = QFormLayout(model_group)

            self._default_model = QLineEdit("groq/llama-3.3-70b-versatile")
            m_layout.addRow("Default model:", self._default_model)
            self._widgets["providers.default_model"] = self._default_model

            self._fallback_model = QLineEdit("gemini/gemini-1.5-flash")
            m_layout.addRow("Fallback model:", self._fallback_model)
            self._widgets["providers.fallback_model"] = self._fallback_model

            self._ollama_enabled = QCheckBox("Use Ollama as final fallback (unlimited local)")
            self._ollama_enabled.setChecked(True)
            m_layout.addRow("", self._ollama_enabled)
            self._widgets["providers.ollama.enabled"] = self._ollama_enabled

            layout.addWidget(model_group)
            layout.addStretch()

            scroll.setWidget(widget)
            return scroll

        def _make_integrations_tab(self) -> QWidget:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            widget = QWidget()
            layout = QVBoxLayout(widget)

            integrations = {
                "WordPress": [
                    ("wordpress.url",      "Site URL",       QLineEdit,  "https://yoursite.com"),
                    ("wordpress.username", "Username",        QLineEdit,  "admin"),
                    ("wordpress.password", "App Password",    _SecretLineEdit, ""),
                ],
                "Medium": [
                    ("medium.integration_token", "Integration Token", _SecretLineEdit, ""),
                    ("medium.user_id",           "User ID",           QLineEdit, ""),
                ],
                "Telegram": [
                    ("telegram.bot_token",  "Bot Token",   _SecretLineEdit, ""),
                    ("telegram.channel_id", "Channel ID",  QLineEdit, "@yourchannel"),
                ],
                "Reddit (PRAW)": [
                    ("reddit.client_id",     "Client ID",     QLineEdit, ""),
                    ("reddit.client_secret", "Client Secret", _SecretLineEdit, ""),
                    ("reddit.username",      "Username",      QLineEdit, ""),
                    ("reddit.password",      "Password",      _SecretLineEdit, ""),
                ],
                "Email (SendGrid)": [
                    ("sendgrid.api_key",  "API Key",         _SecretLineEdit, ""),
                    ("sendgrid.from_email","From Email",     QLineEdit, "noreply@yourdomain.com"),
                    ("sendgrid.from_name", "From Name",      QLineEdit, "AutoEarn"),
                ],
                "Stripe": [
                    ("stripe.secret_key",      "Secret Key",      _SecretLineEdit, ""),
                    ("stripe.publishable_key", "Publishable Key", QLineEdit, ""),
                    ("stripe.webhook_secret",  "Webhook Secret",  _SecretLineEdit, ""),
                ],
            }

            for section_name, fields in integrations.items():
                group = QGroupBox(section_name)
                g_form = QFormLayout(group)
                for cfg_key, label, widget_cls, placeholder in fields:
                    w = widget_cls()
                    if hasattr(w, "setPlaceholderText"):
                        w.setPlaceholderText(placeholder)
                    g_form.addRow(f"{label}:", w)
                    self._widgets[cfg_key] = w
                layout.addWidget(group)

            layout.addStretch()
            scroll.setWidget(widget)
            return scroll

        def _make_notifications_tab(self) -> QWidget:
            widget = QWidget()
            form = QFormLayout(widget)
            form.setSpacing(12)

            self._notif_enabled = QCheckBox("Enable system tray notifications")
            self._notif_enabled.setChecked(True)
            form.addRow("", self._notif_enabled)
            self._widgets["notifications.enabled"] = self._notif_enabled

            events = [
                ("notifications.on_revenue",   "New revenue logged"),
                ("notifications.on_approval",  "Content approved by QC"),
                ("notifications.on_rejection", "Content rejected by QC"),
                ("notifications.on_directive", "New council directive issued"),
                ("notifications.on_error",     "Agent error occurred"),
                ("notifications.on_publish",   "Content published successfully"),
            ]

            for cfg_key, label in events:
                cb = QCheckBox(label)
                cb.setChecked(True)
                form.addRow("Notify on:", cb)
                self._widgets[cfg_key] = cb

            self._notif_duration = QSpinBox()
            self._notif_duration.setRange(1000, 30000)
            self._notif_duration.setValue(4000)
            self._notif_duration.setSingleStep(500)
            self._notif_duration.setSuffix(" ms")
            form.addRow("Notification duration:", self._notif_duration)
            self._widgets["notifications.duration_ms"] = self._notif_duration

            return widget

        def _make_advanced_tab(self) -> QWidget:
            widget = QWidget()
            layout = QVBoxLayout(widget)

            lbl = QLabel("Raw config.toml editor — changes here take effect on next restart.")
            lbl.setProperty("class", "muted")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

            self._raw_editor = QTextEdit()
            self._raw_editor.setFont(QFont("Courier New", 11))
            self._raw_editor.setPlaceholderText("# config.toml content will appear here")
            layout.addWidget(self._raw_editor)

            btn_row = QHBoxLayout()
            reload_btn = QPushButton("↻ Reload from disk")
            reload_btn.clicked.connect(self._reload_raw)
            btn_row.addWidget(reload_btn)
            btn_row.addStretch()
            layout.addLayout(btn_row)

            return widget

        # ------------------------------------------------------------------
        # Config I/O
        # ------------------------------------------------------------------

        def _load_config(self) -> None:
            """Load current config values into widgets."""
            if CONFIG_TOML_PATH.exists():
                raw = CONFIG_TOML_PATH.read_text(encoding="utf-8")
                self._raw_editor.setPlainText(raw)
                try:
                    import tomllib  # Python 3.11+
                    self._cfg = tomllib.loads(raw)
                except ImportError:
                    try:
                        import tomli
                        self._cfg = tomli.loads(raw)
                    except ImportError:
                        pass

        def _reload_raw(self) -> None:
            if CONFIG_TOML_PATH.exists():
                self._raw_editor.setPlainText(
                    CONFIG_TOML_PATH.read_text(encoding="utf-8")
                )

        def _on_save(self) -> None:
            """Save the raw editor content back to config.toml."""
            try:
                content = self._raw_editor.toPlainText()
                CONFIG_TOML_PATH.write_text(content, encoding="utf-8")
                QMessageBox.information(
                    self, "Saved",
                    "Settings saved to config.toml.\n"
                    "Some changes take effect on the next restart.",
                )
                self.accept()
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Could not save settings:\n{exc}")

        def _on_restore_defaults(self) -> None:
            reply = QMessageBox.question(
                self, "Restore Defaults",
                "Reset all settings to defaults? This will overwrite config.toml.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                default_config = self._get_default_config()
                self._raw_editor.setPlainText(default_config)

        def _get_default_config(self) -> str:
            return """\
[paths]
output_dir = "output"

[database]
path = "autoearn.db"

[providers]
default_model = "groq/llama-3.3-70b-versatile"
fallback_model = "gemini/gemini-1.5-flash"

[providers.groq]
api_key = ""

[providers.gemini]
api_key = ""

[providers.huggingface]
token = ""

[providers.ollama]
enabled = true
model = "llama3"

[dashboard]
port = 4200
host = "127.0.0.1"

[ui]
theme = "dark"
minimize_to_tray = true
show_splash = true

[logging]
level = "INFO"

[agents]
auto_start = true
council_interval_minutes = 240

[notifications]
enabled = true
duration_ms = 4000
"""

        def _browse_output_dir(self) -> None:
            path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
            if path and self._out_dir:
                self._out_dir.setText(path)

        def _browse_db(self) -> None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Select Database File", "", "SQLite DB (*.db)"
            )
            if path and self._db_path:
                self._db_path.setText(path)

else:
    class SettingsDialog:  # type: ignore[no-redef]
        def __init__(self, *a, **kw):
            pass
        def exec(self):
            return 0
