"""Advertising platform connectors.

Platforms: Google Ads, Facebook/Meta Ads, Twitter/X Ads, Reddit Ads,
Pinterest Ads, Microsoft (Bing) Ads. Each exposes campaign-management and
reporting methods, returning :class:`ConnectorResult` instances.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from .base import Connector, ConnectorResult, register


# ---------------------------------------------------------------------------
# Google Ads
# ---------------------------------------------------------------------------

@register
class GoogleAdsConnector(Connector):
    name = "google_ads"
    label = "Google Ads"
    config_section = "google_ads"
    required_keys = ("customer_id", "developer_token", "access_token")
    capabilities = ("ads",)

    _BASE = "https://googleads.googleapis.com/v16"

    def _headers(self, cfg: dict) -> dict[str, str]:
        cid = cfg["customer_id"].replace("-", "")
        return {
            "Authorization": f"Bearer {cfg['access_token']}",
            "developer-token": cfg["developer_token"],
            "login-customer-id": cid,
            "Content-Type": "application/json",
        }

    def _cid(self, cfg: dict) -> str:
        return cfg["customer_id"].replace("-", "")

    def get_campaigns(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, "
            "campaign.advertising_channel_type, campaign_budget.amount_micros "
            "FROM campaign ORDER BY campaign.id LIMIT 100"
        )
        resp = requests.post(
            f"{self._BASE}/customers/{self._cid(cfg)}/googleAds:search",
            headers=self._headers(cfg),
            json={"query": query},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Google Ads get_campaigns error {resp.status_code}: {resp.text[:160]}",
            )
        results = resp.json().get("results", [])
        campaigns = [
            {
                "id": r.get("campaign", {}).get("id"),
                "name": r.get("campaign", {}).get("name"),
                "status": r.get("campaign", {}).get("status"),
                "budget_micros": r.get("campaignBudget", {}).get("amountMicros"),
            }
            for r in results
        ]
        return ConnectorResult(
            ok=True,
            message=f"Google Ads: {len(campaigns)} campaigns.",
            data={"campaigns": campaigns},
        )

    def get_campaign_stats(self, campaign_id: str, days: int = 30) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        query = (
            f"SELECT campaign.id, campaign.name, metrics.clicks, metrics.impressions, "
            f"metrics.cost_micros, metrics.conversions, metrics.ctr "
            f"FROM campaign "
            f"WHERE campaign.id = {campaign_id} "
            f"AND segments.date DURING LAST_{days}_DAYS"
        )
        resp = requests.post(
            f"{self._BASE}/customers/{self._cid(cfg)}/googleAds:search",
            headers=self._headers(cfg),
            json={"query": query},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Google Ads get_campaign_stats error {resp.status_code}: {resp.text[:160]}",
            )
        results = resp.json().get("results", [])
        if not results:
            return ConnectorResult(ok=False, message=f"Google Ads: no data for campaign {campaign_id}.")
        metrics = results[0].get("metrics", {})
        cost_usd = int(metrics.get("costMicros", 0)) / 1_000_000
        return ConnectorResult(
            ok=True,
            message=(
                f"Google Ads campaign {campaign_id}: "
                f"clicks={metrics.get('clicks')}, impressions={metrics.get('impressions')}, "
                f"cost=${cost_usd:.2f}"
            ),
            data={"metrics": metrics, "campaign_id": campaign_id},
        )

    def create_keyword_campaign(
        self,
        name: str,
        budget: float,
        keywords: list[str],
        url: str,
    ) -> ConnectorResult:
        """Create a keyword-targeted search campaign with a single ad group.

        This creates three resources in sequence: budget, campaign, ad group.
        Full ad creation (responsive search ads) requires additional calls.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        cid = self._cid(cfg)
        headers = self._headers(cfg)
        # Step 1: create budget
        budget_resp = requests.post(
            f"{self._BASE}/customers/{cid}/campaignBudgets:mutate",
            headers=headers,
            json={
                "operations": [{
                    "create": {
                        "name": f"{name} Budget",
                        "amountMicros": int(budget * 1_000_000),
                        "deliveryMethod": "STANDARD",
                    }
                }]
            },
            timeout=30,
        )
        if budget_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Google Ads create budget error {budget_resp.status_code}: {budget_resp.text[:160]}",
            )
        budget_resource = budget_resp.json()["results"][0]["resourceName"]
        # Step 2: create campaign
        camp_resp = requests.post(
            f"{self._BASE}/customers/{cid}/campaigns:mutate",
            headers=headers,
            json={
                "operations": [{
                    "create": {
                        "name": name,
                        "advertisingChannelType": "SEARCH",
                        "status": "PAUSED",
                        "campaignBudget": budget_resource,
                        "biddingStrategyType": "TARGET_CPA",
                    }
                }]
            },
            timeout=30,
        )
        if camp_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Google Ads create campaign error {camp_resp.status_code}: {camp_resp.text[:160]}",
            )
        campaign_resource = camp_resp.json()["results"][0]["resourceName"]
        campaign_id = campaign_resource.split("/")[-1]
        # Step 3: create ad group
        ag_resp = requests.post(
            f"{self._BASE}/customers/{cid}/adGroups:mutate",
            headers=headers,
            json={
                "operations": [{
                    "create": {
                        "name": f"{name} AdGroup",
                        "campaign": campaign_resource,
                        "status": "ENABLED",
                        "type": "SEARCH_STANDARD",
                        "cpcBidMicros": 1_000_000,
                    }
                }]
            },
            timeout=30,
        )
        if ag_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Google Ads create ad group error {ag_resp.status_code}: {ag_resp.text[:160]}",
            )
        ag_resource = ag_resp.json()["results"][0]["resourceName"]
        # Step 4: add keywords to ad group
        kw_ops = [
            {
                "create": {
                    "adGroup": ag_resource,
                    "status": "ENABLED",
                    "text": kw,
                    "matchType": "BROAD",
                }
            }
            for kw in keywords
        ]
        kw_resp = requests.post(
            f"{self._BASE}/customers/{cid}/adGroupCriteria:mutate",
            headers=headers,
            json={"operations": kw_ops},
            timeout=30,
        )
        kw_ok = kw_resp.status_code < 400
        return ConnectorResult(
            ok=True,
            message=(
                f"Google Ads campaign '{name}' created (id={campaign_id}), "
                f"{len(keywords)} keywords {'added' if kw_ok else 'failed to add'}."
            ),
            data={"campaign_id": campaign_id, "campaign_resource": campaign_resource},
        )

    def pause_campaign(self, campaign_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        cid = self._cid(cfg)
        resp = requests.post(
            f"{self._BASE}/customers/{cid}/campaigns:mutate",
            headers=self._headers(cfg),
            json={
                "operations": [{
                    "update": {
                        "resourceName": f"customers/{cid}/campaigns/{campaign_id}",
                        "status": "PAUSED",
                    },
                    "updateMask": "status",
                }]
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Google Ads pause_campaign error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Google Ads campaign {campaign_id} paused.",
            data=resp.json(),
        )

    def get_keywords_report(self, campaign_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        query = (
            f"SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
            f"metrics.clicks, metrics.impressions, metrics.cost_micros, metrics.average_cpc "
            f"FROM keyword_view "
            f"WHERE campaign.id = {campaign_id} "
            f"AND ad_group_criterion.status != 'REMOVED' "
            f"ORDER BY metrics.clicks DESC LIMIT 50"
        )
        resp = requests.post(
            f"{self._BASE}/customers/{self._cid(cfg)}/googleAds:search",
            headers=self._headers(cfg),
            json={"query": query},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Google Ads get_keywords_report error {resp.status_code}: {resp.text[:160]}",
            )
        results = resp.json().get("results", [])
        keywords = [
            {
                "text": r.get("adGroupCriterion", {}).get("keyword", {}).get("text"),
                "match_type": r.get("adGroupCriterion", {}).get("keyword", {}).get("matchType"),
                "clicks": r.get("metrics", {}).get("clicks"),
                "impressions": r.get("metrics", {}).get("impressions"),
                "cost_micros": r.get("metrics", {}).get("costMicros"),
            }
            for r in results
        ]
        return ConnectorResult(
            ok=True,
            message=f"Google Ads: {len(keywords)} keywords in campaign {campaign_id}.",
            data={"keywords": keywords},
        )


# ---------------------------------------------------------------------------
# Facebook / Meta Ads
# ---------------------------------------------------------------------------

@register
class FacebookAdsConnector(Connector):
    name = "facebook_ads"
    label = "Facebook Ads"
    config_section = "facebook_ads"
    required_keys = ("access_token", "ad_account_id")
    capabilities = ("ads",)

    _BASE = "https://graph.facebook.com/v19.0"

    def _account(self, cfg: dict) -> str:
        aid = cfg["ad_account_id"]
        return aid if aid.startswith("act_") else f"act_{aid}"

    def get_campaigns(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/{self._account(cfg)}/campaigns",
            params={
                "access_token": cfg["access_token"],
                "fields": "id,name,status,objective,daily_budget,lifetime_budget",
                "limit": 100,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Facebook Ads get_campaigns error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", [])
        return ConnectorResult(
            ok=True,
            message=f"Facebook Ads: {len(data)} campaigns.",
            data={"campaigns": data},
        )

    def get_campaign_insights(self, campaign_id: str, days: int = 7) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/{campaign_id}/insights",
            params={
                "access_token": cfg["access_token"],
                "fields": "impressions,clicks,spend,reach,ctr,cpc,cpp,actions",
                "date_preset": f"last_{days}_days",
                "level": "campaign",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Facebook Ads get_campaign_insights error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", [])
        if not data:
            return ConnectorResult(
                ok=True,
                message=f"Facebook Ads: no insights for campaign {campaign_id} in last {days} days.",
                data={},
            )
        insight = data[0]
        return ConnectorResult(
            ok=True,
            message=(
                f"Facebook Ads campaign {campaign_id}: "
                f"impressions={insight.get('impressions')}, "
                f"clicks={insight.get('clicks')}, spend=${insight.get('spend')}"
            ),
            data=insight,
        )

    def create_campaign(
        self,
        name: str,
        objective: str,
        budget: float,
        targeting: dict[str, Any] | None = None,
    ) -> ConnectorResult:
        """Create a Facebook Ads campaign.

        Args:
            name: campaign name.
            objective: Meta campaign objective e.g. 'LINK_CLICKS', 'CONVERSIONS'.
            budget: daily budget in cents (e.g. 1000 = $10.00).
            targeting: optional targeting spec dict.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        camp_resp = requests.post(
            f"{self._BASE}/{self._account(cfg)}/campaigns",
            params={"access_token": cfg["access_token"]},
            json={
                "name": name,
                "objective": objective,
                "status": "PAUSED",
                "special_ad_categories": [],
            },
            timeout=30,
        )
        if camp_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Facebook Ads create campaign error {camp_resp.status_code}: {camp_resp.text[:160]}",
            )
        campaign_id = camp_resp.json().get("id")
        # Create an ad set
        ad_set_payload: dict[str, Any] = {
            "name": f"{name} AdSet",
            "campaign_id": campaign_id,
            "daily_budget": int(budget * 100),
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "REACH",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "status": "PAUSED",
            "targeting": targeting or {"geo_locations": {"countries": ["US"]}},
        }
        as_resp = requests.post(
            f"{self._BASE}/{self._account(cfg)}/adsets",
            params={"access_token": cfg["access_token"]},
            json=ad_set_payload,
            timeout=30,
        )
        if as_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Facebook Ads create ad set error {as_resp.status_code}: {as_resp.text[:160]}",
            )
        ad_set_id = as_resp.json().get("id")
        return ConnectorResult(
            ok=True,
            message=f"Facebook Ads campaign '{name}' created (id={campaign_id}), ad set id={ad_set_id}.",
            data={"campaign_id": campaign_id, "ad_set_id": ad_set_id},
        )

    def pause_campaign(self, campaign_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/{campaign_id}",
            params={"access_token": cfg["access_token"]},
            json={"status": "PAUSED"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Facebook Ads pause_campaign error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Facebook Ads campaign {campaign_id} paused.",
            data=resp.json(),
        )


# ---------------------------------------------------------------------------
# Twitter / X Ads
# ---------------------------------------------------------------------------

@register
class TwitterAdsConnector(Connector):
    name = "twitter_ads"
    label = "Twitter/X Ads"
    config_section = "twitter_ads"
    required_keys = ("consumer_key", "consumer_secret", "access_token", "access_token_secret", "account_id")
    capabilities = ("ads",)

    _BASE = "https://ads-api.twitter.com/12"

    def _session(self, cfg: dict) -> requests.Session:
        """Build a requests Session with OAuth1 headers."""
        from requests_oauthlib import OAuth1  # type: ignore[import]
        sess = requests.Session()
        sess.auth = OAuth1(
            cfg["consumer_key"],
            cfg["consumer_secret"],
            cfg["access_token"],
            cfg["access_token_secret"],
        )
        return sess

    def get_campaigns(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            sess = self._session(cfg)
        except ImportError:
            return ConnectorResult(
                ok=False,
                message="Twitter Ads connector requires 'requests_oauthlib'. Install with: pip install requests-oauthlib",
            )
        resp = sess.get(
            f"{self._BASE}/accounts/{cfg['account_id']}/campaigns",
            params={"count": 100},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Twitter Ads get_campaigns error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", [])
        return ConnectorResult(
            ok=True,
            message=f"Twitter Ads: {len(data)} campaigns.",
            data={"campaigns": data},
        )

    def get_stats(self, campaign_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            sess = self._session(cfg)
        except ImportError:
            return ConnectorResult(
                ok=False,
                message="Twitter Ads connector requires 'requests_oauthlib'.",
            )
        resp = sess.get(
            f"{self._BASE}/stats/accounts/{cfg['account_id']}",
            params={
                "entity": "CAMPAIGN",
                "entity_ids": campaign_id,
                "metric_groups": "ENGAGEMENT,BILLING",
                "granularity": "DAY",
                "placement": "ALL_ON_TWITTER",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Twitter Ads get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", [])
        return ConnectorResult(
            ok=True,
            message=f"Twitter Ads stats for campaign {campaign_id} retrieved.",
            data={"stats": data},
        )

    def create_promoted_tweet(
        self,
        tweet_text: str,
        targeting: dict[str, Any] | None = None,
    ) -> ConnectorResult:
        """Create a tweet and promote it.

        Args:
            tweet_text: text for the tweet.
            targeting: optional targeting dict for the line item.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            sess = self._session(cfg)
        except ImportError:
            return ConnectorResult(
                ok=False,
                message="Twitter Ads connector requires 'requests_oauthlib'.",
            )
        # Step 1: create draft tweet
        tweet_resp = sess.post(
            "https://api.twitter.com/2/tweets",
            json={"text": tweet_text},
            timeout=30,
        )
        if tweet_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Twitter Ads create tweet error {tweet_resp.status_code}: {tweet_resp.text[:160]}",
            )
        tweet_id = tweet_resp.json().get("data", {}).get("id", "")
        return ConnectorResult(
            ok=True,
            message=f"Twitter Ads: tweet {tweet_id} created. Promote via Ads Manager.",
            data={"tweet_id": tweet_id, "targeting": targeting or {}},
        )


# ---------------------------------------------------------------------------
# Reddit Ads
# ---------------------------------------------------------------------------

@register
class RedditAdsConnector(Connector):
    name = "reddit_ads"
    label = "Reddit Ads"
    config_section = "reddit_ads"
    required_keys = ("client_id", "client_secret", "account_id")
    capabilities = ("ads",)

    _BASE = "https://ads-api.reddit.com/api/v2.1"
    _TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

    def _get_token(self, cfg: dict) -> str:
        resp = requests.post(
            self._TOKEN_URL,
            auth=(cfg["client_id"], cfg["client_secret"]),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": "AutoEarn/1.0"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "AutoEarn/1.0",
            "Content-Type": "application/json",
        }

    def get_campaigns(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Reddit Ads auth error: {exc}")
        resp = requests.get(
            f"{self._BASE}/campaigns",
            headers=self._headers(token),
            params={"account_id": cfg["account_id"], "limit": 100},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Reddit Ads get_campaigns error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", [])
        return ConnectorResult(
            ok=True,
            message=f"Reddit Ads: {len(data)} campaigns.",
            data={"campaigns": data},
        )

    def get_stats(self, campaign_id: str, days: int = 7) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Reddit Ads auth error: {exc}")
        resp = requests.get(
            f"{self._BASE}/campaigns/{campaign_id}/reports",
            headers=self._headers(token),
            params={
                "account_id": cfg["account_id"],
                "date_range": f"LAST_{days}_DAYS",
                "breakdown": "TOTAL",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Reddit Ads get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Reddit Ads campaign {campaign_id} stats retrieved.",
            data=data,
        )

    def create_campaign(
        self,
        name: str,
        subreddits: list[str],
        budget: float,
        content: dict[str, Any],
    ) -> ConnectorResult:
        """Create a Reddit Ads campaign.

        Args:
            name: campaign name.
            subreddits: list of subreddit names to target (without r/ prefix).
            budget: total budget in dollars.
            content: dict with keys 'headline', 'body', 'url', 'call_to_action'.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Reddit Ads auth error: {exc}")
        payload = {
            "account_id": cfg["account_id"],
            "name": name,
            "status": "PAUSED",
            "objective": "TRAFFIC",
            "total_budget_cents": int(budget * 100),
            "targeting": {
                "subreddits": subreddits,
                "devices": ["DESKTOP", "MOBILE"],
            },
            "creative": content,
        }
        resp = requests.post(
            f"{self._BASE}/campaigns",
            headers=self._headers(token),
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Reddit Ads create_campaign error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", {})
        return ConnectorResult(
            ok=True,
            message=f"Reddit Ads campaign '{name}' created (id={data.get('id')}).",
            data=data,
        )


# ---------------------------------------------------------------------------
# Pinterest Ads
# ---------------------------------------------------------------------------

@register
class PinterestAdsConnector(Connector):
    name = "pinterest_ads"
    label = "Pinterest Ads"
    config_section = "pinterest_ads"
    required_keys = ("access_token", "ad_account_id")
    capabilities = ("ads",)

    _BASE = "https://api.pinterest.com/v5"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['access_token']}",
            "Content-Type": "application/json",
        }

    def get_campaigns(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/ad_accounts/{cfg['ad_account_id']}/campaigns",
            headers=self._headers(cfg),
            params={"page_size": 100},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Pinterest Ads get_campaigns error {resp.status_code}: {resp.text[:160]}",
            )
        items = resp.json().get("items", [])
        return ConnectorResult(
            ok=True,
            message=f"Pinterest Ads: {len(items)} campaigns.",
            data={"campaigns": items},
        )

    def get_analytics(self, campaign_id: str, days: int = 7) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        import datetime

        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        resp = requests.get(
            f"{self._BASE}/ad_accounts/{cfg['ad_account_id']}/campaigns/analytics",
            headers=self._headers(cfg),
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "campaign_ids": campaign_id,
                "columns": "SPEND_IN_DOLLAR,IMPRESSION_1,CLICK_TYPE_1,TOTAL_CONVERSIONS",
                "granularity": "TOTAL",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Pinterest Ads get_analytics error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Pinterest Ads analytics for campaign {campaign_id} retrieved.",
            data=data,
        )

    def create_campaign(
        self,
        name: str,
        budget: float,
        targeting: dict[str, Any] | None = None,
    ) -> ConnectorResult:
        """Create a Pinterest Ads campaign.

        Args:
            name: campaign name.
            budget: daily budget in dollars.
            targeting: optional targeting spec.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/ad_accounts/{cfg['ad_account_id']}/campaigns",
            headers=self._headers(cfg),
            json={
                "name": name,
                "objective_type": "TRAFFIC",
                "status": "PAUSED",
                "daily_spend_cap": int(budget * 1_000_000),
                "order_line_type": "DIRECT",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Pinterest Ads create_campaign error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", [data])
        campaign_id = items[0].get("id") if items else None
        return ConnectorResult(
            ok=True,
            message=f"Pinterest Ads campaign '{name}' created (id={campaign_id}).",
            data={"campaign_id": campaign_id, "targeting": targeting or {}},
        )


# ---------------------------------------------------------------------------
# Microsoft Ads (Bing Ads)
# ---------------------------------------------------------------------------

@register
class MicrosoftAdsConnector(Connector):
    name = "microsoft_ads"
    label = "Microsoft Ads (Bing)"
    config_section = "microsoft_ads"
    required_keys = ("client_id", "client_secret", "refresh_token", "customer_id")
    capabilities = ("ads",)

    _TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    _BASE = "https://campaign.api.bingads.microsoft.com/CampaignManagement/v13"

    def _get_access_token(self, cfg: dict) -> str:
        resp = requests.post(
            self._TOKEN_URL,
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": cfg["refresh_token"],
                "grant_type": "refresh_token",
                "scope": "https://ads.microsoft.com/msads.manage offline_access",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _headers(self, access_token: str, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "CustomerId": cfg["customer_id"],
            "Content-Type": "application/json",
        }

    def get_campaigns(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_access_token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Microsoft Ads auth error: {exc}")
        # Use REST endpoint (Bing Ads REST is available for reporting; campaign management is SOAP-based;
        # using the Reporting API here as a practical fallback)
        resp = requests.post(
            "https://campaign.api.bingads.microsoft.com/CampaignManagement/v13/Campaigns/Query",
            headers=self._headers(token, cfg),
            json={"PageInfo": {"Index": 0, "Size": 100}},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Microsoft Ads get_campaigns error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        campaigns = data.get("Campaigns", [])
        return ConnectorResult(
            ok=True,
            message=f"Microsoft Ads: {len(campaigns)} campaigns.",
            data={"campaigns": campaigns},
        )

    def get_performance(self, campaign_id: str, days: int = 7) -> ConnectorResult:
        """Fetch performance report for a campaign.

        Args:
            campaign_id: Bing Ads campaign ID.
            days: look-back window in days.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            token = self._get_access_token(cfg)
        except Exception as exc:
            return ConnectorResult(ok=False, message=f"Microsoft Ads auth error: {exc}")
        import datetime

        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        # Submit report request
        report_payload = {
            "ReportRequest": {
                "ReportName": f"Campaign_{campaign_id}_Perf",
                "ReturnOnlyCompleteData": False,
                "Aggregation": "Daily",
                "Scope": {
                    "AccountIds": [cfg.get("account_id", "")],
                    "Campaigns": [{"CampaignId": campaign_id}],
                },
                "TimePeriod": {
                    "CustomDateRangeStart": {"Day": start.day, "Month": start.month, "Year": start.year},
                    "CustomDateRangeEnd": {"Day": end.day, "Month": end.month, "Year": end.year},
                },
                "Columns": ["CampaignName", "Impressions", "Clicks", "Spend", "AverageCpc", "Conversions"],
            }
        }
        resp = requests.post(
            "https://reporting.api.bingads.microsoft.com/Reporting/v13/GenerateReport/Submit",
            headers=self._headers(token, cfg),
            json=report_payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Microsoft Ads get_performance error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Microsoft Ads performance report submitted for campaign {campaign_id}.",
            data=data,
        )
