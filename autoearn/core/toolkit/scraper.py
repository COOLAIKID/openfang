from __future__ import annotations

import re
import json
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlencode, quote_plus

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_SESSION = requests.Session()
_SESSION.headers.update(_DEFAULT_HEADERS)


def _get(url: str, timeout: int = 15, **kwargs) -> requests.Response:
    """Thin wrapper around requests.get with sensible defaults."""
    return _SESSION.get(url, timeout=timeout, **kwargs)


def _soup(html: str, parser: str = "html.parser") -> BeautifulSoup:
    return BeautifulSoup(html, parser)


# ---------------------------------------------------------------------------
# 1. scrape_paginated
# ---------------------------------------------------------------------------

def scrape_paginated(
    base_url: str,
    page_param: str = "page",
    max_pages: int = 10,
    item_selector: str = "",
) -> list[dict]:
    """
    Scrape multiple pages of a paginated website.

    Parameters
    ----------
    base_url       : URL without page parameter (query string OK)
    page_param     : query-string key used for pagination
    max_pages      : maximum number of pages to fetch
    item_selector  : CSS selector for items; defaults to all ``<a>`` tags

    Returns
    -------
    List of dicts {text, href, page} for every item found.
    """
    all_items: list[dict] = []
    seen_hrefs: set[str] = set()

    for page_num in range(1, max_pages + 1):
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}{page_param}={page_num}"

        try:
            resp = _get(url)
            resp.raise_for_status()
        except requests.RequestException:
            break

        soup = _soup(resp.text)
        selector = item_selector or "a"
        items = soup.select(selector)

        if not items:
            break

        page_items: list[dict] = []
        for el in items:
            href = el.get("href", "")
            full_href = urljoin(url, href) if href else ""
            text = el.get_text(strip=True)
            page_items.append({"text": text, "href": full_href, "page": page_num})

        # Detect duplicate pages (loop guard)
        new_hrefs = {i["href"] for i in page_items if i["href"]}
        if new_hrefs and new_hrefs.issubset(seen_hrefs):
            break
        seen_hrefs.update(new_hrefs)
        all_items.extend(page_items)

    return all_items


# ---------------------------------------------------------------------------
# 2. scrape_table
# ---------------------------------------------------------------------------

def scrape_table(url: str, table_index: int = 0) -> dict:
    """
    Extract an HTML table as JSON.

    Returns
    -------
    {headers: [...], rows: [[...], ...]}
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"headers": [], "rows": [], "error": str(exc)}

    soup = _soup(resp.text)
    tables = soup.find_all("table")

    if not tables or table_index >= len(tables):
        return {"headers": [], "rows": [], "error": "Table not found"}

    table = tables[table_index]
    headers: list[str] = []
    rows: list[list[str]] = []

    thead = table.find("thead")
    if thead:
        header_cells = thead.find_all(["th", "td"])
        headers = [c.get_text(strip=True) for c in header_cells]

    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        row = [c.get_text(strip=True) for c in cells]
        # If headers not found yet, treat first row as headers
        if not headers:
            headers = row
        else:
            rows.append(row)

    return {"headers": headers, "rows": rows}


# ---------------------------------------------------------------------------
# 3. scrape_product_price
# ---------------------------------------------------------------------------

_PRICE_SELECTORS = [
    "[itemprop='price']",
    ".price",
    "#price",
    ".product-price",
    ".offer-price",
    ".sale-price",
    "[class*='price']",
    "[id*='price']",
    "span.amount",
    "p.price",
]

_CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
    "₽": "RUB",
    "A$": "AUD",
    "C$": "CAD",
    "CHF": "CHF",
}


def scrape_product_price(url: str) -> dict:
    """
    Try to extract a product price from any e-commerce page.

    Returns
    -------
    {price: float|None, currency: str, raw: str, found: bool}
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"price": None, "currency": "", "raw": "", "found": False, "error": str(exc)}

    soup = _soup(resp.text)

    # 1) Try itemprop first (most reliable)
    itemprop = soup.find(attrs={"itemprop": "price"})
    if itemprop:
        raw = itemprop.get("content") or itemprop.get_text(strip=True)
        return _parse_price_raw(raw)

    # 2) Try JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        price_val = None
        if isinstance(data, dict):
            price_val = (
                data.get("price")
                or (data.get("offers") or {}).get("price")
                or (data.get("offers") or {}).get("lowPrice")
            )
        if price_val:
            return _parse_price_raw(str(price_val))

    # 3) Try CSS selectors
    for selector in _PRICE_SELECTORS:
        el = soup.select_one(selector)
        if el:
            raw = el.get("content") or el.get_text(strip=True)
            result = _parse_price_raw(raw)
            if result["found"]:
                return result

    return {"price": None, "currency": "", "raw": "", "found": False}


