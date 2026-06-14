from __future__ import annotations

import json
import math
import re
import socket
import ssl
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COMMON_KEYWORDS = {
    "tech", "ai", "cloud", "hub", "labs", "studio", "solutions",
    "digital", "smart", "fast", "pro", "go", "my", "best",
}

_PREMIUM_EXTENSIONS = {
    ".com": 1.0,
    ".io":  0.7,
    ".co":  0.6,
    ".net": 0.55,
    ".org": 0.5,
    ".app": 0.5,
    ".dev": 0.45,
    ".ai":  0.65,
    ".xyz": 0.2,
    ".biz": 0.2,
}

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HEADERS = {"User-Agent": _USER_AGENT}


def _get(url: str, timeout: int = 12, **kwargs) -> requests.Response:
    return requests.get(url, headers=_HEADERS, timeout=timeout, **kwargs)


def _iana_whois_raw(domain: str) -> str:
    """Send a raw WHOIS query to whois.iana.org and return the text response."""
    try:
        with socket.create_connection(("whois.iana.org", 43), timeout=10) as s:
            s.sendall((domain + "\r\n").encode())
            chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")
    except Exception as exc:
        return f"ERROR: {exc}"


def _whois_server_for(tld: str) -> str | None:
    """Discover the authoritative WHOIS server for a TLD from IANA."""
    raw = _iana_whois_raw(tld)
    m = re.search(r"whois:\s+(\S+)", raw, re.IGNORECASE)
    return m.group(1) if m else None


def _raw_whois(domain: str) -> str:
    """Query the registrar WHOIS server directly."""
    tld = domain.split(".")[-1]
    server = _whois_server_for(tld) or f"whois.{tld}.{tld}"
    try:
        with socket.create_connection((server, 43), timeout=10) as s:
            s.sendall((domain + "\r\n").encode())
            chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")
    except Exception as exc:
        return f"ERROR: {exc}"


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def check_domain_available(domain: str) -> dict[str, Any]:
    """
    Perform a WHOIS lookup via whois.iana.org socket query and determine
    whether the domain is available.

    Returns a dict with keys: available (bool), registrar (str), expires (str).
    """
    domain = domain.strip().lower()
    raw = _raw_whois(domain)

    if raw.startswith("ERROR:"):
        return {"available": None, "registrar": "unknown", "expires": "unknown", "error": raw}

    not_found_phrases = [
        "no match for",
        "not found",
        "no entries found",
        "domain not found",
        "status: free",
        "is free",
        "available",
    ]
    lower_raw = raw.lower()
    available = any(phrase in lower_raw for phrase in not_found_phrases)

    registrar = "unknown"
    for pattern in [r"registrar:\s+(.+)", r"registrar name:\s+(.+)"]:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            registrar = m.group(1).strip()
            break

    expires = "unknown"
    for pattern in [
        r"registry expiry date:\s+(.+)",
        r"expiration date:\s+(.+)",
        r"expires:\s+(.+)",
        r"expiry date:\s+(.+)",
    ]:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            expires = m.group(1).strip()
            break

    return {"available": available, "registrar": registrar, "expires": expires}


def suggest_domain_names(
    keyword: str,
    extensions: list[str] | None = None,
) -> dict[str, Any]:
    """
    Generate domain name ideas from a keyword, check availability, and return JSON.

    Returns {keyword, suggestions: [{domain, available, registrar, expires}]}.
    """
    if extensions is None:
        extensions = [".com", ".io", ".co", ".net"]

    keyword = re.sub(r"[^a-z0-9-]", "", keyword.lower().replace(" ", "-"))
    prefixes = ["", "get", "try", "use", "go", "my", "the"]
    suffixes = ["", "app", "hq", "hub", "io", "ai", "pro"]

    candidates: list[str] = []
    for ext in extensions:
        for pre in prefixes:
            for suf in suffixes:
                name = f"{pre}{keyword}{suf}".strip("-")
                candidates.append(f"{name}{ext}")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    suggestions = []
    for domain in unique[:30]:
        info = check_domain_available(domain)
        suggestions.append({"domain": domain, **info})

    return {"keyword": keyword, "suggestions": suggestions}


