"""E-learning platform connectors for selling courses.

Platforms: Teachable, Kajabi, Thinkific, Udemy, Podia, Gumroad (extended).
Each exposes course/product management and revenue-reading methods,
returning :class:`ConnectorResult` instances.

Teachable, Thinkific, Udemy, Podia, and GumroadExtended conform to the
canonical AutoEarn connector spec (name, label, config_section,
required_keys, capabilities).  KajabiConnector is an additional platform not
in the base spec but useful when the key is available.
"""
from __future__ import annotations

import json
import datetime
from typing import Any

import requests

from .base import Connector, ConnectorResult, register


# ---------------------------------------------------------------------------
# Teachable
# ---------------------------------------------------------------------------

@register
class TeachableConnector(Connector):
    name = "teachable"
    label = "Teachable"
    config_section = "teachable"
    required_keys = ("api_key", "subdomain")
    capabilities = ("sell", "read_sales")

    def _base(self, cfg: dict) -> str:
        return f"https://{cfg['subdomain']}.teachable.com/api/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }

    def get_courses(self) -> ConnectorResult:
        """Fetch all courses from the Teachable school."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/courses",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Teachable get_courses error {resp.status_code}: {resp.text[:160]}",
            )
        courses = resp.json().get("courses", [])
        return ConnectorResult(
            ok=True,
            message=f"Teachable: {len(courses)} courses.",
            data={"courses": courses},
        )

    def get_course_enrollments(self, course_id: str) -> ConnectorResult:
        """Fetch enrollments for a specific course.

        Args:
            course_id: Teachable course ID.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/courses/{course_id}/enrollments",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Teachable get_course_enrollments error {resp.status_code}: {resp.text[:160]}",
            )
        enrollments = resp.json().get("enrollments", [])
        return ConnectorResult(
            ok=True,
            message=f"Teachable: {len(enrollments)} enrollments in course {course_id}.",
            data={"enrollments": enrollments},
        )

    def get_revenue(self, days: int = 30) -> ConnectorResult:
        """Fetch revenue report for the last N days.

        Args:
            days: number of days to look back (default 30).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/reports/revenue",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Teachable get_revenue error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Teachable revenue report retrieved.",
            data=data,
        )

    def create_coupon(
        self, course_id: str, discount_pct: int, code: str
    ) -> ConnectorResult:
        """Create a percentage-off coupon for a course.

        Args:
            course_id: Teachable course ID.
            discount_pct: discount percentage (0–100).
            code: coupon code string.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._base(cfg)}/coupons",
            headers=self._headers(cfg),
            json={
                "coupon": {
                    "course_id": course_id,
                    "discount_percent": discount_pct,
                    "code": code,
                }
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Teachable create_coupon error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Teachable coupon '{code}' ({discount_pct}% off) created for course {course_id}.",
            data=data,
        )


# ---------------------------------------------------------------------------
# Kajabi
# ---------------------------------------------------------------------------

