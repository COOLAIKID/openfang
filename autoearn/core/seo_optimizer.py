"""
SEO optimizer — on-page analysis, meta generation, internal linking, and
schema markup. Agents use this to optimize content before publishing.

No external APIs required for basic analysis. All results persisted in SQLite
so agents can track improvements over time.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .config import cfg

# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    db_path = cfg("seo.db_path", fallback="autoearn.db")
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _ensure_schema(_conn)
    return _conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS seo_pages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT    NOT NULL UNIQUE,
            title           TEXT    NOT NULL DEFAULT '',
            meta_description TEXT   NOT NULL DEFAULT '',
            h1              TEXT    NOT NULL DEFAULT '',
            target_keyword  TEXT    NOT NULL DEFAULT '',
            word_count      INTEGER NOT NULL DEFAULT 0,
            score           REAL    NOT NULL DEFAULT 0.0,
            issues          TEXT    NOT NULL DEFAULT '[]',
            suggestions     TEXT    NOT NULL DEFAULT '[]',
            last_analyzed   REAL    NOT NULL,
            content_hash    TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS seo_audits (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT    NOT NULL,
            ts              REAL    NOT NULL,
            score           REAL    NOT NULL DEFAULT 0.0,
            checks          TEXT    NOT NULL DEFAULT '{}',
            issues          TEXT    NOT NULL DEFAULT '[]',
            suggestions     TEXT    NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS seo_redirects (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_url        TEXT    NOT NULL UNIQUE,
            to_url          TEXT    NOT NULL,
            status_code     INTEGER NOT NULL DEFAULT 301,
            reason          TEXT    NOT NULL DEFAULT '',
            created_at      REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS seo_internal_links (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_url        TEXT    NOT NULL,
            to_url          TEXT    NOT NULL,
            anchor_text     TEXT    NOT NULL DEFAULT '',
            discovered_at   REAL    NOT NULL,
            UNIQUE(from_url, to_url)
        );

        CREATE TABLE IF NOT EXISTS seo_schema_markup (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT    NOT NULL,
            schema_type     TEXT    NOT NULL,
            json_ld         TEXT    NOT NULL DEFAULT '{}',
            created_at      REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS seo_meta_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT    NOT NULL,
            title           TEXT    NOT NULL DEFAULT '',
            meta_description TEXT   NOT NULL DEFAULT '',
            changed_at      REAL    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_seo_pages_url  ON seo_pages(url);
        CREATE INDEX IF NOT EXISTS idx_seo_pages_kw   ON seo_pages(target_keyword);
        CREATE INDEX IF NOT EXISTS idx_seo_audits_url ON seo_audits(url);
        CREATE INDEX IF NOT EXISTS idx_seo_il_from    ON seo_internal_links(from_url);
        CREATE INDEX IF NOT EXISTS idx_seo_il_to      ON seo_internal_links(to_url);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SEOCheck:
    name: str
    passed: bool
    score: float
    message: str
    severity: str = "warning"  # info | warning | error

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "score": self.score,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class SEOReport:
    url: str
    title: str
    target_keyword: str
    checks: list[SEOCheck] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        if not self.checks:
            return 0.0
        return round(sum(c.score for c in self.checks) / len(self.checks) * 100, 1)

    @property
    def grade(self) -> str:
        s = self.score
        if s >= 90:
            return "A"
        if s >= 80:
            return "B"
        if s >= 70:
            return "C"
        if s >= 60:
            return "D"
        return "F"

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "target_keyword": self.target_keyword,
            "score": self.score,
            "grade": self.grade,
            "checks": [c.to_dict() for c in self.checks],
            "issues": self.issues,
            "suggestions": self.suggestions,
            "passed_checks": sum(1 for c in self.checks if c.passed),
            "total_checks": len(self.checks),
        }


# ---------------------------------------------------------------------------
# Text analysis helpers
# ---------------------------------------------------------------------------

def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _keyword_density(text: str, keyword: str) -> float:
    if not keyword or not text:
        return 0.0
    total_words = _count_words(text)
    if total_words == 0:
        return 0.0
    kw_lower = keyword.lower()
    text_lower = text.lower()
    count = len(re.findall(re.escape(kw_lower), text_lower))
    return round(count / total_words * 100, 2)


def _reading_ease(text: str) -> float:
    """Flesch Reading Ease score (approximate)."""
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = re.findall(r"\b\w+\b", text)
    syllables = sum(_count_syllables(w) for w in words)

    if not sentences or not words:
        return 0.0

    asl = len(words) / len(sentences)
    asw = syllables / len(words)
    score = 206.835 - 1.015 * asl - 84.6 * asw
    return round(max(0.0, min(100.0, score)), 1)


def _count_syllables(word: str) -> int:
    word = word.lower()
    count = len(re.findall(r"[aeiou]+", word))
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _extract_headings(html: str) -> dict:
    """Extract heading structure from HTML."""
    result: dict[str, list[str]] = {"h1": [], "h2": [], "h3": [], "h4": []}
    for level in ("h1", "h2", "h3", "h4"):
        pattern = rf"<{level}[^>]*>(.*?)</{level}>"
        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
        result[level] = [re.sub(r"<[^>]+>", "", m).strip() for m in matches]
    return result


def _extract_links(html: str) -> list[dict]:
    """Extract all links from HTML."""
    pattern = r'<a\s+(?:[^>]*?\s+)?href=(["\'])(.*?)\1(?:[^>]*?)>(.*?)</a>'
    matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
    links = []
    for _, href, anchor in matches:
        links.append({
            "href": href.strip(),
            "anchor": re.sub(r"<[^>]+>", "", anchor).strip(),
        })
    return links


def _extract_images(html: str) -> list[dict]:
    """Extract image tags and check for alt text."""
    pattern = r'<img\s+([^>]*)>'
    matches = re.findall(pattern, html, re.IGNORECASE)
    images = []
    for attrs in matches:
        src_match = re.search(r'src=(["\'])(.*?)\1', attrs)
        alt_match = re.search(r'alt=(["\'])(.*?)\1', attrs)
        images.append({
            "src": src_match.group(2) if src_match else "",
            "alt": alt_match.group(2) if alt_match else "",
            "has_alt": bool(alt_match and alt_match.group(2).strip()),
        })
    return images


def _strip_html(html: str) -> str:
    """Remove HTML tags and return plain text."""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# On-page SEO analysis
# ---------------------------------------------------------------------------

def analyze_content(
    content: str,
    title: str = "",
    meta_description: str = "",
    target_keyword: str = "",
    url: str = "",
    is_html: bool = False,
) -> dict:
    """
    Perform a comprehensive on-page SEO analysis.

    Returns a full SEOReport dict with score, checks, issues, and suggestions.
    """
    report = SEOReport(url=url, title=title, target_keyword=target_keyword)
    plain_text = _strip_html(content) if is_html else content
    word_count = _count_words(plain_text)
    kw = target_keyword.lower()

    # --- Title checks ---
    title_len = len(title)
    report.checks.append(SEOCheck(
        name="title_length",
        passed=50 <= title_len <= 60,
        score=1.0 if 50 <= title_len <= 60 else (0.5 if 40 <= title_len <= 70 else 0.0),
        message=f"Title length: {title_len} chars (ideal: 50-60)",
        severity="warning" if title_len > 0 else "error",
    ))

    if kw:
        title_has_kw = kw in title.lower()
        report.checks.append(SEOCheck(
            name="keyword_in_title",
            passed=title_has_kw,
            score=1.0 if title_has_kw else 0.0,
            message=f"Keyword '{target_keyword}' {'found' if title_has_kw else 'missing'} in title",
            severity="error" if not title_has_kw else "info",
        ))

    # --- Meta description checks ---
    meta_len = len(meta_description)
    report.checks.append(SEOCheck(
        name="meta_description_length",
        passed=120 <= meta_len <= 160,
        score=1.0 if 120 <= meta_len <= 160 else (0.5 if 90 <= meta_len <= 170 else 0.0),
        message=f"Meta description length: {meta_len} chars (ideal: 120-160)",
        severity="warning",
    ))

    if kw and meta_description:
        meta_has_kw = kw in meta_description.lower()
        report.checks.append(SEOCheck(
            name="keyword_in_meta",
            passed=meta_has_kw,
            score=1.0 if meta_has_kw else 0.0,
            message=f"Keyword '{target_keyword}' {'found' if meta_has_kw else 'missing'} in meta description",
            severity="warning",
        ))

    # --- Content length ---
    report.checks.append(SEOCheck(
        name="word_count",
        passed=word_count >= 300,
        score=1.0 if word_count >= 1200 else (0.75 if word_count >= 600 else (0.5 if word_count >= 300 else 0.0)),
        message=f"Word count: {word_count} words (ideal: 1200+ for competitive topics)",
        severity="warning" if word_count < 300 else "info",
    ))

    # --- Keyword density ---
    if kw and plain_text:
        density = _keyword_density(plain_text, target_keyword)
        density_ok = 1.0 <= density <= 3.0
        report.checks.append(SEOCheck(
            name="keyword_density",
            passed=density_ok,
            score=1.0 if density_ok else (0.5 if 0.5 <= density <= 4.0 else 0.0),
            message=f"Keyword density: {density}% (ideal: 1-3%)",
            severity="warning",
        ))

    # --- Heading structure (HTML only) ---
    if is_html:
        headings = _extract_headings(content)
        h1_count = len(headings["h1"])
        h1_ok = h1_count == 1
        report.checks.append(SEOCheck(
            name="single_h1",
            passed=h1_ok,
            score=1.0 if h1_ok else 0.0,
            message=f"H1 count: {h1_count} (must be exactly 1)",
            severity="error" if h1_count != 1 else "info",
        ))

        if kw and headings["h1"]:
            h1_has_kw = any(kw in h.lower() for h in headings["h1"])
            report.checks.append(SEOCheck(
                name="keyword_in_h1",
                passed=h1_has_kw,
                score=1.0 if h1_has_kw else 0.0,
                message=f"Keyword {'found' if h1_has_kw else 'missing'} in H1",
                severity="error" if not h1_has_kw else "info",
            ))

        h2_count = len(headings["h2"])
        report.checks.append(SEOCheck(
            name="heading_structure",
            passed=h2_count >= 2,
            score=1.0 if h2_count >= 3 else (0.5 if h2_count >= 1 else 0.0),
            message=f"H2 headings: {h2_count} (ideal: 3+)",
            severity="warning",
        ))

        # Image alt text
        images = _extract_images(content)
        if images:
            imgs_with_alt = sum(1 for img in images if img["has_alt"])
            alt_ratio = imgs_with_alt / len(images)
            report.checks.append(SEOCheck(
                name="image_alt_text",
                passed=alt_ratio == 1.0,
                score=alt_ratio,
                message=f"Images with alt text: {imgs_with_alt}/{len(images)}",
                severity="warning" if alt_ratio < 1.0 else "info",
            ))

        # Internal links
        links = _extract_links(content)
        internal = [l for l in links if not l["href"].startswith("http")]
        report.checks.append(SEOCheck(
            name="internal_links",
            passed=len(internal) >= 2,
            score=1.0 if len(internal) >= 3 else (0.5 if len(internal) >= 1 else 0.0),
            message=f"Internal links: {len(internal)} (ideal: 3+)",
            severity="warning",
        ))

    # --- Reading ease ---
    if plain_text:
        ease = _reading_ease(plain_text)
        ease_ok = ease >= 60
        report.checks.append(SEOCheck(
            name="reading_ease",
            passed=ease_ok,
            score=1.0 if ease >= 70 else (0.5 if ease >= 50 else 0.0),
            message=f"Flesch Reading Ease: {ease} (ideal: 60+)",
            severity="warning",
        ))

    # Keyword in first 100 words
    if kw and plain_text:
        first_100 = " ".join(plain_text.split()[:100]).lower()
        kw_early = kw in first_100
        report.checks.append(SEOCheck(
            name="keyword_in_intro",
            passed=kw_early,
            score=1.0 if kw_early else 0.0,
            message=f"Keyword '{target_keyword}' {'found' if kw_early else 'missing'} in first 100 words",
            severity="warning",
        ))

    # --- Build issues and suggestions ---
    for check in report.checks:
        if not check.passed:
            if check.severity == "error":
                report.issues.append(f"[ERROR] {check.message}")
            elif check.severity == "warning":
                report.issues.append(f"[WARNING] {check.message}")

    if word_count < 600:
        report.suggestions.append(f"Expand content to at least 600 words (currently {word_count})")
    if not meta_description:
        report.suggestions.append("Add a meta description (120-160 characters)")
    if kw and not title:
        report.suggestions.append(f"Add a title tag containing your target keyword '{target_keyword}'")
    if report.score < 70:
        report.suggestions.append("Focus on addressing ERROR-level issues first to boost score")

    result = report.to_dict()
    result["word_count"] = word_count

    # Persist if URL provided
    if url:
        import hashlib
        content_hash = hashlib.sha256(plain_text.encode()).hexdigest()[:16]
        db = _db()
        db.execute(
            """INSERT INTO seo_pages
               (url, title, meta_description, target_keyword, word_count,
                score, issues, suggestions, last_analyzed, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET
                 title=excluded.title,
                 meta_description=excluded.meta_description,
                 word_count=excluded.word_count,
                 score=excluded.score,
                 issues=excluded.issues,
                 suggestions=excluded.suggestions,
                 last_analyzed=excluded.last_analyzed,
                 content_hash=excluded.content_hash""",
            (url, title, meta_description, target_keyword, word_count,
             report.score, json.dumps(report.issues), json.dumps(report.suggestions),
             time.time(), content_hash),
        )
        db.execute(
            """INSERT INTO seo_audits
               (url, ts, score, checks, issues, suggestions)
               VALUES (?,?,?,?,?,?)""",
            (url, time.time(), report.score,
             json.dumps([c.to_dict() for c in report.checks]),
             json.dumps(report.issues), json.dumps(report.suggestions)),
        )
        db.commit()

    return result


# ---------------------------------------------------------------------------
# Meta tag generation
# ---------------------------------------------------------------------------

def generate_title(
    topic: str,
    keyword: str = "",
    brand: str = "",
    style: str = "standard",
) -> list[str]:
    """
    Generate SEO-optimized title tag variations.
    Returns a list of 5 title options.
    """
    kw = keyword or topic
    templates = {
        "standard": [
            f"{kw}: The Complete Guide ({_current_year()})",
            f"How to {topic} — Step-by-Step Guide",
            f"{kw} — Everything You Need to Know",
            f"The Best {kw} Tips & Strategies for {_current_year()}",
            f"What Is {topic}? A Beginner's Guide",
        ],
        "listicle": [
            f"10 {kw} Tips That Actually Work ({_current_year()})",
            f"7 Ways to {topic} Faster and Smarter",
            f"15 Best {kw} Strategies for Beginners",
            f"Top 5 {topic} Mistakes to Avoid",
            f"12 Proven {kw} Techniques",
        ],
        "question": [
            f"What Is {topic}? ({_current_year()} Guide)",
            f"How Does {topic} Work? A Simple Explanation",
            f"Why {kw} Matters More Than You Think",
            f"Is {topic} Worth It? Our Honest Review",
            f"When Should You Use {kw}?",
        ],
        "ecommerce": [
            f"Buy {topic} — Best Deals & Reviews ({_current_year()})",
            f"{kw}: Compare Prices, Reviews & Features",
            f"Best {kw} in {_current_year()} — Top Picks",
            f"{topic} Review: Is It Worth the Price?",
            f"Where to Buy {kw}: Cheapest Options",
        ],
    }
    titles = templates.get(style, templates["standard"])
    if brand:
        titles = [f"{t} | {brand}" for t in titles[:3]] + titles[3:]
    # Trim to 60 chars max
    return [t[:60] for t in titles]


def generate_meta_description(
    topic: str,
    keyword: str = "",
    value_prop: str = "",
    cta: str = "Learn more",
) -> list[str]:
    """Generate meta description variations (120-160 characters)."""
    kw = keyword or topic
    base_descriptions = [
        f"Discover everything about {kw}. {value_prop or f'Get expert tips and strategies'} to {topic.lower()}. {cta} →",
        f"Looking for {kw} guidance? {value_prop or 'Our experts share proven methods'} that actually work. {cta} now.",
        f"Master {topic} with our complete guide. {value_prop or 'Step-by-step instructions, tips, and best practices'}. {cta}.",
        f"Everything you need to know about {kw}. {value_prop or 'Trusted advice from industry experts'}. {cta} today.",
        f"Struggling with {topic}? {value_prop or 'We break down the process into simple steps'}. {cta} for free →",
    ]
    # Ensure 120-160 chars
    result = []
    for desc in base_descriptions:
        if len(desc) > 160:
            desc = desc[:157] + "..."
        elif len(desc) < 120:
            desc = desc + " " * (120 - len(desc))
            desc = desc.strip()
        result.append(desc)
    return result


def _current_year() -> int:
    from datetime import datetime
    return datetime.now().year


# ---------------------------------------------------------------------------
# Schema markup generators
# ---------------------------------------------------------------------------

def generate_article_schema(
    url: str,
    title: str,
    description: str,
    author_name: str,
    published_date: str,
    modified_date: str = "",
    image_url: str = "",
    organization: str = "",
) -> dict:
    """Generate Article JSON-LD schema markup."""
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": description,
        "url": url,
        "author": {
            "@type": "Person",
            "name": author_name,
        },
        "datePublished": published_date,
        "dateModified": modified_date or published_date,
    }
    if image_url:
        schema["image"] = {"@type": "ImageObject", "url": image_url}
    if organization:
        schema["publisher"] = {
            "@type": "Organization",
            "name": organization,
        }
    return schema


def generate_faq_schema(faqs: list[dict]) -> dict:
    """
    Generate FAQ Page JSON-LD schema.
    faqs: list of {"question": "...", "answer": "..."} dicts
    """
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": faq["answer"],
                },
            }
            for faq in faqs
        ],
    }


def generate_product_schema(
    name: str,
    description: str,
    image_url: str,
    price: float,
    currency: str = "USD",
    availability: str = "InStock",
    rating: float = 0.0,
    review_count: int = 0,
    brand: str = "",
    sku: str = "",
) -> dict:
    """Generate Product JSON-LD schema markup."""
    schema: dict = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "description": description,
        "image": image_url,
        "offers": {
            "@type": "Offer",
            "price": str(price),
            "priceCurrency": currency,
            "availability": f"https://schema.org/{availability}",
        },
    }
    if brand:
        schema["brand"] = {"@type": "Brand", "name": brand}
    if sku:
        schema["sku"] = sku
    if rating > 0 and review_count > 0:
        schema["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": str(rating),
            "reviewCount": str(review_count),
        }
    return schema


def generate_breadcrumb_schema(breadcrumbs: list[dict]) -> dict:
    """
    Generate BreadcrumbList JSON-LD schema.
    breadcrumbs: list of {"name": "...", "url": "..."} dicts
    """
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": crumb["name"],
                "item": crumb["url"],
            }
            for i, crumb in enumerate(breadcrumbs)
        ],
    }


def generate_how_to_schema(
    name: str,
    description: str,
    steps: list[dict],
    total_time: str = "",
    tools: list[str] | None = None,
) -> dict:
    """
    Generate HowTo JSON-LD schema.
    steps: list of {"name": "...", "text": "..."} dicts
    total_time: ISO 8601 duration string, e.g. "PT30M"
    """
    schema: dict = {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": name,
        "description": description,
        "step": [
            {
                "@type": "HowToStep",
                "name": step["name"],
                "text": step["text"],
            }
            for step in steps
        ],
    }
    if total_time:
        schema["totalTime"] = total_time
    if tools:
        schema["tool"] = [{"@type": "HowToTool", "name": t} for t in tools]
    return schema


def generate_review_schema(
    item_name: str,
    reviewer_name: str,
    rating: float,
    review_body: str,
    date_published: str,
    item_type: str = "Product",
) -> dict:
    """Generate Review JSON-LD schema markup."""
    return {
        "@context": "https://schema.org",
        "@type": "Review",
        "itemReviewed": {
            "@type": item_type,
            "name": item_name,
        },
        "author": {
            "@type": "Person",
            "name": reviewer_name,
        },
        "reviewRating": {
            "@type": "Rating",
            "ratingValue": str(rating),
            "bestRating": "5",
            "worstRating": "1",
        },
        "reviewBody": review_body,
        "datePublished": date_published,
    }


# ---------------------------------------------------------------------------
# Internal linking
# ---------------------------------------------------------------------------

def suggest_internal_links(
    content: str,
    site_pages: list[dict],
    max_suggestions: int = 5,
) -> list[dict]:
    """
    Suggest internal links to add to content.
    site_pages: list of {"url": "...", "title": "...", "keywords": [...]} dicts
    Returns list of {"phrase": "...", "url": "...", "title": "..."} suggestions.
    """
    suggestions = []
    content_lower = content.lower()

    for page in site_pages:
        page_keywords = page.get("keywords", [])
        page_title = page.get("title", "")

        # Check if any keyword/title phrase appears in content
        candidates = page_keywords + ([page_title] if page_title else [])
        for phrase in candidates:
            if not phrase:
                continue
            phrase_lower = phrase.lower()
            if phrase_lower in content_lower and len(phrase) > 3:
                suggestions.append({
                    "phrase": phrase,
                    "url": page["url"],
                    "title": page_title,
                    "anchor_text": phrase,
                })
                break

        if len(suggestions) >= max_suggestions:
            break

    return suggestions


def add_internal_link(from_url: str, to_url: str, anchor_text: str) -> bool:
    """Record an internal link relationship."""
    db = _db()
    try:
        db.execute(
            """INSERT INTO seo_internal_links (from_url, to_url, anchor_text, discovered_at)
               VALUES (?,?,?,?)
               ON CONFLICT(from_url, to_url) DO UPDATE SET anchor_text=excluded.anchor_text""",
            (from_url, to_url, anchor_text, time.time()),
        )
        db.commit()
        return True
    except Exception:
        return False


def get_pages_linking_to(url: str) -> list[dict]:
    """Get all pages that link TO the given URL."""
    rows = _db().execute(
        "SELECT * FROM seo_internal_links WHERE to_url=? ORDER BY discovered_at DESC",
        (url,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_links_from_page(url: str) -> list[dict]:
    """Get all internal links going out FROM a page."""
    rows = _db().execute(
        "SELECT * FROM seo_internal_links WHERE from_url=? ORDER BY discovered_at DESC",
        (url,),
    ).fetchall()
    return [dict(row) for row in rows]


def find_orphan_pages() -> list[dict]:
    """Find pages with no internal links pointing to them."""
    db = _db()
    linked_urls = {row[0] for row in db.execute(
        "SELECT DISTINCT to_url FROM seo_internal_links"
    ).fetchall()}
    all_pages = db.execute("SELECT url, title FROM seo_pages").fetchall()
    orphans = [dict(row) for row in all_pages if row["url"] not in linked_urls]
    return orphans


# ---------------------------------------------------------------------------
# Redirect management
# ---------------------------------------------------------------------------

def add_redirect(from_url: str, to_url: str, reason: str = "", status_code: int = 301) -> int:
    """Add a URL redirect rule."""
    db = _db()
    cur = db.execute(
        """INSERT INTO seo_redirects (from_url, to_url, status_code, reason, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(from_url) DO UPDATE SET
             to_url=excluded.to_url,
             status_code=excluded.status_code,
             reason=excluded.reason""",
        (from_url, to_url, status_code, reason, time.time()),
    )
    db.commit()
    return cur.lastrowid or 0


def get_redirect(from_url: str) -> dict | None:
    """Look up a redirect for a given URL."""
    row = _db().execute(
        "SELECT * FROM seo_redirects WHERE from_url=?", (from_url,)
    ).fetchone()
    return dict(row) if row else None


def list_redirects(limit: int = 100) -> list[dict]:
    """List all redirect rules."""
    rows = _db().execute(
        "SELECT * FROM seo_redirects ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Page performance tracking
# ---------------------------------------------------------------------------

def save_page_meta(
    url: str,
    title: str = "",
    meta_description: str = "",
    target_keyword: str = "",
    h1: str = "",
) -> None:
    """Save or update page metadata."""
    db = _db()
    existing = db.execute("SELECT title, meta_description FROM seo_pages WHERE url=?", (url,)).fetchone()
    if existing:
        if existing["title"] != title or existing["meta_description"] != meta_description:
            db.execute(
                "INSERT INTO seo_meta_history (url, title, meta_description, changed_at) VALUES (?,?,?,?)",
                (url, existing["title"], existing["meta_description"], time.time()),
            )

    db.execute(
        """INSERT INTO seo_pages (url, title, meta_description, h1, target_keyword, last_analyzed)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
             title=excluded.title,
             meta_description=excluded.meta_description,
             h1=excluded.h1,
             target_keyword=excluded.target_keyword,
             last_analyzed=excluded.last_analyzed""",
        (url, title, meta_description, h1, target_keyword, time.time()),
    )
    db.commit()


def get_page_info(url: str) -> dict | None:
    """Get stored SEO data for a URL."""
    row = _db().execute("SELECT * FROM seo_pages WHERE url=?", (url,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["issues"] = json.loads(d.get("issues", "[]"))
    d["suggestions"] = json.loads(d.get("suggestions", "[]"))
    return d


def pages_needing_optimization(score_threshold: float = 70.0, limit: int = 20) -> list[dict]:
    """Get pages with SEO score below threshold, ordered by lowest score first."""
    rows = _db().execute(
        "SELECT * FROM seo_pages WHERE score < ? ORDER BY score ASC LIMIT ?",
        (score_threshold, limit),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["issues"] = json.loads(d.get("issues", "[]"))
        d["suggestions"] = json.loads(d.get("suggestions", "[]"))
        result.append(d)
    return result


def seo_site_overview() -> dict:
    """High-level SEO health summary across all tracked pages."""
    db = _db()
    total_pages = db.execute("SELECT COUNT(*) FROM seo_pages").fetchone()[0]
    avg_score = db.execute("SELECT AVG(score) FROM seo_pages").fetchone()[0] or 0.0
    pages_a = db.execute("SELECT COUNT(*) FROM seo_pages WHERE score>=90").fetchone()[0]
    pages_b = db.execute("SELECT COUNT(*) FROM seo_pages WHERE score>=80 AND score<90").fetchone()[0]
    pages_c = db.execute("SELECT COUNT(*) FROM seo_pages WHERE score>=70 AND score<80").fetchone()[0]
    pages_d = db.execute("SELECT COUNT(*) FROM seo_pages WHERE score>=60 AND score<70").fetchone()[0]
    pages_f = db.execute("SELECT COUNT(*) FROM seo_pages WHERE score<60").fetchone()[0]
    total_redirects = db.execute("SELECT COUNT(*) FROM seo_redirects").fetchone()[0]
    total_internal_links = db.execute("SELECT COUNT(*) FROM seo_internal_links").fetchone()[0]
    orphan_count = len(find_orphan_pages())

    return {
        "total_pages_tracked": total_pages,
        "average_seo_score": round(avg_score, 1),
        "grade_breakdown": {"A": pages_a, "B": pages_b, "C": pages_c, "D": pages_d, "F": pages_f},
        "total_redirects": total_redirects,
        "total_internal_links": total_internal_links,
        "orphan_pages": orphan_count,
    }


# ---------------------------------------------------------------------------
# Schema storage
# ---------------------------------------------------------------------------

def save_schema_markup(url: str, schema_type: str, json_ld: dict) -> int:
    """Save generated schema markup for a URL."""
    db = _db()
    cur = db.execute(
        """INSERT INTO seo_schema_markup (url, schema_type, json_ld, created_at)
           VALUES (?,?,?,?)""",
        (url, schema_type, json.dumps(json_ld), time.time()),
    )
    db.commit()
    return cur.lastrowid or 0


def get_schema_markup(url: str) -> list[dict]:
    """Get all schema markup saved for a URL."""
    rows = _db().execute(
        "SELECT * FROM seo_schema_markup WHERE url=? ORDER BY created_at DESC",
        (url,),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["json_ld"] = json.loads(d.get("json_ld", "{}"))
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def analyze_content_tool(
    content: str = "",
    title: str = "",
    meta_description: str = "",
    target_keyword: str = "",
    url: str = "",
) -> str:
    """Agent-callable: analyze content for SEO issues."""
    if not content:
        return "error: content required"
    result = analyze_content(
        content, title=title, meta_description=meta_description,
        target_keyword=target_keyword, url=url,
    )
    return json.dumps({
        "score": result["score"],
        "grade": result["grade"],
        "word_count": result.get("word_count", 0),
        "issues": result["issues"][:5],
        "suggestions": result["suggestions"][:3],
    }, indent=2)


def generate_title_tool(topic: str = "", keyword: str = "", style: str = "standard") -> str:
    """Agent-callable: generate SEO title variations."""
    if not topic:
        return "error: topic required"
    titles = generate_title(topic, keyword, style=style)
    return "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))


def generate_meta_tool(topic: str = "", keyword: str = "", cta: str = "Learn more") -> str:
    """Agent-callable: generate meta description variations."""
    if not topic:
        return "error: topic required"
    metas = generate_meta_description(topic, keyword, cta=cta)
    return "\n".join(f"{i+1}. {m}" for i, m in enumerate(metas))


def generate_faq_schema_tool(faqs_json: str = "") -> str:
    """Agent-callable: generate FAQ schema JSON-LD from JSON array."""
    try:
        faqs = json.loads(faqs_json) if faqs_json else []
    except Exception:
        return "error: faqs_json must be a valid JSON array of {question, answer} objects"
    if not faqs:
        return "error: at least one FAQ required"
    schema = generate_faq_schema(faqs)
    return json.dumps(schema, indent=2)


def site_overview_tool() -> str:
    """Agent-callable: SEO site overview summary."""
    overview = seo_site_overview()
    return json.dumps(overview, indent=2)


def pages_needing_work_tool(score_threshold: float = 70.0, limit: int = 10) -> str:
    """Agent-callable: list pages needing SEO improvement."""
    pages = pages_needing_optimization(score_threshold, limit)
    if not pages:
        return f"all tracked pages have SEO score >= {score_threshold}"
    return json.dumps([{
        "url": p["url"],
        "title": p["title"],
        "score": p["score"],
        "top_issue": p["issues"][0] if p["issues"] else "none",
    } for p in pages], indent=2)


def add_redirect_tool(from_url: str = "", to_url: str = "", reason: str = "") -> str:
    """Agent-callable: add a URL redirect rule."""
    if not from_url or not to_url:
        return "error: from_url and to_url required"
    rid = add_redirect(from_url, to_url, reason)
    return f"redirect created: {from_url} → {to_url} (id={rid})"


def orphan_pages_tool() -> str:
    """Agent-callable: find pages with no internal links pointing to them."""
    orphans = find_orphan_pages()
    if not orphans:
        return "no orphan pages found"
    return json.dumps(orphans[:20], indent=2)
