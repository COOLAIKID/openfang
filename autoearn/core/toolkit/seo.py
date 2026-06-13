from __future__ import annotations

import json
import math
import re
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = Path("/home/user/openfang/autoearn/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "dare",
    "it", "its", "this", "that", "these", "those", "i", "we", "you",
    "he", "she", "they", "me", "us", "him", "her", "them", "my", "our",
    "your", "his", "their", "what", "which", "who", "whom", "when",
    "where", "why", "how", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "no", "not", "only", "same",
    "so", "than", "too", "very", "just", "because", "as", "until",
    "while", "although", "though", "if", "then", "also",
}


def _fetch_html(url: str, timeout: int = 10) -> tuple[str, BeautifulSoup | None]:
    """Fetch a URL and return (raw_html, soup). Returns ('', None) on error."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return resp.text, soup
    except Exception as exc:
        return "", None


def _keyword_density(text: str, keyword: str) -> float:
    """Compute keyword density as a percentage."""
    words = re.findall(r"\w+", text.lower())
    if not words:
        return 0.0
    kw_words = re.findall(r"\w+", keyword.lower())
    # Count sliding-window matches for multi-word keywords
    count = 0
    n = len(kw_words)
    for i in range(len(words) - n + 1):
        if words[i : i + n] == kw_words:
            count += 1
    return round((count / len(words)) * 100, 2)


def analyze_on_page(url: str, keyword: str) -> str:
    """Fetch a page and score it for on-page SEO against a target keyword."""
    raw_html, soup = _fetch_html(url)
    if soup is None:
        return json.dumps({"error": f"Could not fetch {url}"})

    kw_lower = keyword.lower()
    report: dict[str, Any] = {"url": url, "keyword": keyword, "score": 0, "issues": [], "passed": []}

    # Title analysis
    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    title_score = 0
    if not title_text:
        report["issues"].append("Missing <title> tag")
    else:
        if kw_lower in title_text.lower():
            title_score += 15
            report["passed"].append("Keyword found in title")
        else:
            report["issues"].append("Keyword missing from title")
        if 50 <= len(title_text) <= 65:
            title_score += 5
            report["passed"].append(f"Title length optimal ({len(title_text)} chars)")
        elif len(title_text) < 50:
            report["issues"].append(f"Title too short ({len(title_text)} chars, aim 50-65)")
        else:
            report["issues"].append(f"Title too long ({len(title_text)} chars, aim 50-65)")
    report["title"] = {"text": title_text, "length": len(title_text), "score": title_score}

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    meta_text = meta_desc.get("content", "").strip() if meta_desc else ""
    meta_score = 0
    if not meta_text:
        report["issues"].append("Missing meta description")
    else:
        if kw_lower in meta_text.lower():
            meta_score += 10
            report["passed"].append("Keyword found in meta description")
        else:
            report["issues"].append("Keyword missing from meta description")
        if 120 <= len(meta_text) <= 160:
            meta_score += 5
            report["passed"].append(f"Meta description length optimal ({len(meta_text)} chars)")
        else:
            report["issues"].append(f"Meta description length suboptimal ({len(meta_text)} chars, aim 120-160)")
    report["meta_description"] = {"text": meta_text, "length": len(meta_text), "score": meta_score}

    # Headings
    h1_tags = soup.find_all("h1")
    h2_tags = soup.find_all("h2")
    h3_tags = soup.find_all("h3")
    heading_score = 0
    if len(h1_tags) == 1:
        heading_score += 10
        report["passed"].append("Exactly one H1 tag")
    elif len(h1_tags) == 0:
        report["issues"].append("No H1 tag found")
    else:
        report["issues"].append(f"Multiple H1 tags ({len(h1_tags)})")

    h1_text = h1_tags[0].get_text(strip=True) if h1_tags else ""
    if h1_text and kw_lower in h1_text.lower():
        heading_score += 10
        report["passed"].append("Keyword found in H1")
    elif h1_text:
        report["issues"].append("Keyword missing from H1")

    h2_texts = [h.get_text(strip=True) for h in h2_tags]
    h2_with_kw = sum(1 for t in h2_texts if kw_lower in t.lower())
    if h2_with_kw:
        heading_score += 5
        report["passed"].append(f"Keyword found in {h2_with_kw} H2 tag(s)")

    report["headings"] = {
        "h1": [h.get_text(strip=True) for h in h1_tags],
        "h2": h2_texts[:10],
        "h3": [h.get_text(strip=True) for h in h3_tags[:5]],
        "score": heading_score,
    }

    # Word count and keyword density
    body_text = soup.get_text(separator=" ")
    words = re.findall(r"\w+", body_text)
    word_count = len(words)
    density = _keyword_density(body_text, keyword)
    content_score = 0

    if word_count >= 1500:
        content_score += 10
        report["passed"].append(f"Good word count ({word_count})")
    elif word_count >= 800:
        content_score += 5
        report["issues"].append(f"Word count below 1500 ({word_count})")
    else:
        report["issues"].append(f"Thin content - word count too low ({word_count})")

    if 0.5 <= density <= 2.5:
        content_score += 10
        report["passed"].append(f"Keyword density optimal ({density}%)")
    elif density > 2.5:
        report["issues"].append(f"Keyword over-optimized ({density}%)")
    else:
        report["issues"].append(f"Keyword density too low ({density}%)")

    report["content"] = {"word_count": word_count, "keyword_density": density, "score": content_score}

    # Images alt text
    images = soup.find_all("img")
    imgs_missing_alt = sum(1 for img in images if not img.get("alt"))
    image_score = 0
    if images:
        if imgs_missing_alt == 0:
            image_score += 5
            report["passed"].append("All images have alt text")
        else:
            report["issues"].append(f"{imgs_missing_alt} images missing alt text")
    report["images"] = {"total": len(images), "missing_alt": imgs_missing_alt, "score": image_score}

    # Internal/external links
    links = soup.find_all("a", href=True)
    base_domain = urlparse(url).netloc
    internal = [l["href"] for l in links if urlparse(urljoin(url, l["href"])).netloc == base_domain]
    external = [l["href"] for l in links if urlparse(urljoin(url, l["href"])).netloc != base_domain and l["href"].startswith("http")]
    link_score = 0
    if len(internal) >= 3:
        link_score += 5
        report["passed"].append(f"Good internal linking ({len(internal)} links)")
    else:
        report["issues"].append(f"Low internal links ({len(internal)})")
    report["links"] = {"internal": len(internal), "external": len(external), "score": link_score}

    total_score = title_score + meta_score + heading_score + content_score + image_score + link_score
    report["score"] = min(total_score, 100)
    report["grade"] = "A" if total_score >= 80 else "B" if total_score >= 60 else "C" if total_score >= 40 else "D"
    return json.dumps(report, indent=2)


def keyword_ideas(seed_keyword: str, num: int = 20) -> str:
    """Use DuckDuckGo Autocomplete and related-search patterns to generate long-tail keywords."""
    ideas = set()
    prefixes = ["how to", "best", "top", "cheap", "free", "easy", "quick", "professional", "DIY", "guide to"]
    suffixes = ["tips", "guide", "tutorial", "examples", "ideas", "tools", "for beginners", "2024", "free", "online"]
    questions = ["what is", "how to", "why is", "when to", "where to find", "how much does", "is it worth"]

    for prefix in prefixes:
        ideas.add(f"{prefix} {seed_keyword}")
    for suffix in suffixes:
        ideas.add(f"{seed_keyword} {suffix}")
    for q in questions:
        ideas.add(f"{q} {seed_keyword}")

    # DuckDuckGo autocomplete
    try:
        resp = requests.get(
            "https://duckduckgo.com/ac/",
            params={"q": seed_keyword, "type": "list"},
            headers=HEADERS,
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                for item in data[1]:
                    ideas.add(item)
    except Exception:
        pass

    # Google Suggest via unofficial endpoint
    try:
        resp = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "q": seed_keyword},
            headers=HEADERS,
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 1:
                for item in data[1]:
                    ideas.add(item)
    except Exception:
        pass

    result = sorted(ideas)[:num]
    return json.dumps({"seed": seed_keyword, "keywords": result, "count": len(result)}, indent=2)


def check_serp_rank(keyword: str, domain: str) -> str:
    """Scrape DuckDuckGo for keyword and return the position of domain."""
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": keyword},
            headers=HEADERS,
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = soup.select(".result__a")
        domain_clean = domain.lower().replace("https://", "").replace("http://", "").rstrip("/")
        for position, result in enumerate(results, 1):
            href = result.get("href", "")
            if domain_clean in href.lower():
                return json.dumps({"keyword": keyword, "domain": domain, "position": position, "url": href})
        return json.dumps({"keyword": keyword, "domain": domain, "position": -1, "message": "Not found in top results"})
    except Exception as exc:
        return json.dumps({"error": str(exc), "keyword": keyword, "domain": domain, "position": -1})


def competitor_analysis(keyword: str, num_competitors: int = 5) -> str:
    """Get top results for a keyword and extract their SEO data, with aggregate statistics."""
    competitors = []
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": keyword},
            headers=HEADERS,
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        result_blocks = soup.select(".result")
        urls_found = []
        for block in result_blocks:
            link = block.select_one(".result__a")
            if link and link.get("href"):
                urls_found.append(link["href"])
            if len(urls_found) >= num_competitors:
                break

        for url in urls_found[:num_competitors]:
            _, page_soup = _fetch_html(url, timeout=8)
            comp: dict[str, Any] = {"url": url}
            if page_soup:
                t = page_soup.find("title")
                comp["title"] = t.get_text(strip=True) if t else ""
                m = page_soup.find("meta", attrs={"name": re.compile("description", re.I)})
                comp["meta_description"] = m.get("content", "").strip() if m else ""
                body = page_soup.get_text(separator=" ")
                wc = len(re.findall(r"\w+", body))
                comp["word_count"] = wc
                comp["h1"] = [h.get_text(strip=True) for h in page_soup.find_all("h1")]
                comp["h2_count"] = len(page_soup.find_all("h2"))
                comp["h3_count"] = len(page_soup.find_all("h3"))
                comp["keyword_density"] = _keyword_density(body, keyword)
                comp["images"] = len(page_soup.find_all("img"))
                comp["internal_links"] = len([
                    a for a in page_soup.find_all("a", href=True)
                    if not a["href"].startswith("http")
                ])
            else:
                comp["error"] = "Could not fetch page"
            competitors.append(comp)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    # Compute aggregate statistics across competitors
    word_counts = [c["word_count"] for c in competitors if "word_count" in c]
    densities = [c["keyword_density"] for c in competitors if "keyword_density" in c]
    agg: dict[str, Any] = {}
    if word_counts:
        agg["word_count"] = {
            "min": min(word_counts),
            "max": max(word_counts),
            "mean": round(statistics.mean(word_counts)),
            "median": round(statistics.median(word_counts)),
            "stdev": round(statistics.stdev(word_counts), 1) if len(word_counts) > 1 else 0,
            "recommended_target": math.ceil(statistics.mean(word_counts) * 1.1),
        }
    if densities:
        agg["keyword_density"] = {
            "min": min(densities),
            "max": max(densities),
            "mean": round(statistics.mean(densities), 2),
        }

    return json.dumps({"keyword": keyword, "aggregate": agg, "competitors": competitors}, indent=2)


def generate_sitemap(base_url: str, urls: list[str]) -> str:
    """Generate an XML sitemap string."""
    base_url = base_url.rstrip("/")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in urls:
        full_url = url if url.startswith("http") else f"{base_url}/{url.lstrip('/')}"
        lines.append("  <url>")
        lines.append(f"    <loc>{full_url}</loc>")
        lines.append(f"    <lastmod>{datetime.now(timezone.utc).strftime('%Y-%m-%d')}</lastmod>")
        lines.append("    <changefreq>weekly</changefreq>")
        lines.append("    <priority>0.8</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines)


def generate_robots_txt(allow: list[str] = None, disallow: list[str] = None) -> str:
    """Generate a robots.txt string."""
    if allow is None:
        allow = []
    if disallow is None:
        disallow = ["/admin", "/private"]
    lines = ["User-agent: *"]
    for path in disallow:
        lines.append(f"Disallow: {path}")
    for path in allow:
        lines.append(f"Allow: {path}")
    lines.append("")
    lines.append("# Sitemaps")
    lines.append("Sitemap: https://example.com/sitemap.xml")
    return "\n".join(lines)


def json_ld_article(
    title: str,
    description: str,
    author: str,
    url: str,
    date_published: str,
    image_url: str = "",
) -> str:
    """Generate JSON-LD Article schema markup."""
    schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": description,
        "author": {"@type": "Person", "name": author},
        "url": url,
        "datePublished": date_published,
        "dateModified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if image_url:
        schema["image"] = image_url
    return f'<script type="application/ld+json">\n{json.dumps(schema, indent=2)}\n</script>'


def json_ld_product(
    name: str,
    description: str,
    price: float,
    currency: str,
    url: str,
    brand: str = "",
    sku: str = "",
) -> str:
    """Generate JSON-LD Product schema markup."""
    schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "description": description,
        "url": url,
        "offers": {
            "@type": "Offer",
            "price": str(price),
            "priceCurrency": currency,
            "availability": "https://schema.org/InStock",
        },
    }
    if brand:
        schema["brand"] = {"@type": "Brand", "name": brand}
    if sku:
        schema["sku"] = sku
    return f'<script type="application/ld+json">\n{json.dumps(schema, indent=2)}\n</script>'


def json_ld_faq(questions_answers: list[dict[str, str]]) -> str:
    """Generate JSON-LD FAQPage schema from a list of {q, a} dicts."""
    entities = [
        {
            "@type": "Question",
            "name": qa.get("q", ""),
            "acceptedAnswer": {"@type": "Answer", "text": qa.get("a", "")},
        }
        for qa in questions_answers
    ]
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": entities,
    }
    return f'<script type="application/ld+json">\n{json.dumps(schema, indent=2)}\n</script>'


def extract_meta_tags(url: str) -> str:
    """Fetch a page and extract all meta tags as JSON."""
    _, soup = _fetch_html(url)
    if soup is None:
        return json.dumps({"error": f"Could not fetch {url}"})
    meta_tags = []
    for tag in soup.find_all("meta"):
        attrs = dict(tag.attrs)
        meta_tags.append(attrs)
    # Also extract Open Graph and Twitter Card
    og_data = {}
    tc_data = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property", "")
        name = tag.get("name", "")
        content = tag.get("content", "")
        if prop.startswith("og:"):
            og_data[prop] = content
        if name.startswith("twitter:"):
            tc_data[name] = content
    return json.dumps({"url": url, "meta_tags": meta_tags, "open_graph": og_data, "twitter_card": tc_data}, indent=2)


def internal_link_suggestions(content: str, existing_urls: list[str]) -> str:
    """Find anchor text opportunities in content for given URLs."""
    suggestions = []
    for url in existing_urls:
        # Extract likely keyword from URL path
        path = urlparse(url).path
        slug = path.strip("/").split("/")[-1]
        anchor_keyword = slug.replace("-", " ").replace("_", " ").strip()
        if not anchor_keyword:
            continue
        # Find mentions of this keyword in content
        pattern = re.compile(re.escape(anchor_keyword), re.IGNORECASE)
        matches = list(pattern.finditer(content))
        if matches:
            for match in matches[:3]:
                start = max(0, match.start() - 40)
                end = min(len(content), match.end() + 40)
                context = content[start:end].replace("\n", " ")
                suggestions.append({
                    "url": url,
                    "anchor_text": match.group(),
                    "context": f"...{context}...",
                })
    return json.dumps({"suggestions": suggestions, "count": len(suggestions)}, indent=2)


def page_speed_score(url: str) -> str:
    """Score page speed via Google PageSpeed API (if key configured) or estimate from page size."""
    api_key = ""
    try:
        import os
        api_key = os.environ.get("GOOGLE_PAGESPEED_KEY", "")
    except Exception:
        pass

    if api_key:
        try:
            resp = requests.get(
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                params={"url": url, "key": api_key, "strategy": "mobile"},
                timeout=30,
            )
            data = resp.json()
            score = data.get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score", 0)
            fcp = data.get("lighthouseResult", {}).get("audits", {}).get("first-contentful-paint", {}).get("displayValue", "")
            return json.dumps({"url": url, "performance_score": int(score * 100), "fcp": fcp, "source": "pagespeed_api"})
        except Exception as exc:
            pass

    # Fallback: estimate from download speed
    try:
        start = time.time()
        resp = requests.get(url, headers=HEADERS, timeout=15)
        elapsed = time.time() - start
        size_kb = len(resp.content) / 1024
        # Rough heuristic: score based on load time and size
        time_score = max(0, 100 - int(elapsed * 20))
        size_score = max(0, 100 - int(size_kb / 20))
        estimated = int((time_score + size_score) / 2)
        return json.dumps({
            "url": url,
            "estimated_score": estimated,
            "load_time_sec": round(elapsed, 2),
            "page_size_kb": round(size_kb, 1),
            "source": "estimated",
            "note": "Set GOOGLE_PAGESPEED_KEY env var for accurate scores",
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "url": url})


def backlink_opportunities(niche: str) -> str:
    """Search for guest post / link building opportunities in a niche."""
    queries = [
        f"{niche} write for us",
        f"{niche} guest post",
        f"{niche} submit article",
        f"{niche} become a contributor",
        f"{niche} accept guest posts",
    ]
    opportunities = []
    for query in queries[:3]:  # Limit to avoid rate limiting
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=HEADERS,
                timeout=10,
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            for result in soup.select(".result")[:3]:
                link = result.select_one(".result__a")
                snippet = result.select_one(".result__snippet")
                if link:
                    opportunities.append({
                        "url": link.get("href", ""),
                        "title": link.get_text(strip=True),
                        "snippet": snippet.get_text(strip=True) if snippet else "",
                        "query": query,
                    })
            time.sleep(0.5)
        except Exception:
            pass
    seen = set()
    unique = []
    for op in opportunities:
        if op["url"] not in seen:
            seen.add(op["url"])
            unique.append(op)
    return json.dumps({"niche": niche, "opportunities": unique, "count": len(unique)}, indent=2)


def heading_structure(html: str) -> str:
    """Extract h1/h2/h3 hierarchy from HTML string."""
    soup = BeautifulSoup(html, "html.parser")
    structure = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        structure.append({
            "level": int(tag.name[1]),
            "tag": tag.name,
            "text": tag.get_text(strip=True),
        })
    # Build nested hierarchy
    hierarchy: list[dict] = []
    stack: list[dict] = []
    for item in structure:
        node = {"level": item["level"], "tag": item["tag"], "text": item["text"], "children": []}
        while stack and stack[-1]["level"] >= item["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            hierarchy.append(node)
        stack.append(node)
    return json.dumps({"structure": hierarchy, "flat": structure}, indent=2)


def lsi_keywords(keyword: str) -> str:
    """Generate LSI (Latent Semantic Indexing) keyword variations."""
    kw_words = keyword.lower().split()
    lsi_terms = []

    # Synonym-based variations (small built-in thesaurus for common patterns)
    synonym_map = {
        "best": ["top", "leading", "greatest", "finest", "ultimate", "premier"],
        "buy": ["purchase", "get", "acquire", "order", "shop for"],
        "guide": ["tutorial", "walkthrough", "instructions", "manual", "how-to"],
        "tips": ["advice", "strategies", "techniques", "tricks", "hacks"],
        "free": ["no-cost", "complimentary", "gratis", "zero-cost"],
        "online": ["digital", "web-based", "internet", "virtual"],
        "make": ["create", "build", "generate", "produce", "develop"],
        "money": ["income", "revenue", "earnings", "profit", "cash"],
        "learn": ["discover", "understand", "master", "study"],
        "tool": ["software", "platform", "solution", "resource", "app"],
    }

    for word in kw_words:
        if word in synonym_map:
            for syn in synonym_map[word]:
                variation = keyword.lower().replace(word, syn)
                lsi_terms.append(variation)

    # Related concepts by appending common modifiers
    modifiers_before = ["affordable", "professional", "advanced", "complete", "comprehensive", "simple"]
    modifiers_after = ["strategy", "course", "review", "comparison", "checklist", "template"]
    for m in modifiers_before:
        lsi_terms.append(f"{m} {keyword}")
    for m in modifiers_after:
        lsi_terms.append(f"{keyword} {m}")

    # Try fetching related from DuckDuckGo
    try:
        resp = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "q": keyword},
            headers=HEADERS,
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 1:
                for item in data[1][:10]:
                    lsi_terms.append(item)
    except Exception:
        pass

    # Deduplicate and clean
    seen = set()
    unique = []
    for term in lsi_terms:
        t = term.strip().lower()
        if t and t != keyword.lower() and t not in seen:
            seen.add(t)
            unique.append(t)

    return json.dumps({"keyword": keyword, "lsi_keywords": unique[:30], "count": len(unique[:30])}, indent=2)
