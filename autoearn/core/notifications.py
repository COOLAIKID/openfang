"""
core/notifications.py — Multi-channel notification system for AutoEarn.

Agents can send alerts to one or more configured channels (email, Slack,
Discord, Telegram, Pushover, ntfy.sh). Rate-limiting is enforced per
level/agent combination via SQLite.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP, SMTP_SSL
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    with _lock:
        if _db_conn is not None:
            return _db_conn
        try:
            from core.database import DB_PATH
        except ImportError:
            DB_PATH = "/tmp/autoearn.db"
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _init_schema(conn)
        _db_conn = conn
    return _db_conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT    NOT NULL,
            level       TEXT    NOT NULL,
            message     TEXT    NOT NULL,
            channels    TEXT    NOT NULL DEFAULT '[]',
            sent_at     TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notif_agent_level
            ON notification_log (agent, level, sent_at DESC);

        CREATE TABLE IF NOT EXISTS notification_rate (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket      TEXT    NOT NULL UNIQUE,
            count       INTEGER NOT NULL DEFAULT 0,
            window_start TEXT   NOT NULL
        );
    """)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Rate limiter: max 10 per hour per (agent, level)
# ---------------------------------------------------------------------------

RATE_LIMIT_MAX = 10
RATE_WINDOW_SECONDS = 3600


def _check_rate_limit(agent: str, level: str) -> bool:
    """
    Returns True if the notification is allowed, False if rate-limited.
    Increments the counter for the bucket.
    """
    db = _get_db()
    bucket = f"{agent}:{level}"
    now = time.time()
    with _lock:
        row = db.execute(
            "SELECT count, window_start FROM notification_rate WHERE bucket = ?",
            (bucket,),
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO notification_rate (bucket, count, window_start) VALUES (?,1,?)",
                (bucket, _now()),
            )
            db.commit()
            return True
        # Check if window has expired
        try:
            ws = datetime.fromisoformat(str(row["window_start"]).replace("Z", "+00:00"))
            if ws.tzinfo is None:
                ws = ws.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ws).total_seconds()
        except Exception:
            age = RATE_WINDOW_SECONDS + 1  # treat as expired
        if age >= RATE_WINDOW_SECONDS:
            db.execute(
                "UPDATE notification_rate SET count=1, window_start=? WHERE bucket=?",
                (_now(), bucket),
            )
            db.commit()
            return True
        if row["count"] >= RATE_LIMIT_MAX:
            logger.warning(
                "notifications: rate limit hit for agent=%s level=%s", agent, level
            )
            return False
        db.execute(
            "UPDATE notification_rate SET count=count+1 WHERE bucket=?", (bucket,)
        )
        db.commit()
        return True


def _log_notification(
    agent: str, level: str, message: str, channels: list[str]
) -> None:
    db = _get_db()
    with _lock:
        db.execute(
            "INSERT INTO notification_log (agent, level, message, channels, sent_at) VALUES (?,?,?,?,?)",
            (agent, level, message, json.dumps(channels), _now()),
        )
        db.commit()


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def _cfg(key: str, default: str = "") -> str:
    try:
        from core.config import get  # type: ignore
        val = get(key, default)
        return str(val) if val is not None else default
    except Exception:
        import os
        return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Base channel class
# ---------------------------------------------------------------------------


class NotificationChannel(ABC):
    """Abstract base for a notification channel."""

    name: str = "base"

    @abstractmethod
    def send(self, message: str, level: str = "info", agent: str = "system") -> bool:
        """
        Send *message* to this channel.

        Returns True on success, False on failure.
        """

    def is_configured(self) -> bool:
        """Return True if the channel has enough config to send."""
        return True


# ---------------------------------------------------------------------------
# Concrete channel implementations
# ---------------------------------------------------------------------------


class EmailChannel(NotificationChannel):
    """SMTP email notifications."""

    name = "email"

    def is_configured(self) -> bool:
        return bool(_cfg("SMTP_HOST") and _cfg("SMTP_TO"))

    def send(self, message: str, level: str = "info", agent: str = "system") -> bool:
        host = _cfg("SMTP_HOST")
        port = int(_cfg("SMTP_PORT", "587"))
        user = _cfg("SMTP_USER")
        password = _cfg("SMTP_PASSWORD")
        to_addr = _cfg("SMTP_TO")
        from_addr = _cfg("SMTP_FROM", user or "autoearn@localhost")
        use_ssl = _cfg("SMTP_SSL", "false").lower() == "true"

        if not (host and to_addr):
            return False

        subject = f"[AutoEarn][{level.upper()}] Alert from {agent}"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.attach(MIMEText(message, "plain"))

        try:
            if use_ssl:
                with SMTP_SSL(host, port) as smtp:
                    if user and password:
                        smtp.login(user, password)
                    smtp.sendmail(from_addr, [to_addr], msg.as_string())
            else:
                with SMTP(host, port) as smtp:
                    smtp.ehlo()
                    smtp.starttls()
                    if user and password:
                        smtp.login(user, password)
                    smtp.sendmail(from_addr, [to_addr], msg.as_string())
            logger.info("email notification sent to %s", to_addr)
            return True
        except Exception as exc:
            logger.warning("email notification failed: %s", exc)
            return False