@register
class KajabiConnector(Connector):
    name = "kajabi"
    label = "Kajabi"
    config_section = "kajabi"
    required_keys = ("api_key",)
    capabilities = ("elearning", "sell")

    _BASE = "https://kajabi.com/api/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_products(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/products",
            headers=self._headers(cfg),
            params={"page[size]": 50},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Kajabi get_products error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        products = data.get("data", data.get("products", []))
        return ConnectorResult(
            ok=True,
            message=f"Kajabi: {len(products)} products.",
            data={"products": products},
        )

    def get_product_stats(self, product_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/products/{product_id}",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Kajabi get_product_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", resp.json())
        return ConnectorResult(
            ok=True,
            message=f"Kajabi product {product_id} stats retrieved.",
            data=data,
        )

    def get_members(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/members",
            headers=self._headers(cfg),
            params={"page[size]": 100},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Kajabi get_members error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        members = data.get("data", data.get("members", []))
        return ConnectorResult(
            ok=True,
            message=f"Kajabi: {len(members)} members.",
            data={"members": members},
        )

    def get_revenue(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/purchases",
            headers=self._headers(cfg),
            params={"page[size]": 200},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Kajabi get_revenue error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        purchases = data.get("data", data.get("purchases", []))
        total = sum(
            float(p.get("attributes", p).get("amount", p.get("amount", 0))) / 100
            for p in purchases
        )
        return ConnectorResult(
            ok=True,
            message=f"Kajabi: ${total:.2f} revenue across {len(purchases)} purchases.",
            data={"total_usd": total, "purchases": purchases},
        )


# ---------------------------------------------------------------------------
# Thinkific
# ---------------------------------------------------------------------------

@register
class ThinkificConnector(Connector):
    name = "thinkific"
    label = "Thinkific"
    config_section = "thinkific"
    required_keys = ("api_key", "subdomain")
    capabilities = ("sell", "read_sales")

    _BASE = "https://api.thinkific.com/api/public/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "X-Auth-API-Key": cfg["api_key"],
            "X-Auth-Subdomain": cfg["subdomain"],
            "Content-Type": "application/json",
        }

    def get_courses(self) -> ConnectorResult:
        """Fetch all courses from the Thinkific school."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/courses",
            headers=self._headers(cfg),
            params={"page": 1, "limit": 100},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Thinkific get_courses error {resp.status_code}: {resp.text[:160]}",
            )
        items = resp.json().get("items", [])
        return ConnectorResult(
            ok=True,
            message=f"Thinkific: {len(items)} courses.",
            data={"courses": items},
        )

    def get_enrollments(self, course_id: str) -> ConnectorResult:
        """Fetch enrollments for a specific course.

        Args:
            course_id: Thinkific course ID.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/enrollments",
            headers=self._headers(cfg),
            params={"course_id": course_id, "page": 1, "limit": 200},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Thinkific get_enrollments error {resp.status_code}: {resp.text[:160]}",
            )
        items = resp.json().get("items", [])
        return ConnectorResult(
            ok=True,
            message=f"Thinkific: {len(items)} enrollments in course {course_id}.",
            data={"enrollments": items},
        )

    def get_revenue(self, days: int = 30) -> ConnectorResult:
        """Fetch orders placed in the last N days.

        Args:
            days: number of days to look back (default 30).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/orders",
            headers=self._headers(cfg),
            params={"page": 1, "limit": 200},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Thinkific get_revenue error {resp.status_code}: {resp.text[:160]}",
            )
        items = resp.json().get("items", [])
        return ConnectorResult(
            ok=True,
            message=f"Thinkific: {len(items)} orders retrieved.",
            data={"orders": items},
        )

    def create_coupon(
        self, course_id: str, code: str, discount_pct: int
    ) -> ConnectorResult:
        """Create a percentage-off coupon for a Thinkific course.

        Args:
            course_id: Thinkific course ID.
            code: coupon code string.
            discount_pct: discount percentage (0–100).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/coupons",
            headers=self._headers(cfg),
            json={
                "coupon": {
                    "course_id": course_id,
                    "coupon_code": code,
                    "discount_percent": discount_pct,
                }
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Thinkific create_coupon error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Thinkific coupon '{code}' ({discount_pct}% off) created for course {course_id}.",
            data=data,
        )


# ---------------------------------------------------------------------------
# Udemy
# ---------------------------------------------------------------------------

@register
class UdemyConnector(Connector):
    name = "udemy"
    label = "Udemy"
    config_section = "udemy"
    required_keys = ("client_id", "client_secret")
    capabilities = ("sell", "read_sales")

    _BASE = "https://www.udemy.com/api-2.0"

    def _auth(self, cfg: dict):
        return requests.auth.HTTPBasicAuth(cfg["client_id"], cfg["client_secret"])

    def get_courses(self) -> ConnectorResult:
        """Fetch all instructor courses from Udemy."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/courses/",
            auth=self._auth(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Udemy get_courses error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        results = data.get("results", [])
        return ConnectorResult(
            ok=True,
            message=f"Udemy: {len(results)} courses.",
            data={"courses": results},
        )

    def get_course_revenue(self, course_id: str, months: int = 1) -> ConnectorResult:
        """Fetch revenue report for a specific course.

        Args:
            course_id: Udemy course ID.
            months: number of months to look back (default 1).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/revenue-report/",
            auth=self._auth(cfg),
            params={"date_filter": "last_month", "course_id": course_id},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Udemy get_course_revenue error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Udemy revenue report for course {course_id} retrieved.",
            data=data,
        )

    def get_reviews(self, course_id: str) -> ConnectorResult:
        """Fetch reviews for a specific course.

        Args:
            course_id: Udemy course ID.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/courses/{course_id}/reviews/",
            auth=self._auth(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Udemy get_reviews error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        results = data.get("results", [])
        return ConnectorResult(
            ok=True,
            message=f"Udemy course {course_id}: {len(results)} reviews.",
            data={"reviews": results},
        )


# ---------------------------------------------------------------------------
# Podia
# ---------------------------------------------------------------------------

@register
class PodiaConnector(Connector):
    name = "podia"
    label = "Podia"
    config_section = "podia"
    required_keys = ("api_token",)
    capabilities = ("sell", "read_sales")

    _BASE = "https://api.podia.com/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['api_token']}",
            "Content-Type": "application/json",
        }

    def get_products(self) -> ConnectorResult:
        """Fetch all Podia products."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/products",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Podia get_products error {resp.status_code}: {resp.text[:160]}",
            )
        items = resp.json().get("data", [])
        return ConnectorResult(
            ok=True,
            message=f"Podia: {len(items)} products.",
            data={"products": items},
        )

    def get_sales(self, days: int = 30) -> ConnectorResult:
        """Fetch sales for the last N days.

        Args:
            days: number of days to look back (default 30).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/sales",
            headers=self._headers(cfg),
            params={"days": days},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Podia get_sales error {resp.status_code}: {resp.text[:160]}",
            )
        items = resp.json().get("data", [])
        return ConnectorResult(
            ok=True,
            message=f"Podia: {len(items)} sales in last {days} days.",
            data={"sales": items},
        )

    def get_subscribers(self) -> ConnectorResult:
        """Fetch all email subscribers."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/email-subscribers",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Podia get_subscribers error {resp.status_code}: {resp.text[:160]}",
            )
        items = resp.json().get("data", [])
        return ConnectorResult(
            ok=True,
            message=f"Podia: {len(items)} email subscribers.",
            data={"subscribers": items},
        )