def _parse_price_raw(raw: str) -> dict:
    raw = raw.strip()
    currency = ""
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in raw:
            currency = code
            break
    num_match = re.search(r"[\d,]+\.?\d*", raw.replace(",", ""))
    if num_match:
        try:
            price = float(num_match.group().replace(",", ""))
            return {"price": price, "currency": currency or "USD", "raw": raw, "found": True}
        except ValueError:
            pass
    return {"price": None, "currency": currency, "raw": raw, "found": False}


# ---------------------------------------------------------------------------
# 4. scrape_emails
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)


def scrape_emails(url: str) -> list[str]:
    """
    Extract all email addresses from a page.
    Decodes common obfuscation patterns (mailto:, [at], (dot)).
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    text = resp.text

    # Decode mailto: links
    soup = _soup(text)
    emails: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if _EMAIL_RE.match(addr):
                emails.add(addr.lower())

    # Normalise obfuscated forms
    normalised = (
        text.replace("[at]", "@")
        .replace("(at)", "@")
        .replace(" at ", "@")
        .replace("[dot]", ".")
        .replace("(dot)", ".")
    )
    for match in _EMAIL_RE.findall(normalised):
        emails.add(match.lower())

    return sorted(emails)


# ---------------------------------------------------------------------------
# 5. scrape_phone_numbers
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(
    r"(?:\+?\d[\s\-.]?)?(?:\(?\d{1,4}\)?[\s\-.]?)?\d{1,4}[\s\-.]?\d{2,4}[\s\-.]?\d{2,4}"
)


def scrape_phone_numbers(url: str) -> list[str]:
    """
    Extract phone numbers from a page. Returns deduplicated list.
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    soup = _soup(resp.text)

    # tel: links
    phones: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("tel:"):
            phones.add(href[4:].strip())

    text = soup.get_text(" ")
    for match in _PHONE_RE.findall(text):
        cleaned = match.strip()
        digits = re.sub(r"\D", "", cleaned)
        if 7 <= len(digits) <= 15:
            phones.add(cleaned)

    return sorted(phones)


# ---------------------------------------------------------------------------
# 6. scrape_social_links
# ---------------------------------------------------------------------------

_SOCIAL_PATTERNS: dict[str, re.Pattern] = {
    "twitter": re.compile(r"(?:twitter\.com|x\.com)/[A-Za-z0-9_]+"),
    "linkedin": re.compile(r"linkedin\.com/(?:in|company)/[A-Za-z0-9_\-]+"),
    "facebook": re.compile(r"facebook\.com/[A-Za-z0-9_.]+"),
    "instagram": re.compile(r"instagram\.com/[A-Za-z0-9_.]+"),
    "youtube": re.compile(r"youtube\.com/(?:channel|user|@)[A-Za-z0-9_\-]+"),
    "tiktok": re.compile(r"tiktok\.com/@[A-Za-z0-9_.]+"),
    "github": re.compile(r"github\.com/[A-Za-z0-9_\-]+"),
    "pinterest": re.compile(r"pinterest\.com/[A-Za-z0-9_\-]+"),
}


def scrape_social_links(url: str) -> dict[str, list[str]]:
    """
    Extract links to social media profiles from a page.

    Returns
    -------
    {platform: [url, ...], ...}
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException:
        return {}

    soup = _soup(resp.text)
    results: dict[str, set[str]] = {k: set() for k in _SOCIAL_PATTERNS}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        for platform, pattern in _SOCIAL_PATTERNS.items():
            if pattern.search(href):
                full = urljoin(url, href)
                results[platform].add(full)

    return {k: sorted(v) for k, v in results.items() if v}


# ---------------------------------------------------------------------------
# 7. scrape_structured_data
# ---------------------------------------------------------------------------

def scrape_structured_data(url: str) -> dict:
    """
    Extract JSON-LD and microdata from a page.

    Returns
    -------
    {jsonld: [...], microdata: [...]}
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"jsonld": [], "microdata": [], "error": str(exc)}

    soup = _soup(resp.text)
    jsonld_items: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                jsonld_items.extend(data)
            else:
                jsonld_items.append(data)
        except (json.JSONDecodeError, TypeError):
            continue

    # Microdata
    microdata_items: list[dict] = []
    for el in soup.find_all(attrs={"itemscope": True}):
        item: dict = {}
        item_type = el.get("itemtype", "")
        if item_type:
            item["@type"] = item_type
        for prop in el.find_all(attrs={"itemprop": True}):
            name = prop["itemprop"]
            value = (
                prop.get("content")
                or prop.get("href")
                or prop.get("src")
                or prop.get_text(strip=True)
            )
            if name in item:
                existing = item[name]
                if not isinstance(existing, list):
                    item[name] = [existing]
                item[name].append(value)
            else:
                item[name] = value
        if item:
            microdata_items.append(item)

    return {"jsonld": jsonld_items, "microdata": microdata_items}