class SlackChannel(NotificationChannel):
    """Slack incoming webhook notifications."""

    name = "slack"

    def is_configured(self) -> bool:
        return bool(_cfg("SLACK_WEBHOOK_URL"))

    def send(self, message: str, level: str = "info", agent: str = "system") -> bool:
        url = _cfg("SLACK_WEBHOOK_URL")
        if not url:
            return False
        emoji = {"info": ":information_source:", "warning": ":warning:",
                 "error": ":red_circle:", "revenue": ":moneybag:",
                 "milestone": ":trophy:"}.get(level, ":bell:")
        payload = {
            "text": f"{emoji} *[{level.upper()}]* _{agent}_\n{message}"
        }
        return _post_json(url, payload, "Slack")


class DiscordChannel(NotificationChannel):
    """Discord webhook notifications."""

    name = "discord"

    def is_configured(self) -> bool:
        return bool(_cfg("DISCORD_WEBHOOK_URL"))

    def send(self, message: str, level: str = "info", agent: str = "system") -> bool:
        url = _cfg("DISCORD_WEBHOOK_URL")
        if not url:
            return False
        color = {"info": 3447003, "warning": 16776960, "error": 15158332,
                 "revenue": 3066993, "milestone": 10181046}.get(level, 7506394)
        payload = {
            "embeds": [{
                "title": f"[{level.upper()}] {agent}",
                "description": message[:2000],
                "color": color,
                "timestamp": _now(),
            }]
        }
        return _post_json(url, payload, "Discord")


class TelegramChannel(NotificationChannel):
    """Telegram bot notifications."""

    name = "telegram"

    def is_configured(self) -> bool:
        return bool(_cfg("TELEGRAM_BOT_TOKEN") and _cfg("TELEGRAM_CHAT_ID"))

    def send(self, message: str, level: str = "info", agent: str = "system") -> bool:
        token = _cfg("TELEGRAM_BOT_TOKEN")
        chat_id = _cfg("TELEGRAM_CHAT_ID")
        if not (token and chat_id):
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        text = f"[{level.upper()}] {agent}\n\n{message}"
        payload = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "Markdown",
        }
        return _post_json(url, payload, "Telegram")


class PushoverChannel(NotificationChannel):
    """Pushover push notification service."""

    name = "pushover"

    def is_configured(self) -> bool:
        return bool(_cfg("PUSHOVER_APP_TOKEN") and _cfg("PUSHOVER_USER_KEY"))

    def send(self, message: str, level: str = "info", agent: str = "system") -> bool:
        app_token = _cfg("PUSHOVER_APP_TOKEN")
        user_key = _cfg("PUSHOVER_USER_KEY")
        if not (app_token and user_key):
            return False
        priority = {"error": 1, "warning": 0}.get(level, -1)
        payload = {
            "token": app_token,
            "user": user_key,
            "message": message[:1024],
            "title": f"AutoEarn [{level.upper()}] {agent}",
            "priority": priority,
        }
        url = "https://api.pushover.net/1/messages.json"
        return _post_json(url, payload, "Pushover")


