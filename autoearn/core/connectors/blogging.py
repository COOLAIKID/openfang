"""Blogging / publishing connectors.

Platforms an agent can publish long-form content to: WordPress, Ghost, Dev.to,
Hashnode, Medium. Each implements ``publish(title, body, **opts)`` returning a
:class:`ConnectorResult`. Bodies are markdown unless a platform requires HTML.
"""
from __future__ import annotations

import requests

from .base import Connector, ConnectorResult, register


@register
class WordPressConnector(Connector):
    name = "wordpress"
    label = "WordPress"
    config_section = "wordpress"
    required_keys = ("url", "username", "password")
    capabilities = ("publish",)

    def publish(self, title: str, body: str, status: str = "publish") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            cfg["url"].rstrip("/") + "/wp-json/wp/v2/posts",
            auth=(cfg["username"], cfg.get("password", "")),
            json={"title": title, "content": body, "status": status},
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"WordPress error HTTP {resp.status_code}: {resp.text[:160]}")
        link = resp.json().get("link", "")
        return ConnectorResult(True, f"Published to WordPress: {link}", url=link)


@register
class GhostConnector(Connector):
    name = "ghost"
    label = "Ghost"
    config_section = "ghost"
    required_keys = ("admin_api_url", "admin_api_key")
    capabilities = ("publish",)

    def _token(self, api_key: str) -> str:
        import base64
        import hashlib
        import hmac
        import json as _json
        import time

        key_id, secret = api_key.split(":")
        header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
        now = int(time.time())
        payload = {"iat": now, "exp": now + 300, "aud": "/admin/"}

        def b64(data: bytes) -> bytes:
            return base64.urlsafe_b64encode(data).rstrip(b"=")

        segments = [b64(_json.dumps(header).encode()), b64(_json.dumps(payload).encode())]
        signing_input = b".".join(segments)
        signature = hmac.new(bytes.fromhex(secret), signing_input, hashlib.sha256).digest()
        segments.append(b64(signature))
        return b".".join(segments).decode()

    def publish(self, title: str, body: str, status: str = "published") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._token(cfg["admin_api_key"])
        except (ValueError, TypeError) as exc:
            return ConnectorResult(False, f"Ghost: bad admin_api_key format: {exc}")
        resp = requests.post(
            cfg["admin_api_url"].rstrip("/") + "/ghost/api/admin/posts/?source=html",
            headers={"Authorization": f"Ghost {token}"},
            json={"posts": [{"title": title, "html": body, "status": status}]},
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Ghost error HTTP {resp.status_code}: {resp.text[:160]}")
        url = resp.json().get("posts", [{}])[0].get("url", "")
        return ConnectorResult(True, f"Published to Ghost: {url}", url=url)


@register
class DevToConnector(Connector):
    name = "devto"
    label = "Dev.to"
    config_section = "devto"
    required_keys = ("api_key",)
    capabilities = ("publish",)

    def publish(self, title: str, body: str, published: bool = True, tags: list | None = None) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            "https://dev.to/api/articles",
            headers={"api-key": cfg["api_key"], "Content-Type": "application/json"},
            json={"article": {"title": title, "body_markdown": body, "published": published, "tags": tags or []}},
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Dev.to error HTTP {resp.status_code}: {resp.text[:160]}")
        url = resp.json().get("url", "")
        return ConnectorResult(True, f"Published to Dev.to: {url}", url=url)


@register
class HashnodeConnector(Connector):
    name = "hashnode"
    label = "Hashnode"
    config_section = "hashnode"
    required_keys = ("api_key", "publication_id")
    capabilities = ("publish",)

    def publish(self, title: str, body: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        query = """
        mutation Publish($input: PublishPostInput!) {
          publishPost(input: $input) { post { url } }
        }"""
        variables = {
            "input": {
                "title": title,
                "contentMarkdown": body,
                "publicationId": cfg["publication_id"],
            }
        }
        resp = requests.post(
            "https://gql.hashnode.com/",
            headers={"Authorization": cfg["api_key"], "Content-Type": "application/json"},
            json={"query": query, "variables": variables},
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Hashnode error HTTP {resp.status_code}: {resp.text[:160]}")
        data = resp.json()
        url = (data.get("data") or {}).get("publishPost", {}).get("post", {}).get("url", "")
        if not url:
            return ConnectorResult(False, f"Hashnode: {data.get('errors', 'unknown error')}")
        return ConnectorResult(True, f"Published to Hashnode: {url}", url=url)


@register
class MediumConnector(Connector):
    name = "medium"
    label = "Medium"
    config_section = "medium"
    required_keys = ("token",)
    capabilities = ("publish",)

    def publish(self, title: str, body: str, status: str = "public") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        me = requests.get(
            "https://api.medium.com/v1/me",
            headers={"Authorization": f"Bearer {cfg['token']}"},
            timeout=30,
        )
        if me.status_code >= 400:
            return ConnectorResult(False, f"Medium auth error HTTP {me.status_code}")
        user_id = me.json()["data"]["id"]
        resp = requests.post(
            f"https://api.medium.com/v1/users/{user_id}/posts",
            headers={"Authorization": f"Bearer {cfg['token']}"},
            json={"title": title, "contentFormat": "markdown", "content": body, "publishStatus": status},
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Medium error HTTP {resp.status_code}: {resp.text[:160]}")
        url = resp.json()["data"]["url"]
        return ConnectorResult(True, f"Published to Medium: {url}", url=url)
