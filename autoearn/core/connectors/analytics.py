"""Analytics platform connectors.

Platforms: Google Analytics 4, Plausible, Fathom, PostHog, Mixpanel, Umami.
Each exposes read methods for sessions, page views, referrers, events, and
revenue — returning :class:`ConnectorResult` instances.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from .base import Connector, ConnectorResult, register


# ---------------------------------------------------------------------------
# Google Analytics 4
# ---------------------------------------------------------------------------

@register
class GoogleAnalyticsConnector(Connector):
    name = "google_analytics"
    label = "Google Analytics 4"
    config_section = "google_analytics"
    required_keys = ("property_id", "credentials_json")
    capabilities = ("analytics",)

    def _get_access_token(self, credentials_path: str) -> str:
        """Obtain a short-lived access token from a service account JSON file."""
        import base64
        import hashlib
        import hmac as _hmac
        import json as _json

        with open(credentials_path) as fh:
            sa = _json.load(fh)

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": sa["client_email"],
            "sub": sa["client_email"],
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
            "scope": "https://www.googleapis.com/auth/analytics.readonly",
        }

        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        seg = b64url(_json.dumps(header).encode()) + "." + b64url(_json.dumps(payload).encode())
        # Use cryptography library if available, otherwise fall back to subprocess
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            private_key = serialization.load_pem_private_key(
                sa["private_key"].encode(), password=None
            )
            signature = private_key.sign(seg.encode(), padding.PKCS1v15(), hashes.SHA256())
            jwt_token = seg + "." + b64url(signature)
        except ImportError:
            raise RuntimeError(
                "Google Analytics connector requires the 'cryptography' package. "
                "Install with: pip install cryptography"
            )

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _run_report(self, token: str, property_id: str, body: dict) -> dict:
        resp = requests.post(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"GA4 API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def get_sessions(self, days: int = 7) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_access_token(cfg["credentials_json"])
            data = self._run_report(token, cfg["property_id"], {
                "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
                "metrics": [{"name": "sessions"}, {"name": "activeUsers"}, {"name": "bounceRate"}],
            })
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Google Analytics get_sessions error: {exc}")
        rows = data.get("rows", [])
        total_sessions = int(rows[0]["metricValues"][0]["value"]) if rows else 0
        return ConnectorResult(
            ok=True,
            message=f"GA4: {total_sessions} sessions in last {days} days.",
            data=data,
        )

    def get_top_pages(self, limit: int = 10) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_access_token(cfg["credentials_json"])
            data = self._run_report(token, cfg["property_id"], {
                "dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}],
                "dimensions": [{"name": "pagePath"}],
                "metrics": [{"name": "screenPageViews"}, {"name": "sessions"}],
                "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
                "limit": limit,
            })
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Google Analytics get_top_pages error: {exc}")
        rows = data.get("rows", [])
        pages = [
            {
                "path": r["dimensionValues"][0]["value"],
                "views": int(r["metricValues"][0]["value"]),
                "sessions": int(r["metricValues"][1]["value"]),
            }
            for r in rows
        ]
        return ConnectorResult(
            ok=True,
            message=f"GA4: top {len(pages)} pages retrieved.",
            data={"pages": pages},
        )

    def get_traffic_sources(self, days: int = 7) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_access_token(cfg["credentials_json"])
            data = self._run_report(token, cfg["property_id"], {
                "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
                "dimensions": [{"name": "sessionDefaultChannelGrouping"}],
                "metrics": [{"name": "sessions"}, {"name": "activeUsers"}],
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
            })
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Google Analytics get_traffic_sources error: {exc}")
        rows = data.get("rows", [])
        sources = [
            {
                "channel": r["dimensionValues"][0]["value"],
                "sessions": int(r["metricValues"][0]["value"]),
                "users": int(r["metricValues"][1]["value"]),
            }
            for r in rows
        ]
        return ConnectorResult(
            ok=True,
            message=f"GA4: {len(sources)} traffic channels in last {days} days.",
            data={"sources": sources},
        )

    def get_events(self, event_name: str, days: int = 7) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_access_token(cfg["credentials_json"])
            data = self._run_report(token, cfg["property_id"], {
                "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
                "dimensions": [{"name": "eventName"}],
                "metrics": [{"name": "eventCount"}, {"name": "eventCountPerUser"}],
                "dimensionFilter": {
                    "filter": {
                        "fieldName": "eventName",
                        "stringFilter": {"value": event_name, "matchType": "EXACT"},
                    }
                },
            })
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Google Analytics get_events error: {exc}")
        rows = data.get("rows", [])
        count = int(rows[0]["metricValues"][0]["value"]) if rows else 0
        return ConnectorResult(
            ok=True,
            message=f"GA4: event '{event_name}' fired {count} times in last {days} days.",
            data=data,
        )

    def get_revenue(self, days: int = 30) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_access_token(cfg["credentials_json"])
            data = self._run_report(token, cfg["property_id"], {
                "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
                "metrics": [
                    {"name": "purchaseRevenue"},
                    {"name": "transactions"},
                    {"name": "averagePurchaseRevenue"},
                ],
            })
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Google Analytics get_revenue error: {exc}")
        rows = data.get("rows", [])
        revenue = float(rows[0]["metricValues"][0]["value"]) if rows else 0.0
        return ConnectorResult(
            ok=True,
            message=f"GA4: ${revenue:.2f} revenue in last {days} days.",
            data=data,
        )


# ---------------------------------------------------------------------------
# Plausible
# ---------------------------------------------------------------------------

@register
class PlausibleConnector(Connector):
    name = "plausible"
    label = "Plausible Analytics"
    config_section = "plausible"
    required_keys = ("api_key", "site_id")
    capabilities = ("analytics",)

    def _base(self, cfg: dict) -> str:
        host = cfg.get("host", "plausible.io").rstrip("/")
        return f"https://{host}/api/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {"Authorization": f"Bearer {cfg['api_key']}"}

    def get_stats(self, period: str = "7d") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/stats/aggregate",
            headers=self._headers(cfg),
            params={
                "site_id": cfg["site_id"],
                "period": period,
                "metrics": "visitors,visits,pageviews,bounce_rate,visit_duration",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Plausible get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        results = data.get("results", {})
        visitors = results.get("visitors", {}).get("value", 0)
        return ConnectorResult(
            ok=True,
            message=f"Plausible: {visitors} visitors in period {period}.",
            data=results,
        )

    def get_top_pages(self, limit: int = 10, period: str = "7d") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/stats/breakdown",
            headers=self._headers(cfg),
            params={
                "site_id": cfg["site_id"],
                "period": period,
                "property": "event:page",
                "metrics": "visitors,pageviews,bounce_rate,visit_duration",
                "limit": limit,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Plausible get_top_pages error {resp.status_code}: {resp.text[:160]}",
            )
        results = resp.json().get("results", [])
        return ConnectorResult(
            ok=True,
            message=f"Plausible: {len(results)} top pages.",
            data={"pages": results},
        )

    def get_referrers(self, period: str = "7d") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/stats/breakdown",
            headers=self._headers(cfg),
            params={
                "site_id": cfg["site_id"],
                "period": period,
                "property": "visit:referrer",
                "metrics": "visitors,visits,bounce_rate",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Plausible get_referrers error {resp.status_code}: {resp.text[:160]}",
            )
        results = resp.json().get("results", [])
        return ConnectorResult(
            ok=True,
            message=f"Plausible: {len(results)} referrers in period {period}.",
            data={"referrers": results},
        )

    def get_goals(self, period: str = "7d") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/stats/breakdown",
            headers=self._headers(cfg),
            params={
                "site_id": cfg["site_id"],
                "period": period,
                "property": "event:goal",
                "metrics": "visitors,events,conversion_rate",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Plausible get_goals error {resp.status_code}: {resp.text[:160]}",
            )
        results = resp.json().get("results", [])
        return ConnectorResult(
            ok=True,
            message=f"Plausible: {len(results)} goal conversions in period {period}.",
            data={"goals": results},
        )


# ---------------------------------------------------------------------------
# Fathom
# ---------------------------------------------------------------------------

@register
class FathomConnector(Connector):
    name = "fathom"
    label = "Fathom Analytics"
    config_section = "fathom"
    required_keys = ("api_key", "site_id")
    capabilities = ("analytics",)

    _BASE = "https://api.usefathom.com/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {"Authorization": f"Bearer {cfg['api_key']}"}

    def get_aggregations(self, date_from: str, date_to: str) -> ConnectorResult:
        """Fetch aggregated stats for a date range.

        Args:
            date_from: ISO date string e.g. '2024-01-01'.
            date_to: ISO date string e.g. '2024-01-31'.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/aggregations",
            headers=self._headers(cfg),
            params={
                "entity": "pageview",
                "entity_id": cfg["site_id"],
                "aggregates": "visits,uniques,pageviews,avg_duration,bounce_rate",
                "date_grouping": "day",
                "date_from": date_from,
                "date_to": date_to,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Fathom get_aggregations error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Fathom: aggregations from {date_from} to {date_to} retrieved.",
            data={"aggregations": data},
        )

    def get_top_pages(self, period: str = "week") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/aggregations",
            headers=self._headers(cfg),
            params={
                "entity": "pageview",
                "entity_id": cfg["site_id"],
                "aggregates": "pageviews,uniques",
                "field_grouping": "pathname",
                "sort_by": "pageviews:desc",
                "date_grouping": period,
                "limit": 20,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Fathom get_top_pages error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Fathom: top pages for period '{period}' retrieved.",
            data={"pages": data},
        )

    def get_referrers(self, period: str = "week") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/aggregations",
            headers=self._headers(cfg),
            params={
                "entity": "pageview",
                "entity_id": cfg["site_id"],
                "aggregates": "visits,uniques",
                "field_grouping": "referrer_hostname",
                "sort_by": "visits:desc",
                "date_grouping": period,
                "limit": 20,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Fathom get_referrers error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Fathom: referrers for period '{period}' retrieved.",
            data={"referrers": data},
        )


