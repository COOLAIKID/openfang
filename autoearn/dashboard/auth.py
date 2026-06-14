"""Tiny single-user sign-in for the dashboard.

Designed for "this is *my* money machine on a public URL". No database, no
sessions table, no extra dependencies:

  • The password comes from ``AUTOEARN_PASSWORD`` (env or config ``[server]``).
  • The session cookie is a stable HMAC of the password, so it survives restarts
    and you don't get logged out every deploy.
  • If no password is set, auth is OFF (handy for local LAN use). The cloud
    blueprint sets one, so a public deployment is always protected.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from core import config

COOKIE = "ae_session"
_PUBLIC_PREFIXES = ("/login", "/api/login", "/static/", "/manifest.webmanifest",
                    "/sw.js", "/favicon.ico", "/api/health")


def configured_password() -> str:
    """Return the configured password, or '' if sign-in is disabled."""
    return os.environ.get("AUTOEARN_PASSWORD") or config.get("server", "password", "") or ""


def auth_enabled() -> bool:
    return bool(configured_password())


def session_token() -> str:
    """A stable, unguessable token derived from the password."""
    pw = configured_password().encode("utf-8")
    return hmac.new(pw, b"autoearn-session-v1", hashlib.sha256).hexdigest()


def check_password(candidate: str) -> bool:
    pw = configured_password()
    if not pw:
        return True
    return hmac.compare_digest(candidate or "", pw)


def valid_cookie(value: str | None) -> bool:
    if not auth_enabled():
        return True
    return bool(value) and hmac.compare_digest(value, session_token())


def valid_bearer(authorization: str | None) -> bool:
    """Accept `Authorization: Bearer <session_token>` (used by home connectors)."""
    if not auth_enabled():
        return True
    if not authorization:
        return False
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return hmac.compare_digest(parts[1], session_token())
    return False


def is_public_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _PUBLIC_PREFIXES)