class NtfyChannel(NotificationChannel):
    """ntfy.sh push notifications (self-hosted or cloud)."""

    name = "ntfy"

    def is_configured(self) -> bool:
        return bool(_cfg("NTFY_TOPIC"))

    def send(self, message: str, level: str = "info", agent: str = "system") -> bool:
        topic = _cfg("NTFY_TOPIC")
        server = _cfg("NTFY_SERVER", "https://ntfy.sh")
        if not topic:
            return False
        url = f"{server.rstrip('/')}/{topic}"
        priority_map = {"error": "urgent", "warning": "high", "info": "default"}
        headers = {
            "Title": f"AutoEarn [{level.upper()}] {agent}",
            "Priority": priority_map.get(level, "default"),
            "Tags": level,
            "Content-Type": "text/plain",
        }
        try:
            data = message.encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = resp.status < 400
            logger.info("ntfy notification sent topic=%s ok=%s", topic, ok)
            return ok
        except Exception as exc:
            logger.warning("ntfy notification failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, channel_name: str) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status < 400
        logger.info("%s notification sent ok=%s", channel_name, ok)
        return ok
    except Exception as exc:
        logger.warning("%s notification failed: %s", channel_name, exc)
        return False


# ---------------------------------------------------------------------------
# Channel registry
# ---------------------------------------------------------------------------

_channels: dict[str, NotificationChannel] = {
    "email": EmailChannel(),
    "slack": SlackChannel(),
    "discord": DiscordChannel(),
    "telegram": TelegramChannel(),
    "pushover": PushoverChannel(),
    "ntfy": NtfyChannel(),
}


def register_channel(name: str, channel: NotificationChannel) -> None:
    """Register a custom notification channel at runtime."""
    _channels[name] = channel
    logger.info("notifications: registered channel '%s'", name)


def get_channels() -> dict[str, NotificationChannel]:
    """Return the current channel registry."""
    return dict(_channels)


# ---------------------------------------------------------------------------
# Public send functions
# ---------------------------------------------------------------------------


def notify(
    message: str,
    level: str = "info",
    channels: Optional[list[str]] = None,
    agent: str = "system",
) -> dict[str, bool]:
    """
    Send *message* to one or more notification channels.

    Parameters
    ----------
    message:  The notification body text.
    level:    Severity level: 'info', 'warning', 'error', 'revenue', 'milestone'.
    channels: List of channel names to use. If None, sends to all configured channels.
    agent:    Name of the originating agent (used for rate limiting and logging).

    Returns
    -------
    Dict mapping channel name to success bool.
    """
    if not _check_rate_limit(agent, level):
        return {}

    target_channels = channels if channels is not None else list(_channels.keys())
    results: dict[str, bool] = {}
    sent_to: list[str] = []

    for ch_name in target_channels:
        ch = _channels.get(ch_name)
        if ch is None:
            logger.debug("notifications: unknown channel '%s', skipping", ch_name)
            continue
        if not ch.is_configured():
            logger.debug("notifications: channel '%s' not configured, skipping", ch_name)
            continue
        try:
            ok = ch.send(message, level=level, agent=agent)
        except Exception as exc:
            logger.error("notifications: channel '%s' raised: %s", ch_name, exc)
            ok = False
        results[ch_name] = ok
        if ok:
            sent_to.append(ch_name)

    _log_notification(agent, level, message, sent_to)
    return results


def notify_error(agent: str, error: str, context: str = "") -> dict[str, bool]:
    """
    Send a formatted error alert.

    Parameters
    ----------
    agent:   Agent that encountered the error.
    error:   Error message or exception string.
    context: Optional additional context (stack trace, input data, etc.).
    """
    parts = [f"ERROR in agent *{agent}*", f"```{error}```"]
    if context:
        parts.append(f"Context:\n{context[:500]}")
    message = "\n".join(parts)
    return notify(message, level="error", agent=agent)


def notify_revenue(agent: str, amount: float, source: str) -> dict[str, bool]:
    """
    Send a revenue alert.

    Parameters
    ----------
    agent:  Agent that generated the revenue.
    amount: Dollar amount.
    source: Revenue source description (e.g. 'Gumroad sale', 'Affiliate click').
    """
    message = (
        f"Revenue event from *{agent}*\n"
        f"Amount : ${amount:,.2f}\n"
        f"Source : {source}"
    )
    return notify(message, level="revenue", agent=agent)


def notify_milestone(message: str) -> dict[str, bool]:
    """
    Send a milestone alert formatted with celebration context.

    Parameters
    ----------
    message: The milestone description.
    """
    full_message = f"MILESTONE REACHED\n\n{message}"
    return notify(full_message, level="milestone", agent="system")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def notification_history(limit: int = 50) -> list[dict[str, Any]]:
    """
    Return the most recent notifications from the log.

    Parameters
    ----------
    limit: Maximum number of records to return (newest first).

    Returns
    -------
    List of dicts with keys: id, agent, level, message, channels, sent_at.
    """
    db = _get_db()
    rows = db.execute(
        """
        SELECT id, agent, level, message, channels, sent_at
        FROM notification_log
        ORDER BY sent_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        try:
            ch_list = json.loads(row["channels"])
        except Exception:
            ch_list = []
        result.append({
            "id": row["id"],
            "agent": row["agent"],
            "level": row["level"],
            "message": row["message"],
            "channels": ch_list,
            "sent_at": row["sent_at"],
        })
    return result


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    results = notify("Test notification from AutoEarn", level="info", agent="test")
    print("send results:", results)
    results = notify_error("cfo", "ValueError: could not parse amount", context="Line 42")
    print("error results:", results)
    results = notify_revenue("affiliate", 49.99, "Gumroad product sale")
    print("revenue results:", results)
    history = notification_history(limit=5)
    print("recent history:", history)
