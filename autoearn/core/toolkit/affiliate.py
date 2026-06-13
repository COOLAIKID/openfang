"""Affiliate marketing research and tools.

Provides helpers for finding affiliate programs, generating review content,
estimating revenue, and scraping marketplaces.

All public functions return JSON strings.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

import requests

OUTPUT_ROOT = Path(__file__).resolve().parent.parent.parent / "output"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None,
         headers: dict | None = None, timeout: int = 15) -> requests.Response:
    h = headers or _HEADERS
    return requests.get(url, headers=h, params=params, timeout=timeout)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


# ---------------------------------------------------------------------------
# 1. amazon_search_products
# ---------------------------------------------------------------------------

def amazon_search_products(
    keywords: str,
    category: str = "All",
    max_results: int = 10,
) -> str:
    """Scrape Amazon search results.

    Returns JSON list of {asin, title, price, rating, review_count, url}.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return _err("BeautifulSoup4 not installed. Run: pip install beautifulsoup4")

    cat_map = {
        "All": "aps", "Electronics": "electronics", "Books": "stripbooks",
        "Clothing": "apparel", "Home": "garden", "Toys": "toys-and-games",
        "Sports": "sporting", "Health": "hpc", "Software": "software",
    }
    search_index = cat_map.get(category, "aps")

    params = {"k": keywords, "i": search_index, "ref": "nb_sb_noss"}
    headers = {
        **_HEADERS,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip, deflate, br",
    }
    try:
        resp = _get("https://www.amazon.com/s", params=params, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Amazon search failed: {exc}")

    results: list[dict] = []
    cards = soup.select('[data-component-type="s-search-result"]')
    for card in cards[:max_results]:
        asin = card.get("data-asin", "")
        title_el = card.select_one("h2 a span")
        title = title_el.get_text(strip=True) if title_el else ""

        price_whole = card.select_one(".a-price-whole")
        price_frac = card.select_one(".a-price-fraction")
        price = ""
        if price_whole:
            price = price_whole.get_text(strip=True).rstrip(".")
            if price_frac:
                price += "." + price_frac.get_text(strip=True)

        rating_el = card.select_one(".a-icon-star-small .a-icon-alt")
        rating = rating_el.get_text(strip=True).split(" ")[0] if rating_el else ""

        review_el = card.select_one('[aria-label*="stars"] + span')
        if not review_el:
            review_el = card.select_one(".a-size-base.s-underline-text")
        review_count = review_el.get_text(strip=True).replace(",", "") if review_el else "0"

        if asin and title:
            results.append({
                "asin": asin,
                "title": title,
                "price": price,
                "rating": rating,
                "review_count": review_count,
                "url": f"https://www.amazon.com/dp/{asin}",
            })

    return json.dumps(results)


# ---------------------------------------------------------------------------
# 2. amazon_affiliate_link
# ---------------------------------------------------------------------------

def amazon_affiliate_link(asin: str, tag: str) -> str:
    """Generate an Amazon affiliate link for a given ASIN and tag.

    Returns JSON {asin, tag, affiliate_url}.
    """
    url = f"https://www.amazon.com/dp/{asin}?tag={tag}"
    return json.dumps({"asin": asin, "tag": tag, "affiliate_url": url})


# ---------------------------------------------------------------------------
# 3. amazon_bestsellers
# ---------------------------------------------------------------------------

def amazon_bestsellers(
    category_url: str = "https://www.amazon.com/Best-Sellers/zgbs",
) -> str:
    """Scrape Amazon Best Sellers page.

    Returns JSON list of top 20 {title, asin, rank, price, rating}.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return _err("BeautifulSoup4 not installed.")

    try:
        resp = _get(category_url)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to fetch Amazon bestsellers: {exc}")

    results: list[dict] = []
    items = soup.select(".zg-bestseller-items .zg-item-immersion, #zg-ordered-list .zg-item-immersion")
    if not items:
        items = soup.select("[data-p13n-asin-metadata]")

    for i, item in enumerate(items[:20], start=1):
        asin = ""
        asin_data = item.get("data-p13n-asin-metadata", "")
        if asin_data:
            try:
                asin = json.loads(asin_data).get("asin", "")
            except (json.JSONDecodeError, AttributeError):
                pass

        if not asin:
            link = item.select_one("a[href*='/dp/']")
            if link:
                m = re.search(r"/dp/([A-Z0-9]{10})", link.get("href", ""))
                if m:
                    asin = m.group(1)

        title_el = item.select_one(".p13n-sc-truncated, .a-size-small.a-link-normal")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = item.select_one(".p13n-sc-price, .a-price .a-offscreen")
        price = price_el.get_text(strip=True) if price_el else ""

        rating_el = item.select_one(".a-icon-star-small .a-icon-alt, .a-icon-star .a-icon-alt")
        rating = rating_el.get_text(strip=True).split(" ")[0] if rating_el else ""

        rank_el = item.select_one(".zg-badge-text, .aok-inline-block.zg-badge-wrapper")
        rank_str = rank_el.get_text(strip=True).lstrip("#") if rank_el else str(i)

        if title or asin:
            results.append({
                "title": title,
                "asin": asin,
                "rank": rank_str or str(i),
                "price": price,
                "rating": rating,
                "url": f"https://www.amazon.com/dp/{asin}" if asin else "",
            })

    return json.dumps(results)


# ---------------------------------------------------------------------------
# 4. clickbank_marketplace
# ---------------------------------------------------------------------------

def clickbank_marketplace(category: str = "", sort_by: str = "gravity") -> str:
    """Scrape or browse the ClickBank public marketplace directory.

    Returns JSON list of {title, vendor, commission_pct, gravity, avg_sale, url}.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return _err("BeautifulSoup4 not installed.")

    params: dict[str, Any] = {"sortBy": sort_by}
    if category:
        params["category"] = category

    try:
        resp = _get(
            "https://accounts.clickbank.com/marketplace.htm",
            params=params,
            headers={**_HEADERS, "Referer": "https://accounts.clickbank.com/"},
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".marketplaceResultRow, .cb-result-item")
    except Exception as exc:  # noqa: BLE001
        return _err(f"ClickBank scrape failed: {exc}")

    results: list[dict] = []
    for item in items[:20]:
        title_el = item.select_one(".resultTitle a, h3 a")
        title = title_el.get_text(strip=True) if title_el else ""
        link = title_el.get("href", "") if title_el else ""

        vendor_el = item.select_one(".vendorName, .result-vendor")
        vendor = vendor_el.get_text(strip=True) if vendor_el else ""

        # Parse stats text
        text = item.get_text(" ", strip=True)
        comm_m = re.search(r"Commission[:\s]+(\d+)%", text, re.IGNORECASE)
        gravity_m = re.search(r"Gravity[:\s]+([\d.]+)", text, re.IGNORECASE)
        avg_m = re.search(r"Avg\s+\$Sale[:\s]+\$([\d.]+)", text, re.IGNORECASE)

        if title:
            results.append({
                "title": title,
                "vendor": vendor,
                "commission_pct": int(comm_m.group(1)) if comm_m else 0,
                "gravity": float(gravity_m.group(1)) if gravity_m else 0.0,
                "avg_sale": float(avg_m.group(1)) if avg_m else 0.0,
                "url": f"https://accounts.clickbank.com{link}" if link.startswith("/") else link,
            })

    if not results:
        return json.dumps({
            "error": (
                "ClickBank marketplace requires a logged-in session for detailed results. "
                "Visit https://accounts.clickbank.com/marketplace.htm directly "
                "or use the ClickBank API with an account."
            )
        })

    return json.dumps(results)


# ---------------------------------------------------------------------------
# 5. affiliate_program_search
# ---------------------------------------------------------------------------

_AFFILIATE_DB: dict[str, list[dict]] = {
    "finance": [
        {"program_name": "Wise", "network": "Direct", "commission": "$50/referral", "cookie_days": 30, "url": "https://wise.com/partners"},
        {"program_name": "NerdWallet", "network": "CJ Affiliate", "commission": "Up to $150/lead", "cookie_days": 45, "url": "https://www.cj.com"},
        {"program_name": "Robinhood", "network": "Direct", "commission": "$5-$500/referral", "cookie_days": 30, "url": "https://robinhood.com/us/en/about/affiliate/"},
    ],
    "tech": [
        {"program_name": "Shopify", "network": "Direct", "commission": "$58/sale avg", "cookie_days": 30, "url": "https://www.shopify.com/affiliates"},
        {"program_name": "Bluehost", "network": "Direct", "commission": "$65/sale", "cookie_days": 90, "url": "https://www.bluehost.com/affiliate"},
        {"program_name": "NordVPN", "network": "Direct", "commission": "40%", "cookie_days": 30, "url": "https://nordvpn.com/affiliate/"},
    ],
    "health": [
        {"program_name": "iHerb", "network": "Direct", "commission": "5-10%", "cookie_days": 30, "url": "https://www.iherb.com/info/affiliates"},
        {"program_name": "Noom", "network": "CJ Affiliate", "commission": "$20-40/lead", "cookie_days": 30, "url": "https://www.cj.com"},
        {"program_name": "Thrive Market", "network": "Direct", "commission": "$40/sale", "cookie_days": 14, "url": "https://thrivemarket.com/affiliates"},
    ],
    "education": [
        {"program_name": "Coursera", "network": "Rakuten", "commission": "10-45%", "cookie_days": 30, "url": "https://www.coursera.org/about/affiliates"},
        {"program_name": "Udemy", "network": "Rakuten", "commission": "15%", "cookie_days": 7, "url": "https://www.udemy.com/affiliate/"},
        {"program_name": "Skillshare", "network": "Direct", "commission": "$7/trial", "cookie_days": 30, "url": "https://www.skillshare.com/affiliates"},
    ],
    "software": [
        {"program_name": "ClickFunnels", "network": "Direct", "commission": "20-40%", "cookie_days": 45, "url": "https://www.clickfunnels.com/affiliates"},
        {"program_name": "ActiveCampaign", "network": "Direct", "commission": "20-30%", "cookie_days": 90, "url": "https://www.activecampaign.com/partner/affiliates"},
        {"program_name": "ConvertKit", "network": "Direct", "commission": "30%", "cookie_days": 60, "url": "https://convertkit.com/affiliate"},
    ],
    "travel": [
        {"program_name": "Booking.com", "network": "Direct", "commission": "25-40% of commission", "cookie_days": 30, "url": "https://www.booking.com/affiliate-program"},
        {"program_name": "Expedia", "network": "CJ Affiliate", "commission": "2-6%", "cookie_days": 7, "url": "https://www.cj.com"},
        {"program_name": "Viator", "network": "Direct", "commission": "8%", "cookie_days": 30, "url": "https://www.viator.com/affiliate"},
    ],
    "general": [
        {"program_name": "Amazon Associates", "network": "Amazon", "commission": "1-10%", "cookie_days": 24, "url": "https://affiliate-program.amazon.com"},
        {"program_name": "ShareASale", "network": "ShareASale", "commission": "Varies", "cookie_days": 30, "url": "https://www.shareasale.com"},
        {"program_name": "Impact", "network": "Impact", "commission": "Varies", "cookie_days": 30, "url": "https://impact.com/affiliates/"},
    ],
}


def affiliate_program_search(niche: str) -> str:
    """Search for affiliate programs in a given niche.

    Returns JSON list of {program_name, network, commission, cookie_days, url}.
    """
    niche_lower = niche.lower().strip()

    programs = _AFFILIATE_DB.get(niche_lower, [])

    if not programs:
        for key, progs in _AFFILIATE_DB.items():
            if key in niche_lower or niche_lower in key:
                programs = progs
                break

    general = _AFFILIATE_DB.get("general", [])
    all_programs = programs + [p for p in general if p not in programs]

    return json.dumps(all_programs[:15])


# ---------------------------------------------------------------------------
# 6. affiliate_opportunity_score
# ---------------------------------------------------------------------------

def affiliate_opportunity_score(
    commission_pct: float,
    avg_order_value: float,
    cookie_days: int,
    competition_score: float,
) -> str:
    """Score an affiliate opportunity on a 0-100 scale.

    Score formula: (commission_pct/100 * avg_order_value * log(cookie_days) /
    competition_score) normalised to 0-100.

    Returns JSON {score, potential_revenue_per_100_visitors, grade}.
    """
    commission_pct = float(commission_pct)
    avg_order_value = float(avg_order_value)
    cookie_days = max(1, int(cookie_days))
    competition_score = max(0.1, float(competition_score))

    commission_per_sale = (commission_pct / 100.0) * avg_order_value
    log_cookie = math.log(cookie_days + 1)
    raw_score = (commission_per_sale * log_cookie) / competition_score

    # Normalise: assume a "perfect" product has commission=$100, cookie=90d, competition=1
    perfect = (100.0 * math.log(91)) / 1.0
    score = min(round(raw_score / perfect * 100, 1), 100.0)

    # Assume 2% CTR from traffic, 2% conversion among clickers
    potential = round(100 * 0.02 * 0.02 * commission_per_sale, 2)

    grade = "A" if score >= 75 else "B" if score >= 50 else "C" if score >= 25 else "D"

    return json.dumps({
        "score": score,
        "potential_revenue_per_100_visitors": potential,
        "grade": grade,
        "commission_per_sale": round(commission_per_sale, 2),
        "inputs": {
            "commission_pct": commission_pct,
            "avg_order_value": avg_order_value,
            "cookie_days": cookie_days,
            "competition_score": competition_score,
        },
    })


# ---------------------------------------------------------------------------
# 7. generate_product_review_outline
# ---------------------------------------------------------------------------

def generate_product_review_outline(
    product_name: str,
    pros: list[str] | str,
    cons: list[str] | str,
    target_audience: str,
    price: str,
) -> str:
    """Generate a structured product review outline with SEO guidance.

    Returns JSON with sections for an affiliate review article.
    """
    if isinstance(pros, str):
        pros = [p.strip() for p in pros.split(",") if p.strip()]
    if isinstance(cons, str):
        cons = [c.strip() for c in cons.split(",") if c.strip()]

    outline = {
        "product": product_name,
        "word_count_target": 1500,
        "sections": [
            {
                "name": "Introduction",
                "h2": f"{product_name} Review — Is It Worth It?",
                "content": [
                    f"Brief intro to {product_name} and why it matters.",
                    f"Who this review is for: {target_audience}.",
                    "What you will learn in this review.",
                ],
            },
            {
                "name": "Who Is It For?",
                "h2": f"Who Should Use {product_name}?",
                "content": [
                    f"Primary audience: {target_audience}",
                    "Use cases and ideal scenarios.",
                    "Who should NOT buy this product.",
                ],
            },
            {
                "name": "Features & Overview",
                "h2": f"Key Features of {product_name}",
                "content": [
                    "Walk through the main features with screenshots or examples.",
                    "Compare feature set to common alternatives.",
                ],
            },
            {
                "name": "Pros",
                "h2": f"Pros of {product_name}",
                "content": pros or ["List main advantages here."],
            },
            {
                "name": "Cons",
                "h2": f"Cons of {product_name}",
                "content": cons or ["List main drawbacks here."],
            },
            {
                "name": "Pricing",
                "h2": f"{product_name} Pricing — How Much Does It Cost?",
                "content": [
                    f"Current price: {price}",
                    "Pricing tiers and what each includes.",
                    "Comparison to competitors on value-for-money.",
                ],
            },
            {
                "name": "Verdict",
                "h2": f"Final Verdict — Should You Buy {product_name}?",
                "content": [
                    "Summary of pros and cons.",
                    "Overall rating out of 5.",
                    "Best alternative if this is not right for you.",
                ],
            },
            {
                "name": "Call to Action",
                "h2": f"Get {product_name}",
                "content": [
                    f"[Button: Check Price of {product_name}]",
                    "Reminder of any discounts or bonuses.",
                    "FAQ section addressing top buyer objections.",
                ],
            },
        ],
        "seo_tips": [
            f"Primary keyword: '{product_name} review'",
            f"Secondary keywords: '{product_name} pros cons', '{product_name} alternatives'",
            "Use schema markup: Review type with ratingValue and reviewCount.",
            "Add a comparison table if reviewing multiple products.",
        ],
    }
    return json.dumps(outline)


# ---------------------------------------------------------------------------
# 8. generate_comparison_table_html
# ---------------------------------------------------------------------------

def generate_comparison_table_html(products: list[dict] | str) -> str:
    """Generate a responsive HTML comparison table for affiliate products.

    products: list of {name, price, features: [], affiliate_url, rating}
    Returns HTML string.
    """
    if isinstance(products, str):
        try:
            products = json.loads(products)
        except json.JSONDecodeError:
            return "ERROR: products must be a JSON list or Python list"

    if not products:
        return "ERROR: No products provided."

    # Collect all feature keys across all products
    all_features: list[str] = []
    for p in products:
        for f in p.get("features", []):
            if f not in all_features:
                all_features.append(f)

    header_cells = "".join(
        f'<th><a href="{p.get("affiliate_url","#")}" rel="nofollow sponsored" target="_blank">'
        f'{p.get("name","")}</a></th>'
        for p in products
    )

    price_cells = "".join(
        f'<td class="price">{p.get("price","N/A")}</td>' for p in products
    )
    rating_cells = "".join(
        f'<td class="rating">{"&#9733;" * int(float(p.get("rating", 0)))}'
        f' ({p.get("rating","-")})</td>'
        for p in products
    )

    feature_rows = ""
    for feat in all_features:
        cells = "".join(
            f'<td class="{"yes" if feat in p.get("features",[]) else "no"}">'
            f'{"&#10004;" if feat in p.get("features",[]) else "&#10008;"}</td>'
            for p in products
        )
        feature_rows += f"<tr><td class='feature-name'>{feat}</td>{cells}</tr>\n"

    cta_cells = "".join(
        f'<td><a href="{p.get("affiliate_url","#")}" class="btn-cta" '
        f'rel="nofollow sponsored" target="_blank">Check Price</a></td>'
        for p in products
    )

    html = (
        '<div class="comparison-table-wrapper" style="overflow-x:auto;">\n'
        "<style>\n"
        "  .comparison-table { width:100%; border-collapse:collapse; font-family:sans-serif; }\n"
        "  .comparison-table th { background:#2c3e50; color:#fff; padding:12px 8px; }\n"
        "  .comparison-table td { border:1px solid #ddd; padding:10px 8px; text-align:center; }\n"
        "  .comparison-table tr:nth-child(even) { background:#f9f9f9; }\n"
        "  .feature-name { text-align:left; font-weight:bold; }\n"
        "  .yes { color:#27ae60; font-size:1.2em; }\n"
        "  .no { color:#e74c3c; font-size:1.2em; }\n"
        "  .price { font-weight:bold; color:#2980b9; }\n"
        "  .rating { color:#f39c12; }\n"
        "  .btn-cta { background:#e74c3c; color:#fff; padding:8px 16px; border-radius:4px;\n"
        "             text-decoration:none; display:inline-block; }\n"
        "  .btn-cta:hover { background:#c0392b; }\n"
        "</style>\n"
        '<table class="comparison-table">\n'
        "  <thead>\n"
        "    <tr>\n"
        "      <th>Feature</th>\n"
        f"      {header_cells}\n"
        "    </tr>\n"
        "  </thead>\n"
        "  <tbody>\n"
        f"    <tr><td class='feature-name'>Price</td>{price_cells}</tr>\n"
        f"    <tr><td class='feature-name'>Rating</td>{rating_cells}</tr>\n"
        f"    {feature_rows}"
        f"    <tr><td class='feature-name'>Buy</td>{cta_cells}</tr>\n"
        "  </tbody>\n"
        "</table>\n"
        "</div>"
    )
    return html


# ---------------------------------------------------------------------------
# 9. estimate_affiliate_revenue
# ---------------------------------------------------------------------------

def estimate_affiliate_revenue(
    monthly_traffic: float,
    ctr_pct: float,
    conversion_rate_pct: float,
    avg_commission: float,
) -> str:
    """Estimate monthly and annual affiliate revenue.

    Returns JSON {monthly_estimate, annual_estimate, clicks, conversions}.
    """
    monthly_traffic = float(monthly_traffic)
    ctr_pct = float(ctr_pct)
    conversion_rate_pct = float(conversion_rate_pct)
    avg_commission = float(avg_commission)

    clicks = monthly_traffic * (ctr_pct / 100.0)
    conversions = clicks * (conversion_rate_pct / 100.0)
    monthly = round(conversions * avg_commission, 2)
    annual = round(monthly * 12, 2)

    return json.dumps({
        "monthly_estimate": monthly,
        "annual_estimate": annual,
        "clicks": round(clicks, 0),
        "conversions": round(conversions, 2),
        "epc": round(monthly / clicks, 4) if clicks > 0 else 0,
        "inputs": {
            "monthly_traffic": monthly_traffic,
            "ctr_pct": ctr_pct,
            "conversion_rate_pct": conversion_rate_pct,
            "avg_commission": avg_commission,
        },
    })


# ---------------------------------------------------------------------------
# 10. top_affiliate_niches
# ---------------------------------------------------------------------------

def top_affiliate_niches() -> str:
    """Return a curated list of top affiliate marketing niches.

    Returns JSON list of {niche, avg_commission_pct, avg_epc, competition,
    best_networks}.
    """
    niches = [
        {"niche": "Finance & Investing", "avg_commission_pct": 25, "avg_epc": 3.50, "competition": "high", "best_networks": ["CJ Affiliate", "Impact", "Direct"]},
        {"niche": "Software / SaaS", "avg_commission_pct": 30, "avg_epc": 2.80, "competition": "high", "best_networks": ["PartnerStack", "ShareASale", "Direct"]},
        {"niche": "Web Hosting", "avg_commission_pct": 0, "avg_epc": 4.20, "competition": "high", "best_networks": ["Direct", "CJ Affiliate"], "notes": "Fixed bounty $65-$150/sale"},
        {"niche": "Health & Wellness", "avg_commission_pct": 20, "avg_epc": 1.60, "competition": "medium", "best_networks": ["ShareASale", "Rakuten", "CJ Affiliate"]},
        {"niche": "Weight Loss", "avg_commission_pct": 30, "avg_epc": 2.10, "competition": "high", "best_networks": ["ClickBank", "CJ Affiliate"]},
        {"niche": "Online Courses / E-Learning", "avg_commission_pct": 30, "avg_epc": 1.90, "competition": "medium", "best_networks": ["Rakuten", "Direct", "ShareASale"]},
        {"niche": "VPN & Cybersecurity", "avg_commission_pct": 40, "avg_epc": 3.00, "competition": "medium", "best_networks": ["Direct", "Impact"]},
        {"niche": "Travel", "avg_commission_pct": 6, "avg_epc": 1.40, "competition": "high", "best_networks": ["Booking.com", "CJ Affiliate", "Travelpayouts"]},
        {"niche": "Personal Finance / Credit Cards", "avg_commission_pct": 0, "avg_epc": 8.00, "competition": "very high", "best_networks": ["CJ Affiliate", "Bankrate", "LendingTree"], "notes": "Cost-per-lead model"},
        {"niche": "Beauty & Skincare", "avg_commission_pct": 12, "avg_epc": 1.20, "competition": "medium", "best_networks": ["ShareASale", "Rakuten", "LTK"]},
        {"niche": "Fashion & Apparel", "avg_commission_pct": 10, "avg_epc": 0.90, "competition": "high", "best_networks": ["LTK", "RewardStyle", "ShareASale"]},
        {"niche": "Pet Products", "avg_commission_pct": 10, "avg_epc": 1.10, "competition": "low", "best_networks": ["Chewy", "Amazon", "ShareASale"]},
        {"niche": "Home Improvement", "avg_commission_pct": 8, "avg_epc": 1.30, "competition": "medium", "best_networks": ["Amazon", "ShareASale", "CJ Affiliate"]},
        {"niche": "Gaming", "avg_commission_pct": 5, "avg_epc": 0.70, "competition": "high", "best_networks": ["Amazon", "Fanatical", "Green Man Gaming"]},
        {"niche": "CBD / Supplements", "avg_commission_pct": 25, "avg_epc": 2.00, "competition": "medium", "best_networks": ["ShareASale", "Direct"]},
        {"niche": "Crypto & NFT", "avg_commission_pct": 30, "avg_epc": 3.50, "competition": "high", "best_networks": ["Direct", "CJ Affiliate"]},
        {"niche": "Baby & Parenting", "avg_commission_pct": 8, "avg_epc": 1.00, "competition": "low", "best_networks": ["Amazon", "ShareASale"]},
        {"niche": "Dating & Relationships", "avg_commission_pct": 40, "avg_epc": 2.50, "competition": "medium", "best_networks": ["ClickBank", "CJ Affiliate"]},
        {"niche": "Business & Entrepreneurship", "avg_commission_pct": 20, "avg_epc": 2.00, "competition": "medium", "best_networks": ["PartnerStack", "Impact", "Direct"]},
        {"niche": "Photography & Video", "avg_commission_pct": 15, "avg_epc": 1.50, "competition": "low", "best_networks": ["Adobe", "Amazon", "ShareASale"]},
    ]
    return json.dumps(niches)


# ---------------------------------------------------------------------------
# 11. payhip_scrape
# ---------------------------------------------------------------------------

def payhip_scrape(username: str) -> str:
    """Scrape a Payhip creator store page.

    Returns JSON list of {title, price, type, url}.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return _err("BeautifulSoup4 not installed.")

    url = f"https://payhip.com/{username}"
    try:
        resp = _get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to fetch Payhip page for {username!r}: {exc}")

    results: list[dict] = []
    cards = soup.select(".product-card, .product-item, [class*='product']")
    if not cards:
        cards = soup.select("article, .item")

    for card in cards[:20]:
        title_el = card.select_one("h2, h3, .product-title, .item-title, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = card.select_one(".price, [class*='price'], .product-price")
        price = price_el.get_text(strip=True) if price_el else ""

        link_el = card.select_one("a[href]")
        href = link_el.get("href", "") if link_el else ""
        if href and not href.startswith("http"):
            href = f"https://payhip.com{href}"

        product_type = "digital"
        text_lower = (title + " " + card.get_text()).lower()
        if "course" in text_lower:
            product_type = "course"
        elif "bundle" in text_lower:
            product_type = "bundle"
        elif "membership" in text_lower:
            product_type = "membership"

        if title:
            results.append({
                "title": title,
                "price": price,
                "type": product_type,
                "url": href,
            })

    return json.dumps(results)


# ---------------------------------------------------------------------------
# 12. gumroad_scrape
# ---------------------------------------------------------------------------

def gumroad_scrape(creator_slug: str) -> str:
    """Scrape a Gumroad creator profile page.

    Returns JSON list of {title, price, type, url}.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return _err("BeautifulSoup4 not installed.")

    url = f"https://gumroad.com/{creator_slug}"
    try:
        resp = _get(url, headers={**_HEADERS, "Accept": "text/html"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to fetch Gumroad page for {creator_slug!r}: {exc}")

    results: list[dict] = []
    cards = soup.select("[data-product-id], .product-card, .js-product")
    if not cards:
        cards = soup.select("li.product")

    for card in cards[:20]:
        title_el = card.select_one("h2, h3, strong, .product-name")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = card.select_one(".price, [data-price], .js-price")
        price = price_el.get_text(strip=True) if price_el else ""
        if not price:
            price_data = card.get("data-price", "")
            if price_data:
                try:
                    price = f"${float(price_data)/100:.2f}"
                except (ValueError, TypeError):
                    price = price_data

        link_el = card.select_one("a[href]")
        href = link_el.get("href", "") if link_el else ""
        if href and not href.startswith("http"):
            href = "https://gumroad.com" + href

        product_type = "digital"
        text_lower = (title + " " + card.get_text()).lower()
        if "course" in text_lower or "class" in text_lower:
            product_type = "course"
        elif "ebook" in text_lower or "book" in text_lower:
            product_type = "ebook"
        elif "template" in text_lower:
            product_type = "template"
        elif "membership" in text_lower:
            product_type = "membership"

        if title:
            results.append({
                "title": title,
                "price": price,
                "type": product_type,
                "url": href,
            })

    return json.dumps(results)


# ---------------------------------------------------------------------------
# 13. etsy_bestsellers
# ---------------------------------------------------------------------------

def etsy_bestsellers(category: str, num: int = 20) -> str:
    """Scrape Etsy bestsellers in a category.

    Returns JSON list of {title, price, sales_count, shop, url}.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return _err("BeautifulSoup4 not installed.")

    url = f"https://www.etsy.com/c/{category}"
    params = {"sort_on": "most_relevant", "explicit": "1"}
    headers = {**_HEADERS, "Accept-Language": "en-US,en;q=0.9"}

    try:
        resp = _get(url, params=params, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to fetch Etsy category {category!r}: {exc}")

    results: list[dict] = []
    listings = soup.select(
        "li[data-appears-component-name='listing'], "
        ".v2-listing-card, "
        "[class*='listing-card']"
    )
    if not listings:
        listings = soup.select("li.listing-link, article")

    for listing in listings[:int(num)]:
        title_el = listing.select_one(
            "h3, .v2-listing-card__title, [class*='listing-title'], "
            "[class*='card-title']"
        )
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = listing.select_one(
            "[class*='currency-value'], .currency-value, span.currency"
        )
        price = price_el.get_text(strip=True) if price_el else ""

        shop_el = listing.select_one("[class*='shop-name'], .shop-name, p.text-body")
        shop = shop_el.get_text(strip=True) if shop_el else ""

        sales_el = listing.select_one("[class*='sales'], .listing-card__details")
        sales_text = sales_el.get_text(strip=True) if sales_el else ""
        sales_m = re.search(r"([\d,]+)\s+sale", sales_text, re.IGNORECASE)
        sales_count = sales_m.group(1).replace(",", "") if sales_m else "0"

        link_el = listing.select_one("a[href*='/listing/']")
        href = link_el.get("href", "") if link_el else ""
        if href and not href.startswith("http"):
            href = "https://www.etsy.com" + href

        if title:
            results.append({
                "title": title,
                "price": price,
                "sales_count": int(sales_count) if sales_count.isdigit() else 0,
                "shop": shop,
                "url": href,
            })

    return json.dumps(results)


# ---------------------------------------------------------------------------
# 14. commission_tracker
# ---------------------------------------------------------------------------

def commission_tracker(
    product_name: str,
    clicks: int,
    sales: int,
    commission_per_sale: float,
) -> str:
    """Track affiliate conversion metrics for a product.

    Returns JSON {conversion_rate_pct, total_earnings, epc, status}.
    """
    clicks = max(1, int(clicks))
    sales = int(sales)
    commission_per_sale = float(commission_per_sale)

    conversion_rate = (sales / clicks) * 100.0
    total_earnings = round(sales * commission_per_sale, 2)
    epc = round(total_earnings / clicks, 4)

    if conversion_rate >= 3.0:
        status = "excellent"
    elif conversion_rate >= 1.5:
        status = "good"
    elif conversion_rate >= 0.5:
        status = "average"
    else:
        status = "poor"

    return json.dumps({
        "product": product_name,
        "clicks": clicks,
        "sales": sales,
        "conversion_rate_pct": round(conversion_rate, 2),
        "total_earnings": total_earnings,
        "epc": epc,
        "status": status,
        "tip": (
            "Conversion rate is strong — scale traffic to this page."
            if status == "excellent"
            else "Consider A/B testing your landing page or review content to improve conversions."
        ),
    })
