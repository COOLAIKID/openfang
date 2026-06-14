"""Social / messaging connectors.

Channels the org uses to distribute content and grow an audience: Telegram,
Reddit, Discord (webhook), Slack (webhook), Mastodon, and an X/Twitter stub.
Each exposes ``post(...)`` returning a :class:`ConnectorResult`.
"""
from __future__ import annotations

import requests

from .base import Connector, ConnectorResult, register


@register
class TelegramConnector(Connector):
    name = "telegram"
    label = "Telegram"
    config_section = "telegram"
    required_keys = ("bot_token", "channel_id")
    capabilities = ("post",)

    def post(self, message: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
            json={"chat_id": cfg["channel_id"], "text": message},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Telegram error HTTP {resp.status_code}: {resp.text[:160]}")
        return ConnectorResult(True, "Posted to Telegram.")


@register
class RedditConnector(Connector):
    name = "reddit"
    label = "Reddit"
    config_section = "reddit"
    required_keys = ("client_id", "client_secret", "username", "password")
    capabilities = ("post",)

    def post(self, subreddit: str, title: str, body: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        import praw

        reddit = praw.Reddit(
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            username=cfg["username"],
            password=cfg["password"],
            user_agent=cfg.get("user_agent", "autoearn/0.1"),
        )
        submission = reddit.subreddit(subreddit).submit(title=title, selftext=body)
        return ConnectorResult(True, f"Posted to r/{subreddit}: {submission.url}", url=submission.url)


@register
class DiscordConnector(Connector):
    name = "discord"
    label = "Discord"
    config_section = "discord"
    required_keys = ("webhook_url",)
    capabilities = ("post",)

    def post(self, message: str, username: str = "AutoEarn") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            cfg["webhook_url"],
            json={"content": message[:2000], "username": username},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Discord error HTTP {resp.status_code}: {resp.text[:160]}")
        return ConnectorResult(True, "Posted to Discord.")


@register
class SlackConnector(Connector):
    name = "slack"
    label = "Slack"
    config_section = "slack"
    required_keys = ("webhook_url",)
    capabilities = ("post",)

    def post(self, message: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(cfg["webhook_url"], json={"text": message}, timeout=30)
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Slack error HTTP {resp.status_code}: {resp.text[:160]}")
        return ConnectorResult(True, "Posted to Slack.")


@register
class MastodonConnector(Connector):
    name = "mastodon"
    label = "Mastodon"
    config_section = "mastodon"
    required_keys = ("instance_url", "access_token")
    capabilities = ("post",)

    def post(self, message: str, visibility: str = "public") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            cfg["instance_url"].rstrip("/") + "/api/v1/statuses",
            headers={"Authorization": f"Bearer {cfg['access_token']}"},
            data={"status": message[:500], "visibility": visibility},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Mastodon error HTTP {resp.status_code}: {resp.text[:160]}")
        url = resp.json().get("url", "")
        return ConnectorResult(True, f"Posted to Mastodon: {url}", url=url)


@register
class TwitterConnector(Connector):
    name = "twitter"
    label = "X / Twitter"
    config_section = "twitter"
    required_keys = ("bearer_token",)
    capabilities = ("post",)

    def post(self, message: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        # v2 tweet creation requires OAuth1.0a user context; bearer alone is
        # read-only. We surface a clear message rather than failing opaquely.
        resp = requests.post(
            "https://api.twitter.com/2/tweets",
            headers={"Authorization": f"Bearer {cfg['bearer_token']}", "Content-Type": "application/json"},
            json={"text": message[:280]},
            timeout=30,
        )
        if resp.status_code == 403:
            return ConnectorResult(False, "X/Twitter: posting needs OAuth1.0a user context, not just a bearer token.")
        if resp.status_code >= 400:
            return ConnectorResult(False, f"X/Twitter error HTTP {resp.status_code}: {resp.text[:160]}")
        tid = resp.json().get("data", {}).get("id", "")
        return ConnectorResult(True, f"Posted to X: {tid}", url=f"https://x.com/i/web/status/{tid}")
