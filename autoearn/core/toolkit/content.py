"""Content toolkit — text analysis and SEO helpers (no external services).

Pure-Python utilities the content team uses to assess and shape writing:
readability, keyword density, slugs, reading time, and a basic meta-description
extractor. Deliberately dependency-free so they always work.
"""
from __future__ import annotations

import json
import re
from collections import Counter

_WORD = re.compile(r"[A-Za-z']+")
_SENTENCE = re.compile(r"[.!?]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "as", "at",
    "by", "from", "you", "your", "we", "our", "they", "their", "i", "he", "she",
}


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def reading_time(text: str, wpm: int = 220) -> str:
    minutes = max(1, round(word_count(text) / wpm))
    return f"{minutes} min read ({word_count(text)} words)"


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80]


def keyword_density(text: str, top: int = 12) -> str:
    words = [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOPWORDS and len(w) > 2]
    total = len(words) or 1
    counts = Counter(words).most_common(top)
    rows = [{"word": w, "count": c, "density_pct": round(c / total * 100, 2)} for w, c in counts]
    return json.dumps(rows)


def flesch_reading_ease(text: str) -> str:
    """Approximate Flesch Reading Ease (higher = easier)."""
    words = _WORD.findall(text)
    sentences = [s for s in _SENTENCE.split(text) if s.strip()]
    if not words or not sentences:
        return "n/a (need more text)"
    syllables = sum(_count_syllables(w) for w in words)
    wps = len(words) / len(sentences)
    spw = syllables / len(words)
    score = 206.835 - 1.015 * wps - 84.6 * spw
    score = round(score, 1)
    if score >= 70:
        band = "easy"
    elif score >= 50:
        band = "fairly difficult"
    else:
        band = "difficult"
    return f"Flesch {score} ({band}); {wps:.1f} words/sentence"


def _count_syllables(word: str) -> int:
    word = word.lower()
    vowels = "aeiouy"
    count, prev_vowel = 0, False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def meta_description(text: str, max_len: int = 158) -> str:
    """Take the first sentences up to ~max_len chars as a meta description."""
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    cut = clean[:max_len]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


def outline_headings(text: str) -> str:
    """Extract markdown headings to inspect an article's structure."""
    heads = [line.strip() for line in text.splitlines() if line.lstrip().startswith("#")]
    return "\n".join(heads) if heads else "(no markdown headings found)"