# ---------------------------------------------------------------------------
# PostHog
# ---------------------------------------------------------------------------

@register
class PostHogConnector(Connector):
    name = "posthog"
    label = "PostHog"
    config_section = "posthog"
    required_keys = ("api_key", "project_id")
    capabilities = ("analytics",)

    def _base(self, cfg: dict) -> str:
        host = cfg.get("host", "app.posthog.com").rstrip("/")
        if not host.startswith("http"):
            host = f"https://{host}"
        return host

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }

    def capture_event(
        self, distinct_id: str, event: str, properties: dict[str, Any] | None = None
    ) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        payload: dict[str, Any] = {
            "api_key": cfg["api_key"],
            "event": event,
            "distinct_id": distinct_id,
            "properties": properties or {},
        }
        resp = requests.post(
            f"{self._base(cfg)}/capture/",
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"PostHog capture_event error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"PostHog: event '{event}' captured for {distinct_id}.",
            data=resp.json() if resp.text else {},
        )

    def get_insight(self, insight_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/api/projects/{cfg['project_id']}/insights/{insight_id}/",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"PostHog get_insight error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"PostHog insight {insight_id} retrieved.",
            data=data,
        )

    def create_action(self, name: str, steps: list[dict]) -> ConnectorResult:
        """Create a PostHog action.

        Args:
            name: action name.
            steps: list of step dicts e.g. [{"event": "$pageview", "url": "/checkout"}].
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._base(cfg)}/api/projects/{cfg['project_id']}/actions/",
            headers=self._headers(cfg),
            json={"name": name, "steps": steps},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"PostHog create_action error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"PostHog action '{name}' created (id={data.get('id')}).",
            data=data,
        )


# ---------------------------------------------------------------------------
# Mixpanel
# ---------------------------------------------------------------------------

@register
class MixpanelConnector(Connector):
    name = "mixpanel"
    label = "Mixpanel"
    config_section = "mixpanel"
    required_keys = ("api_secret", "project_token")
    capabilities = ("analytics",)

    _INGEST = "https://api.mixpanel.com"
    _DATA = "https://data.mixpanel.com/api/2.0"

    def track_event(self, event: str, properties: dict[str, Any] | None = None) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        props = properties or {}
        props["token"] = cfg["project_token"]
        props.setdefault("time", int(time.time()))
        props.setdefault("distinct_id", "autoearn")
        resp = requests.post(
            f"{self._INGEST}/track",
            json=[{"event": event, "properties": props}],
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mixpanel track_event error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Mixpanel: event '{event}' tracked.",
            data={"response": resp.text},
        )

    def get_funnel(self, funnel_id: int, from_date: str, to_date: str) -> ConnectorResult:
        """Fetch funnel conversion data.

        Args:
            funnel_id: numeric Mixpanel funnel ID.
            from_date: ISO date string e.g. '2024-01-01'.
            to_date: ISO date string e.g. '2024-01-31'.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._DATA}/funnels/",
            auth=(cfg["api_secret"], ""),
            params={
                "funnel_id": funnel_id,
                "from_date": from_date,
                "to_date": to_date,
                "unit": "day",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mixpanel get_funnel error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Mixpanel funnel {funnel_id} data retrieved.",
            data=data,
        )

    def get_retention(self, from_date: str, to_date: str) -> ConnectorResult:
        """Fetch retention data.

        Args:
            from_date: ISO date string e.g. '2024-01-01'.
            to_date: ISO date string e.g. '2024-01-31'.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._DATA}/retention/",
            auth=(cfg["api_secret"], ""),
            params={
                "from_date": from_date,
                "to_date": to_date,
                "retention_type": "birth",
                "unit": "week",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mixpanel get_retention error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Mixpanel retention data from {from_date} to {to_date} retrieved.",
            data=data,
        )


# ---------------------------------------------------------------------------
# Umami
# ---------------------------------------------------------------------------

@register
class UmamiConnector(Connector):
    name = "umami"
    label = "Umami Analytics"
    config_section = "umami"
    required_keys = ("server_url", "username", "password", "website_id")
    capabilities = ("analytics",)

    def _token(self, cfg: dict) -> str:
        resp = requests.post(
            cfg["server_url"].rstrip("/") + "/api/auth/login",
            json={"username": cfg["username"], "password": cfg["password"]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["token"]

    def get_stats(self, start: int, end: int) -> ConnectorResult:
        """Fetch aggregate stats.

        Args:
            start: Unix timestamp in milliseconds.
            end: Unix timestamp in milliseconds.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Umami auth error: {exc}")
        resp = requests.get(
            cfg["server_url"].rstrip("/") + f"/api/websites/{cfg['website_id']}/stats",
            headers={"Authorization": f"Bearer {token}"},
            params={"startAt": start, "endAt": end},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Umami get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        visitors = data.get("visitors", {}).get("value", 0)
        return ConnectorResult(
            ok=True,
            message=f"Umami: {visitors} visitors in timeframe.",
            data=data,
        )

    def get_pageviews(self, start: int, end: int, unit: str = "day") -> ConnectorResult:
        """Fetch pageview time series.

        Args:
            start: Unix timestamp in milliseconds.
            end: Unix timestamp in milliseconds.
            unit: grouping unit — 'year', 'month', 'day', 'hour'.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Umami auth error: {exc}")
        resp = requests.get(
            cfg["server_url"].rstrip("/") + f"/api/websites/{cfg['website_id']}/pageviews",
            headers={"Authorization": f"Bearer {token}"},
            params={"startAt": start, "endAt": end, "unit": unit, "timezone": "UTC"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Umami get_pageviews error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        pageviews = data.get("pageviews", [])
        return ConnectorResult(
            ok=True,
            message=f"Umami: {len(pageviews)} data points retrieved.",
            data=data,
        )

    def get_referrers(self, start: int, end: int) -> ConnectorResult:
        """Fetch top referrers.

        Args:
            start: Unix timestamp in milliseconds.
            end: Unix timestamp in milliseconds.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Umami auth error: {exc}")
        resp = requests.get(
            cfg["server_url"].rstrip("/") + f"/api/websites/{cfg['website_id']}/metrics",
            headers={"Authorization": f"Bearer {token}"},
            params={"startAt": start, "endAt": end, "type": "referrer"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Umami get_referrers error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Umami: {len(data)} referrers retrieved.",
            data={"referrers": data},
        )