# ---------------------------------------------------------------------------
# 8. scrape_news_articles
# ---------------------------------------------------------------------------

def scrape_news_articles(url: str) -> dict:
    """
    Extract article text, author and date from a news URL.

    Returns
    -------
    {title, author, date, text, url, word_count}
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"title": "", "author": "", "date": "", "text": "", "url": url, "error": str(exc)}

    soup = _soup(resp.text)

    # Title
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = og_title.get("content", "")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else (soup.title.string.strip() if soup.title else "")

    # Author
    author = ""
    for selector in [
        "[itemprop='author']",
        ".author",
        "[rel='author']",
        "[class*='author']",
        "[name='author']",
    ]:
        el = soup.select_one(selector)
        if el:
            author = el.get("content") or el.get_text(strip=True)
            break

    # Date
    pub_date = ""
    for attr in ["article:published_time", "og:article:published_time", "datePublished"]:
        meta = soup.find("meta", attrs={"property": attr}) or soup.find(
            "meta", attrs={"name": attr}
        )
        if meta:
            pub_date = meta.get("content", "")
            break
    if not pub_date:
        time_el = soup.find("time")
        if time_el:
            pub_date = time_el.get("datetime") or time_el.get_text(strip=True)

    # Body text
    article_el = (
        soup.find("article")
        or soup.find(attrs={"itemprop": "articleBody"})
        or soup.find(class_=re.compile(r"article|post|story|content", re.I))
    )
    if article_el:
        paragraphs = article_el.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    else:
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40)

    return {
        "title": title,
        "author": author,
        "date": pub_date,
        "text": text,
        "url": url,
        "word_count": len(text.split()),
    }


# ---------------------------------------------------------------------------
# 9. crawl_sitemap
# ---------------------------------------------------------------------------

def crawl_sitemap(sitemap_url: str, max_urls: int = 100) -> list[str]:
    """
    Parse an XML sitemap (including sitemap index files) and return URLs.

    Recursively follows sitemap index entries up to max_urls total.
    """
    urls: list[str] = []
    _crawl_sitemap_recursive(sitemap_url, urls, max_urls)
    return urls[:max_urls]


def _crawl_sitemap_recursive(url: str, urls: list[str], max_urls: int) -> None:
    if len(urls) >= max_urls:
        return
    try:
        resp = _get(url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return

    ns_map = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
    }

    # Sitemap index
    for sitemap_tag in root.findall(".//sm:sitemap/sm:loc", ns_map):
        if len(urls) >= max_urls:
            return
        _crawl_sitemap_recursive(sitemap_tag.text.strip(), urls, max_urls)

    # Regular url entries
    for loc_tag in root.findall(".//sm:url/sm:loc", ns_map):
        if len(urls) >= max_urls:
            return
        loc = loc_tag.text.strip()
        if loc not in urls:
            urls.append(loc)

    # Fallback: no-namespace
    if not urls:
        for child in root.iter():
            tag = child.tag.split("}")[-1]
            if tag == "loc" and child.text:
                loc = child.text.strip()
                if loc not in urls:
                    urls.append(loc)
                if len(urls) >= max_urls:
                    return


# ---------------------------------------------------------------------------
# 10. check_url_status
# ---------------------------------------------------------------------------

def check_url_status(urls: list[str]) -> dict[str, int | str]:
    """
    Batch check HTTP status codes for a list of URLs.

    Returns
    -------
    {url: status_code_or_error_string}
    """
    results: dict[str, int | str] = {}
    for url in urls:
        try:
            resp = _SESSION.head(url, timeout=10, allow_redirects=True)
            results[url] = resp.status_code
        except requests.exceptions.ConnectionError:
            results[url] = "connection_error"
        except requests.exceptions.Timeout:
            results[url] = "timeout"
        except requests.RequestException as exc:
            results[url] = str(exc)
    return results


# ---------------------------------------------------------------------------
# 11. extract_rss_urls
# ---------------------------------------------------------------------------

_RSS_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/rss",
}


def extract_rss_urls(url: str) -> list[str]:
    """
    Find RSS/Atom feed URLs from a website's HTML <link> tags.

    Also tries common feed paths (/feed, /rss, /atom) as fallback.
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    soup = _soup(resp.text)
    feeds: list[str] = []

    for link in soup.find_all("link", type=True):
        if link["type"].lower() in _RSS_TYPES:
            href = link.get("href", "")
            if href:
                feeds.append(urljoin(url, href))

    if not feeds:
        candidates = ["/feed", "/rss", "/atom", "/feed.xml", "/rss.xml", "/atom.xml"]
        base = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(url))
        for path in candidates:
            candidate_url = base + path
            try:
                r = _SESSION.head(candidate_url, timeout=8, allow_redirects=True)
                if r.status_code == 200:
                    feeds.append(candidate_url)
            except requests.RequestException:
                continue

    return list(dict.fromkeys(feeds))  # deduplicate, preserve order


