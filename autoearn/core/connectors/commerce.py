"""Commerce / monetization connectors.

Platforms the org actually collects money or lists products through: Gumroad,
Shopify, Stripe, and Lemon Squeezy. These let agents create products, read sales,
and reconcile revenue against the local ledger.
"""
from __future__ import annotations

import requests

from .base import Connector, ConnectorResult, register


@register
class GumroadConnector(Connector):
    name = "gumroad"
    label = "Gumroad"
    config_section = "gumroad"
    required_keys = ("access_token",)
    capabilities = ("sell", "read_sales")

    def list_products(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            "https://api.gumroad.com/v2/products",
            params={"access_token": cfg["access_token"]},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Gumroad error HTTP {resp.status_code}")
        products = resp.json().get("products", [])
        return ConnectorResult(True, f"{len(products)} products", data={"products": products})

    def sales_total(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            "https://api.gumroad.com/v2/sales",
            params={"access_token": cfg["access_token"]},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Gumroad error HTTP {resp.status_code}")
        sales = resp.json().get("sales", [])
        total = sum(float(s.get("price", 0)) / 100.0 for s in sales)
        return ConnectorResult(True, f"${total:.2f} across {len(sales)} sales", data={"total_usd": total})


@register
class ShopifyConnector(Connector):
    name = "shopify"
    label = "Shopify"
    config_section = "shopify"
    required_keys = ("store", "access_token")
    capabilities = ("sell", "read_sales")

    def _base(self, cfg: dict) -> str:
        return f"https://{cfg['store']}.myshopify.com/admin/api/2024-10"

    def create_product(self, title: str, body_html: str, price: float) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            self._base(cfg) + "/products.json",
            headers={"X-Shopify-Access-Token": cfg["access_token"], "Content-Type": "application/json"},
            json={"product": {"title": title, "body_html": body_html, "variants": [{"price": f"{price:.2f}"}]}},
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Shopify error HTTP {resp.status_code}: {resp.text[:160]}")
        product = resp.json().get("product", {})
        return ConnectorResult(True, f"Created Shopify product #{product.get('id')}", data=product)

    def revenue_today(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            self._base(cfg) + "/orders.json",
            headers={"X-Shopify-Access-Token": cfg["access_token"]},
            params={"status": "any", "financial_status": "paid"},
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Shopify error HTTP {resp.status_code}")
        orders = resp.json().get("orders", [])
        total = sum(float(o.get("total_price", 0)) for o in orders)
        return ConnectorResult(True, f"${total:.2f} across {len(orders)} paid orders", data={"total_usd": total})


@register
class StripeConnector(Connector):
    name = "stripe"
    label = "Stripe"
    config_section = "stripe"
    required_keys = ("secret_key",)
    capabilities = ("read_sales",)

    def balance(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            "https://api.stripe.com/v1/balance",
            auth=(cfg["secret_key"], ""),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Stripe error HTTP {resp.status_code}: {resp.text[:160]}")
        available = resp.json().get("available", [])
        total = sum(b.get("amount", 0) for b in available) / 100.0
        return ConnectorResult(True, f"Stripe available balance: ${total:.2f}", data={"available_usd": total})


@register
class LemonSqueezyConnector(Connector):
    name = "lemonsqueezy"
    label = "Lemon Squeezy"
    config_section = "lemonsqueezy"
    required_keys = ("api_key",)
    capabilities = ("read_sales",)

    def revenue(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            "https://api.lemonsqueezy.com/v1/orders",
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Accept": "application/vnd.api+json"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(False, f"Lemon Squeezy error HTTP {resp.status_code}")
        orders = resp.json().get("data", [])
        total = sum(o.get("attributes", {}).get("total", 0) for o in orders) / 100.0
        return ConnectorResult(True, f"${total:.2f} across {len(orders)} orders", data={"total_usd": total})