# ---------------------------------------------------------------------------
# Gumroad (Extended)
# ---------------------------------------------------------------------------

@register
class GumroadExtendedConnector(Connector):
    """Extended Gumroad connector with full product management and per-product
    subscriber aggregation.

    Sits alongside the basic GumroadConnector in commerce.py and adds richer
    product creation, price updates, and subscriber listing.
    """

    name = "gumroad_extended"
    label = "Gumroad Extended"
    config_section = "gumroad"
    required_keys = ("access_token",)
    capabilities = ("sell", "read_sales", "create")

    _BASE = "https://api.gumroad.com/v2"

    def _params(self, cfg: dict, extra: dict | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {"access_token": cfg["access_token"]}
        if extra:
            p.update(extra)
        return p

    def get_products(self) -> ConnectorResult:
        """Fetch all Gumroad products."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/products",
            params=self._params(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Gumroad get_products error {resp.status_code}: {resp.text[:160]}",
            )
        products = resp.json().get("products", [])
        return ConnectorResult(
            ok=True,
            message=f"Gumroad: {len(products)} products.",
            data={"products": products},
        )

    def get_sales(self, after: str = "") -> ConnectorResult:
        """Fetch sales, optionally filtered to those after a given date.

        Args:
            after: ISO date string; only return sales after this date.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        params = self._params(cfg)
        if after:
            params["after"] = after
        resp = requests.get(
            f"{self._BASE}/sales",
            params=params,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Gumroad get_sales error {resp.status_code}: {resp.text[:160]}",
            )
        sales = resp.json().get("sales", [])
        return ConnectorResult(
            ok=True,
            message=f"Gumroad: {len(sales)} sales.",
            data={"sales": sales},
        )

    def create_product(
        self, name: str, price_cents: int, description: str
    ) -> ConnectorResult:
        """Create a new Gumroad product.

        Args:
            name: product name.
            price_cents: price in cents (e.g. 999 for $9.99).
            description: product description (supports markdown).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/products",
            params=self._params(cfg, {"name": name, "price": price_cents, "description": description}),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Gumroad create_product error {resp.status_code}: {resp.text[:160]}",
            )
        product = resp.json().get("product", {})
        url = product.get("short_url", product.get("url", ""))
        return ConnectorResult(
            ok=True,
            message=f"Gumroad product '{name}' created (id={product.get('id')}).",
            url=url,
            data=product,
        )

    def update_product_price(self, product_id: str, price_cents: int) -> ConnectorResult:
        """Update the price of an existing Gumroad product.

        Args:
            product_id: Gumroad product ID.
            price_cents: new price in cents.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.put(
            f"{self._BASE}/products/{product_id}",
            params=self._params(cfg, {"price": price_cents}),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Gumroad update_product_price error {resp.status_code}: {resp.text[:160]}",
            )
        product = resp.json().get("product", {})
        return ConnectorResult(
            ok=True,
            message=f"Gumroad product {product_id} price updated to {price_cents} cents.",
            data=product,
        )

    def get_subscribers(self) -> ConnectorResult:
        """Aggregate subscribers across all products.

        Iterates every product and fetches its subscriber list, then returns
        a combined list with a total count.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        products_result = self.get_products()
        if not products_result.ok:
            return products_result
        all_subscribers: list[Any] = []
        for product in products_result.data.get("products", []):
            product_id = product.get("id")
            if not product_id:
                continue
            resp = requests.get(
                f"{self._BASE}/products/{product_id}/subscribers",
                params=self._params(cfg),
                timeout=30,
            )
            if resp.status_code >= 400:
                continue
            subs = resp.json().get("subscribers", [])
            all_subscribers.extend(subs)
        return ConnectorResult(
            ok=True,
            message=f"Gumroad: {len(all_subscribers)} subscribers across all products.",
            data={"subscribers": all_subscribers, "total": len(all_subscribers)},
        )