def whois_lookup(domain: str) -> dict[str, Any]:
    """
    Parse WHOIS data for a domain.

    Returns {registrar, created, expires, nameservers}.
    """
    domain = domain.strip().lower()
    raw = _raw_whois(domain)

    def first_match(patterns: list[str]) -> str:
        for pat in patterns:
            m = re.search(pat, raw, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return "unknown"

    registrar = first_match([r"registrar:\s+(.+)", r"registrar name:\s+(.+)"])
    created = first_match([
        r"creation date:\s+(.+)",
        r"created:\s+(.+)",
        r"registered on:\s+(.+)",
    ])
    expires = first_match([
        r"registry expiry date:\s+(.+)",
        r"expiration date:\s+(.+)",
        r"expires:\s+(.+)",
        r"expiry date:\s+(.+)",
    ])

    nameservers: list[str] = re.findall(r"name server:\s+(\S+)", raw, re.IGNORECASE)
    if not nameservers:
        nameservers = re.findall(r"nserver:\s+(\S+)", raw, re.IGNORECASE)

    return {
        "domain": domain,
        "registrar": registrar,
        "created": created,
        "expires": expires,
        "nameservers": [ns.lower() for ns in nameservers],
    }


def dns_lookup(domain: str, record_type: str = "A") -> list[str]:
    """
    Perform a DNS query using socket/getaddrinfo and return a list of records.

    Supports A, AAAA, MX (via SMTP banner probe fallback), CNAME heuristics.
    For richer record types the function falls back to the Google DNS-over-HTTPS API.
    """
    domain = domain.strip().lower()
    record_type = record_type.upper()

    type_map = {"A": 1, "AAAA": 28, "MX": 15, "CNAME": 5, "TXT": 16, "NS": 2}

    if record_type in ("A", "AAAA"):
        family = socket.AF_INET if record_type == "A" else socket.AF_INET6
        try:
            results = socket.getaddrinfo(domain, None, family)
            return list({r[4][0] for r in results})
        except socket.gaierror:
            return []

    # Fall back to Google DNS-over-HTTPS for other record types
    try:
        r_type = type_map.get(record_type, 1)
        url = f"https://dns.google/resolve?name={domain}&type={r_type}"
        resp = _get(url, timeout=8)
        data = resp.json()
        answers = data.get("Answer", [])
        return [a.get("data", "") for a in answers]
    except Exception:
        return []


def check_ssl_cert(domain: str) -> dict[str, Any]:
    """
    Connect via TLS and return certificate validity, expiry date, and issuer.
    """
    domain = domain.strip().lower()
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as tls:
                cert = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return {"domain": domain, "valid": False, "error": str(exc)}
    except Exception as exc:
        return {"domain": domain, "valid": False, "error": str(exc)}

    not_after_str = cert.get("notAfter", "")
    not_before_str = cert.get("notBefore", "")
    issuer_dict = dict(x[0] for x in cert.get("issuer", []))

    try:
        not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(timezone.utc)
        days_left = (not_after - now).days
        valid = days_left > 0
    except ValueError:
        days_left = -1
        valid = False

    return {
        "domain": domain,
        "valid": valid,
        "not_before": not_before_str,
        "not_after": not_after_str,
        "days_remaining": days_left,
        "issuer": issuer_dict.get("organizationName", "unknown"),
        "subject": dict(x[0] for x in cert.get("subject", [])).get("commonName", domain),
        "san": cert.get("subjectAltName", []),
    }


def measure_page_load(url: str) -> dict[str, Any]:
    """
    Measure time-to-first-byte and total content size for a URL.

    Returns {url, ttfb_ms, total_ms, content_bytes, status_code}.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    t0 = time.perf_counter()
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20, stream=True)
        ttfb = time.perf_counter() - t0

        content = b""
        for chunk in resp.iter_content(chunk_size=8192):
            content += chunk
        total = time.perf_counter() - t0

        return {
            "url": url,
            "status_code": resp.status_code,
            "ttfb_ms": round(ttfb * 1000, 1),
            "total_ms": round(total * 1000, 1),
            "content_bytes": len(content),
            "content_kb": round(len(content) / 1024, 2),
        }
    except Exception as exc:
        return {"url": url, "error": str(exc)}


def check_broken_links(url: str, max_links: int = 50) -> dict[str, Any]:
    """
    Fetch a page, extract internal links, and check which are broken.

    Returns {base_url, ok: [str], broken: [str], errors: {url: reason}}.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = _get(url)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        return {"base_url": url, "error": str(exc), "ok": [], "broken": []}

    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    links: list[str] = []
    for href in hrefs:
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        if href.startswith("http"):
            if parsed.netloc in href:
                links.append(href)
        else:
            links.append(urllib.parse.urljoin(base, href))

    links = list(dict.fromkeys(links))[:max_links]

    ok: list[str] = []
    broken: list[str] = []
    errors: dict[str, str] = {}

    for link in links:
        try:
            r = requests.head(link, headers=_HEADERS, timeout=8, allow_redirects=True)
            if r.status_code < 400:
                ok.append(link)
            else:
                broken.append(link)
                errors[link] = f"HTTP {r.status_code}"
        except Exception as exc:
            broken.append(link)
            errors[link] = str(exc)

    return {"base_url": url, "checked": len(links), "ok": ok, "broken": broken, "errors": errors}


def estimate_domain_value(domain: str) -> dict[str, Any]:
    """
    Estimate domain value using heuristics: length, extension, keyword presence,
    pronounceability, and numeric penalty.

    Returns {domain, estimated_value_usd, factors}.
    """
    domain = domain.strip().lower()
    name_part = domain.split(".")[0]
    ext = "." + domain.split(".")[-1] if "." in domain else ".com"

    # Base score by length
    length = len(name_part)
    if length <= 3:
        base = 5000
    elif length <= 5:
        base = 2000
    elif length <= 7:
        base = 800
    elif length <= 10:
        base = 300
    elif length <= 15:
        base = 100
    else:
        base = 30

    # Extension multiplier
    ext_mult = _PREMIUM_EXTENSIONS.get(ext, 0.15)

    # Keyword bonus
    keyword_bonus = 1.0
    for kw in _COMMON_KEYWORDS:
        if kw in name_part:
            keyword_bonus = 1.4
            break

    # Numeric penalty
    num_penalty = 0.6 if re.search(r"\d", name_part) else 1.0

    # Hyphen penalty
    hyphen_penalty = 0.5 if "-" in name_part else 1.0

    # Vowel ratio heuristic (pronounceability)
    vowels = sum(1 for c in name_part if c in "aeiou")
    vowel_ratio = vowels / max(len(name_part), 1)
    pronounce = 1.2 if 0.25 <= vowel_ratio <= 0.55 else 0.8

    value = base * ext_mult * keyword_bonus * num_penalty * hyphen_penalty * pronounce

    return {
        "domain": domain,
        "estimated_value_usd": round(value, 2),
        "factors": {
            "base": base,
            "extension_multiplier": ext_mult,
            "keyword_bonus": keyword_bonus,
            "numeric_penalty": num_penalty,
            "hyphen_penalty": hyphen_penalty,
            "pronounceability": pronounce,
        },
    }


def cloudflare_zones(email: str, api_key: str) -> dict[str, Any]:
    """
    List all Cloudflare zones (domains) for the given account.

    Returns {zones: [{id, name, status, nameservers}]}.
    """
    headers = {
        "X-Auth-Email": email,
        "X-Auth-Key": api_key,
        "Content-Type": "application/json",
    }
    zones = []
    page = 1
    while True:
        try:
            resp = requests.get(
                "https://api.cloudflare.com/client/v4/zones",
                headers=headers,
                params={"page": page, "per_page": 50},
                timeout=12,
            )
            data = resp.json()
        except Exception as exc:
            return {"error": str(exc), "zones": []}

        if not data.get("success"):
            return {"error": data.get("errors", "unknown error"), "zones": []}

        for z in data.get("result", []):
            zones.append({
                "id": z.get("id"),
                "name": z.get("name"),
                "status": z.get("status"),
                "nameservers": z.get("name_servers", []),
            })

        info = data.get("result_info", {})
        if page >= info.get("total_pages", 1):
            break
        page += 1

    return {"total": len(zones), "zones": zones}


def namecheap_domains(username: str, api_key: str) -> dict[str, Any]:
    """
    List all domains in a Namecheap account using the Namecheap API.

    Returns {domains: [{name, expires, auto_renew, locked}]}.
    """
    try:
        client_ip = _get("https://api.ipify.org", timeout=6).text.strip()
    except Exception:
        client_ip = "127.0.0.1"

    params = {
        "ApiUser": username,
        "ApiKey": api_key,
        "UserName": username,
        "ClientIp": client_ip,
        "Command": "namecheap.domains.getList",
        "PageSize": "100",
        "Page": "1",
    }

    domains: list[dict[str, Any]] = []
    page = 1
    while True:
        params["Page"] = str(page)
        try:
            resp = _get("https://api.namecheap.com/xml.response", params=params, timeout=15)
            text = resp.text
        except Exception as exc:
            return {"error": str(exc), "domains": []}

        # Parse domain names from XML response
        for m in re.finditer(r'<Domain\s+([^/]+)/>', text):
            attrs_str = m.group(1)
            name = re.search(r'Name="([^"]+)"', attrs_str)
            expires = re.search(r'Expires="([^"]+)"', attrs_str)
            auto_renew = re.search(r'AutoRenew="([^"]+)"', attrs_str)
            locked = re.search(r'IsLocked="([^"]+)"', attrs_str)
            domains.append({
                "name": name.group(1) if name else "unknown",
                "expires": expires.group(1) if expires else "unknown",
                "auto_renew": auto_renew.group(1) == "true" if auto_renew else False,
                "locked": locked.group(1) == "true" if locked else False,
            })

        total_m = re.search(r'TotalItems="(\d+)"', text)
        total = int(total_m.group(1)) if total_m else len(domains)
        if len(domains) >= total:
            break
        page += 1

    return {"total": len(domains), "domains": domains}


def check_domain_da_pa(domain: str) -> dict[str, Any]:
    """
    Estimate Domain Authority and Page Authority.

    Tries the Moz Link Explorer API if credentials are available via environment
    variables MOZ_ACCESS_ID and MOZ_SECRET_KEY, otherwise falls back to scraping
    the Ahrefs free tools page for a rough authority score.

    Returns {domain, da, pa, backlinks, referring_domains, source}.
    """
    import os
    import base64

    domain = domain.strip().lower().lstrip("www.")
    access_id = os.environ.get("MOZ_ACCESS_ID", "")
    secret_key = os.environ.get("MOZ_SECRET_KEY", "")

    if access_id and secret_key:
        # Moz API
        token = base64.b64encode(f"{access_id}:{secret_key}".encode()).decode()
        try:
            resp = requests.post(
                "https://lsapi.seomoz.com/v2/url_metrics",
                headers={
                    "Authorization": f"Basic {token}",
                    "Content-Type": "application/json",
                },
                json={"targets": [f"https://{domain}/"]},
                timeout=12,
            )
            data = resp.json()
            result = data.get("results", [{}])[0]
            return {
                "domain": domain,
                "da": result.get("domain_authority", 0),
                "pa": result.get("page_authority", 0),
                "backlinks": result.get("link_propensity", 0),
                "referring_domains": result.get("root_domains_to_root_domain", 0),
                "source": "moz_api",
            }
        except Exception as exc:
            pass  # Fall through to scrape

    # Fallback: ahrefs free domain overview
    try:
        url = f"https://ahrefs.com/website-authority-checker/?input={domain}"
        resp = _get(url, timeout=15)
        html = resp.text
        dr_m = re.search(r'"domainRating"[:\s]+(\d+)', html)
        bl_m = re.search(r'"backlinks"[:\s]+"?([\d,]+)"?', html)
        rd_m = re.search(r'"refdomains"[:\s]+"?([\d,]+)"?', html)
        dr = int(dr_m.group(1)) if dr_m else 0
        bl = int(bl_m.group(1).replace(",", "")) if bl_m else 0
        rd = int(rd_m.group(1).replace(",", "")) if rd_m else 0
        return {
            "domain": domain,
            "da": dr,
            "pa": 0,
            "backlinks": bl,
            "referring_domains": rd,
            "source": "ahrefs_scrape",
        }
    except Exception as exc:
        return {"domain": domain, "da": 0, "pa": 0, "backlinks": 0, "referring_domains": 0,
                "source": "unavailable", "error": str(exc)}


def generate_business_names(
    niche: str, style: str = "professional", num: int = 20
) -> dict[str, Any]:
    """
    Generate brandable business/domain name ideas for a given niche and style.

    Styles: professional, playful, modern, tech, classic.
    Returns {niche, style, names: [str]}.
    """
    niche_word = re.sub(r"[^a-z0-9]", "", niche.lower().replace(" ", ""))[:12]

    style_prefixes = {
        "professional": ["apex", "prime", "elite", "core", "peak", "nexus", "vero", "alto"],
        "playful":      ["zap", "wiz", "pop", "hey", "yay", "fun", "hop", "buzz"],
        "modern":       ["qr", "lyt", "vy", "nxt", "xo", "vyr", "kyn", "zyn"],
        "tech":         ["byte", "flux", "node", "dev", "code", "api", "stack", "bit"],
        "classic":      ["sterling", "crown", "anchor", "herald", "crest", "manor"],
    }
    style_suffixes = {
        "professional": ["group", "corp", "advisory", "partners", "solutions", "global"],
        "playful":      ["ify", "ly", "oo", "ie", "ster", "er", "hub"],
        "modern":       ["io", "ai", "ux", "fx", "os", "iq"],
        "tech":         ["labs", "works", "soft", "tech", "ware", "sys"],
        "classic":      ["& co", "associates", "collective", "house", "guild"],
    }

    prefixes = style_prefixes.get(style, style_prefixes["professional"])
    suffixes = style_suffixes.get(style, style_suffixes["professional"])

    names: list[str] = []
    # prefix + niche
    for p in prefixes:
        names.append(f"{p}{niche_word}".capitalize())
    # niche + suffix
    for s in suffixes:
        names.append(f"{niche_word}{s}".capitalize())
    # prefix + niche + suffix
    for p in prefixes[:4]:
        for s in suffixes[:4]:
            names.append(f"{p}{niche_word}{s}".capitalize())

    # Deduplicate, trim to num
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    return {"niche": niche, "style": style, "names": unique[:num]}


def check_trademark(name: str, country: str = "US") -> dict[str, Any]:
    """
    Search the USPTO TSDR (Trademark Status and Document Retrieval) for conflicts.

    Returns {name, country, conflicts: [{mark, status, owner, serial}]}.
    """
    query = urllib.parse.quote(name)
    # USPTO TESS search endpoint
    search_url = (
        f"https://tmsearch.uspto.gov/search/search-information"
        f"?searchInput={query}&searchType=trademark"
    )
    try:
        resp = _get(search_url, timeout=15)
        data = resp.json()
        marks_raw = data.get("trademarkData", {}).get("trademarks", [])
    except Exception:
        # Fallback to HTML scrape of TESS
        try:
            url = (
                "https://tmsearch.uspto.gov/bin/showfield"
                f"?f=toc&state=4803:&p_search=searchss&p_ALL={query}"
            )
            resp = _get(url, timeout=15)
            html = resp.text
            serials = re.findall(r'(\d{8})', html)
            conflicts = [{"serial": s, "mark": "unknown", "status": "unknown", "owner": "unknown"}
                         for s in serials[:10]]
            return {"name": name, "country": country, "conflicts": conflicts,
                    "source": "tess_scrape"}
        except Exception as exc:
            return {"name": name, "country": country, "conflicts": [], "error": str(exc)}

    conflicts = []
    for mark in marks_raw[:20]:
        conflicts.append({
            "mark":   mark.get("markIdentification", ""),
            "status": mark.get("legalEntityTypeName", ""),
            "owner":  mark.get("partyName", ""),
            "serial": mark.get("serialNumber", ""),
        })

    return {"name": name, "country": country, "conflicts": conflicts}


def website_technology_stack(url: str) -> dict[str, Any]:
    """
    Detect CMS, frameworks, analytics, CDN, and other technologies from
    HTTP response headers and HTML body.

    Returns {url, technologies: {cms, frameworks, analytics, cdn, server, ...}}.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = _get(url, timeout=15)
        html = resp.text.lower()
        headers = {k.lower(): v for k, v in resp.headers.items()}
    except Exception as exc:
        return {"url": url, "error": str(exc), "technologies": {}}

    tech: dict[str, Any] = {
        "cms": "unknown",
        "frameworks": [],
        "analytics": [],
        "cdn": "unknown",
        "server": headers.get("server", "unknown"),
        "language": "unknown",
        "ecommerce": "none",
        "js_libraries": [],
        "security_headers": [],
    }

    # CMS detection
    cms_signatures = {
        "WordPress":  ["/wp-content/", "/wp-includes/", "wp-json"],
        "Joomla":     ["joomla", "/components/com_"],
        "Drupal":     ["drupal", "/sites/default/"],
        "Shopify":    ["shopify", "cdn.shopify.com"],
        "Wix":        ["wix.com", "_wix_"],
        "Squarespace":["squarespace.com", "squarespace"],
        "Ghost":      ["ghost.org", "ghost/"],
        "Magento":    ["magento", "mage/"],
        "PrestaShop": ["prestashop", "/modules/"],
        "Webflow":    ["webflow.com", "webflow"],
    }
    for cms, sigs in cms_signatures.items():
        if any(s in html for s in sigs):
            tech["cms"] = cms
            break

    # JS frameworks
    fw_signatures = {
        "React":      ["react", "__react"],
        "Vue.js":     ["vue.js", "__vue"],
        "Angular":    ["angular", "ng-version"],
        "Next.js":    ["_next/", "__next"],
        "Nuxt.js":    ["__nuxt", "_nuxt/"],
        "Svelte":     ["svelte"],
        "jQuery":     ["jquery"],
        "Bootstrap":  ["bootstrap"],
    }
    for fw, sigs in fw_signatures.items():
        if any(s in html for s in sigs):
            tech["frameworks"].append(fw)

    # Analytics
    analytics_signatures = {
        "Google Analytics":   ["google-analytics.com", "gtag", "ga.js"],
        "Google Tag Manager": ["googletagmanager.com"],
        "Facebook Pixel":     ["connect.facebook.net", "fbevents.js"],
        "Hotjar":             ["hotjar.com", "hotjar"],
        "Mixpanel":           ["mixpanel.com"],
        "Segment":            ["segment.com", "analytics.js"],
        "Heap":               ["heapanalytics.com"],
        "Plausible":          ["plausible.io"],
    }
    for tool, sigs in analytics_signatures.items():
        if any(s in html for s in sigs):
            tech["analytics"].append(tool)

    # CDN
    cdn_signatures = {
        "Cloudflare": ["cloudflare", "cf-ray"],
        "Fastly":     ["fastly"],
        "Akamai":     ["akamai"],
        "AWS CloudFront": ["cloudfront.net", "x-amz-cf"],
        "Vercel":     ["vercel", "x-vercel"],
        "Netlify":    ["netlify"],
    }
    server_str = " ".join([
        headers.get("server", ""),
        headers.get("cf-ray", ""),
        headers.get("x-served-by", ""),
        headers.get("via", ""),
        html[:2000],
    ]).lower()
    for cdn, sigs in cdn_signatures.items():
        if any(s in server_str for s in sigs):
            tech["cdn"] = cdn
            break

    # Server language
    x_powered = headers.get("x-powered-by", "").lower()
    if "php" in x_powered:
        tech["language"] = "PHP"
    elif "asp.net" in x_powered:
        tech["language"] = "ASP.NET"
    elif "ruby" in x_powered or "rails" in x_powered:
        tech["language"] = "Ruby"
    elif "python" in x_powered or "django" in x_powered or "flask" in x_powered:
        tech["language"] = "Python"
    elif "node" in x_powered or "express" in x_powered:
        tech["language"] = "Node.js"

    # Security headers
    security_hdrs = [
        "strict-transport-security",
        "content-security-policy",
        "x-frame-options",
        "x-content-type-options",
        "referrer-policy",
        "permissions-policy",
    ]
    tech["security_headers"] = [h for h in security_hdrs if h in headers]

    return {"url": url, "status_code": resp.status_code, "technologies": tech}