# ---------------------------------------------------------------------------
# 12. scrape_job_posting
# ---------------------------------------------------------------------------

def scrape_job_posting(url: str) -> dict:
    """
    Extract structured data from a job posting page.

    Returns
    -------
    {title, company, location, description, requirements, salary, date_posted, url}
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"title": "", "company": "", "url": url, "error": str(exc)}

    soup = _soup(resp.text)

    # Try JSON-LD first
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("JobPosting", "jobPosting"):
                salary_raw = item.get("baseSalary", {})
                if isinstance(salary_raw, dict):
                    val = salary_raw.get("value", {})
                    salary = (
                        f"{val.get('minValue', '')}-{val.get('maxValue', '')} "
                        f"{salary_raw.get('currency', '')}"
                        if isinstance(val, dict)
                        else str(val)
                    )
                else:
                    salary = str(salary_raw)
                return {
                    "title": item.get("title", ""),
                    "company": (item.get("hiringOrganization") or {}).get("name", ""),
                    "location": _flatten_location(item.get("jobLocation", {})),
                    "description": _strip_html(item.get("description", "")),
                    "requirements": item.get("qualifications", ""),
                    "salary": salary.strip(),
                    "date_posted": item.get("datePosted", ""),
                    "url": url,
                }

    # Fallback: heuristic selectors
    title = _text_by_selectors(
        soup, ["h1", "[class*='job-title']", "[class*='position']", "[itemprop='title']"]
    )
    company = _text_by_selectors(
        soup,
        [
            "[class*='company']",
            "[itemprop='hiringOrganization']",
            "[class*='employer']",
        ],
    )
    description_el = soup.find(
        attrs={"class": re.compile(r"description|job-desc|details", re.I)}
    )
    description = description_el.get_text("\n", strip=True) if description_el else ""
    requirements_el = soup.find(
        attrs={"class": re.compile(r"requirements|qualifications", re.I)}
    )
    requirements = requirements_el.get_text("\n", strip=True) if requirements_el else ""

    return {
        "title": title,
        "company": company,
        "location": "",
        "description": description,
        "requirements": requirements,
        "salary": "",
        "date_posted": "",
        "url": url,
    }


def _flatten_location(loc) -> str:
    if isinstance(loc, str):
        return loc
    if isinstance(loc, dict):
        addr = loc.get("address", loc)
        if isinstance(addr, dict):
            parts = [
                addr.get("streetAddress", ""),
                addr.get("addressLocality", ""),
                addr.get("addressRegion", ""),
                addr.get("addressCountry", ""),
            ]
            return ", ".join(p for p in parts if p)
        return str(addr)
    return ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _text_by_selectors(soup: BeautifulSoup, selectors: list[str]) -> str:
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            return el.get_text(strip=True)
    return ""


# ---------------------------------------------------------------------------
# 13. monitor_page_change
# ---------------------------------------------------------------------------

def monitor_page_change(url: str, previous_hash: str = "") -> dict:
    """
    Fetch a page and compute a content hash.  Compare to a previous hash.

    Returns
    -------
    {changed: bool, hash: str, url: str, fetched_at: str}
    """
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {
            "changed": False,
            "hash": "",
            "url": url,
            "fetched_at": datetime.utcnow().isoformat(),
            "error": str(exc),
        }

    # Normalise whitespace so minor formatting changes don't trigger alerts
    soup = _soup(resp.text)
    # Remove scripts and styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    content = " ".join(soup.get_text().split())
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    changed = bool(previous_hash) and content_hash != previous_hash

    return {
        "changed": changed,
        "hash": content_hash,
        "url": url,
        "fetched_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# 14. extract_contact_info
# ---------------------------------------------------------------------------

def extract_contact_info(url: str) -> dict:
    """
    Comprehensive contact info extraction.

    Returns
    -------
    {emails, phones, address, social, contact_page_url}
    """
    emails = scrape_emails(url)
    phones = scrape_phone_numbers(url)
    social = scrape_social_links(url)

    try:
        resp = _get(url)
        resp.raise_for_status()
        soup = _soup(resp.text)
    except requests.RequestException:
        return {
            "emails": emails,
            "phones": phones,
            "address": "",
            "social": social,
            "contact_page_url": "",
        }

    # Address extraction via schema.org or heuristic
    address = ""
    addr_el = soup.find(attrs={"itemprop": "address"}) or soup.find(
        attrs={"class": re.compile(r"address", re.I)}
    )
    if addr_el:
        address = addr_el.get_text(", ", strip=True)

    # Contact page link
    contact_page_url = ""
    base = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(url))
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = a["href"]
        if re.search(r"\bcontact\b", text) or re.search(r"/contact", href, re.I):
            contact_page_url = urljoin(base, href)
            break

    # If primary page has no contact info, try the contact page
    if contact_page_url and not emails:
        emails = scrape_emails(contact_page_url)
    if contact_page_url and not phones:
        phones = scrape_phone_numbers(contact_page_url)

    return {
        "emails": emails,
        "phones": phones,
        "address": address,
        "social": social,
        "contact_page_url": contact_page_url,
    }


# ---------------------------------------------------------------------------
# 15. scrape_google_trends_keyword
# ---------------------------------------------------------------------------

_TRENDS_RSS_BASE = "https://trends.google.com/trends/trendingsearches/daily/rss"
_TRENDS_API_BASE = "https://trends.google.com/trends/api/explore"


def scrape_google_trends_keyword(keyword: str) -> dict:
    """
    Fetch Google Trends data for a keyword.

    Attempts the unofficial JSON API first; falls back to the RSS trending feed.

    Returns
    -------
    {keyword, interest_over_time: [...], related_queries: [...], source}
    """
    # Attempt 1 – unofficial Trends API (returns JSONP with )]}'\n prefix)
    try:
        params = {
            "hl": "en-US",
            "tz": "-60",
            "req": json.dumps(
                {
                    "comparisonItem": [
                        {"keyword": keyword, "geo": "", "time": "today 12-m"}
                    ],
                    "category": 0,
                    "property": "",
                }
            ),
            "token": "APP6_UEAAAAAZnk",  # public token, may change
        }
        resp = _get(_TRENDS_API_BASE, params=params, timeout=20)
        if resp.status_code == 200:
            body = resp.text
            # Strip JSONP safety prefix
            body = re.sub(r"^\)\]\}'\n", "", body)
            data = json.loads(body)
            widgets = data.get("widgets", [])
            interest: list[dict] = []
            related: list[str] = []
            for widget in widgets:
                if widget.get("id") == "TIMESERIES":
                    for point in widget.get("lineAnnotations", []):
                        interest.append(point)
                elif widget.get("id") in ("RELATED_QUERIES", "RELATED_TOPICS"):
                    for item in widget.get("rankedList", [{}])[0].get("rankedKeyword", []):
                        related.append(item.get("query", {}).get("value", ""))
            if interest or related:
                return {
                    "keyword": keyword,
                    "interest_over_time": interest,
                    "related_queries": [r for r in related if r],
                    "source": "google_trends_api",
                }
    except Exception:
        pass

    # Attempt 2 – RSS trending searches (daily top)
    try:
        rss_url = f"{_TRENDS_RSS_BASE}?geo=US"
        resp = _get(rss_url, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items: list[dict] = []
        for item in root.findall(".//item"):
            title_el = item.find("title")
            traffic_el = item.find("{http://www.google.com/trending/}approx_traffic")
            title_text = title_el.text.strip() if title_el is not None and title_el.text else ""
            traffic_text = (
                traffic_el.text.strip()
                if traffic_el is not None and traffic_el.text
                else ""
            )
            items.append({"query": title_text, "approx_traffic": traffic_text})

        # Filter for the queried keyword
        matching = [i for i in items if keyword.lower() in i["query"].lower()]
        return {
            "keyword": keyword,
            "interest_over_time": [],
            "related_queries": [i["query"] for i in items[:20]],
            "matching_trending": matching,
            "source": "google_trends_rss",
        }
    except Exception as exc:
        return {
            "keyword": keyword,
            "interest_over_time": [],
            "related_queries": [],
            "error": str(exc),
            "source": "none",
        }
