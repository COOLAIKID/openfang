"""Video toolkit — YouTube, TikTok, Vimeo, and video monetisation helpers.

All public functions return JSON strings so they can be used directly as agent
tool responses.  Config is read via ``from ..config import get, section``.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..config import get, section

OUTPUT_ROOT = Path(__file__).resolve().parent.parent.parent / "output"

_YT_API_BASE = "https://www.googleapis.com/youtube/v3"
_VIMEO_API_BASE = "https://api.vimeo.com"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _yt_key() -> str:
    """Return configured YouTube Data API key."""
    return get("youtube", "api_key", "")


def _get_json(url: str, params: dict | None = None,
              headers: dict | None = None, timeout: int = 15) -> dict:
    """Perform a GET and return parsed JSON, or {"error": ...} on failure."""
    try:
        resp = requests.get(
            url,
            params=params,
            headers=headers or _HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        body = exc.response.text[:400] if exc.response is not None else ""
        return {"error": f"HTTP {exc.response.status_code if exc.response is not None else '?'}: {body}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _scrape_yt_search(query: str, max_results: int = 10) -> list[dict]:
    """Fallback: scrape YouTube search page when no API key is set."""
    try:
        url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        html = resp.text
        # Extract video IDs and titles from embedded JSON
        match = re.search(r'var ytInitialData\s*=\s*(\{.+?\});</script>', html, re.DOTALL)
        if not match:
            return [{"error": "Could not parse YouTube search page"}]
        data = json.loads(match.group(1))
        contents = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [{}])[0]
            .get("itemSectionRenderer", {})
            .get("contents", [])
        )
        results: list[dict] = []
        for item in contents:
            vr = item.get("videoRenderer", {})
            if not vr:
                continue
            vid_id = vr.get("videoId", "")
            title = "".join(
                r.get("text", "") for r in
                vr.get("title", {}).get("runs", [])
            )
            channel = (
                vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
            )
            desc = "".join(
                r.get("text", "") for r in
                vr.get("descriptionSnippet", {}).get("runs", [])
            )
            published = vr.get("publishedTimeText", {}).get("simpleText", "")
            results.append({
                "id": vid_id,
                "title": title,
                "description": desc,
                "channel_title": channel,
                "published_at": published,
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"Scrape failed: {exc}"}]


# ---------------------------------------------------------------------------
# 1. youtube_search
# ---------------------------------------------------------------------------

def youtube_search(query: str, max_results: int = 10) -> str:
    """Search YouTube videos.

    Uses the Data API v3 if [youtube] api_key is configured, otherwise falls
    back to scraping the search results page.

    Returns JSON list of {id, title, description, channel_title, published_at}.
    """
    key = _yt_key()
    if not key:
        results = _scrape_yt_search(query, max_results)
        return json.dumps(results)

    params = {
        "part": "snippet",
        "q": query,
        "maxResults": min(int(max_results), 50),
        "type": "video",
        "key": key,
    }
    data = _get_json(f"{_YT_API_BASE}/search", params=params)
    if "error" in data:
        return json.dumps({"error": data["error"]})

    results: list[dict] = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        results.append({
            "id": item.get("id", {}).get("videoId", ""),
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt", ""),
        })
    return json.dumps(results)


# ---------------------------------------------------------------------------
# 2. youtube_video_details
# ---------------------------------------------------------------------------

def youtube_video_details(video_id: str) -> str:
    """Return detailed information for a single YouTube video.

    Returns JSON {title, description, tags, view_count, like_count,
    comment_count, duration, published_at}.
    """
    key = _yt_key()
    if not key:
        return json.dumps({"error": "YouTube API key not configured. Set [youtube] api_key in config.toml."})

    params = {
        "part": "snippet,statistics,contentDetails",
        "id": video_id,
        "key": key,
    }
    data = _get_json(f"{_YT_API_BASE}/videos", params=params)
    if "error" in data:
        return json.dumps(data)

    items = data.get("items", [])
    if not items:
        return json.dumps({"error": f"Video {video_id!r} not found."})

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content_details = item.get("contentDetails", {})

    # Parse ISO 8601 duration (PT4M13S)
    dur_raw = content_details.get("duration", "PT0S")
    dm = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur_raw)
    duration_secs = 0
    if dm:
        h, m, s = (int(x or 0) for x in dm.groups())
        duration_secs = h * 3600 + m * 60 + s

    return json.dumps({
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "tags": snippet.get("tags", []),
        "view_count": int(stats.get("viewCount", 0)),
        "like_count": int(stats.get("likeCount", 0)),
        "comment_count": int(stats.get("commentCount", 0)),
        "duration": duration_secs,
        "published_at": snippet.get("publishedAt", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "category_id": snippet.get("categoryId", ""),
    })


# ---------------------------------------------------------------------------
# 3. youtube_channel_stats
# ---------------------------------------------------------------------------

def youtube_channel_stats(channel_id: str) -> str:
    """Return subscriber count, view count, video count, and title for a channel.

    Returns JSON {subscriber_count, view_count, video_count, title}.
    """
    key = _yt_key()
    if not key:
        return json.dumps({"error": "YouTube API key not configured."})

    params = {
        "part": "statistics,snippet",
        "id": channel_id,
        "key": key,
    }
    data = _get_json(f"{_YT_API_BASE}/channels", params=params)
    if "error" in data:
        return json.dumps(data)

    items = data.get("items", [])
    if not items:
        return json.dumps({"error": f"Channel {channel_id!r} not found."})

    item = items[0]
    stats = item.get("statistics", {})
    snippet = item.get("snippet", {})
    return json.dumps({
        "title": snippet.get("title", ""),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "created_at": snippet.get("publishedAt", ""),
    })


# ---------------------------------------------------------------------------
# 4. youtube_trending
# ---------------------------------------------------------------------------

def youtube_trending(region_code: str = "US", category_id: str = "0") -> str:
    """Return currently trending YouTube videos.

    Returns JSON list of {id, title, channel_title, view_count, like_count,
    published_at}.
    """
    key = _yt_key()
    if not key:
        return json.dumps({"error": "YouTube API key not configured."})

    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": region_code,
        "videoCategoryId": category_id,
        "maxResults": 25,
        "key": key,
    }
    data = _get_json(f"{_YT_API_BASE}/videos", params=params)
    if "error" in data:
        return json.dumps(data)

    results: list[dict] = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        results.append({
            "id": item.get("id", ""),
            "title": snippet.get("title", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "published_at": snippet.get("publishedAt", ""),
        })
    return json.dumps(results)


# ---------------------------------------------------------------------------
# 5. extract_youtube_transcript
# ---------------------------------------------------------------------------

def extract_youtube_transcript(video_id: str) -> str:
    """Fetch the transcript for a YouTube video.

    Tries youtube_transcript_api first; falls back to the timedtext endpoint.
    Returns transcript text or ERROR: <message>.
    """
    # Attempt 1 – youtube_transcript_api package
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        try:
            transcript = transcript_list.find_manually_created_transcript(["en"])
        except Exception:  # noqa: BLE001
            try:
                transcript = transcript_list.find_generated_transcript(["en"])
            except Exception:  # noqa: BLE001
                for t in transcript_list:
                    transcript = t
                    break
        if transcript is None:
            return "ERROR: No transcript available for this video."
        fetched = transcript.fetch()
        text = " ".join(entry["text"] for entry in fetched)
        return text
    except ImportError:
        pass  # fall through to HTTP fallback
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: youtube_transcript_api failed: {exc}"

    # Attempt 2 – timedtext endpoint
    try:
        url = f"https://www.youtube.com/api/timedtext?lang=en&v={video_id}"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            # Strip XML tags
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return text
        return "ERROR: No transcript available (timedtext endpoint returned empty)."
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


# ---------------------------------------------------------------------------
# 6. youtube_comments
# ---------------------------------------------------------------------------

def youtube_comments(video_id: str, max_results: int = 50) -> str:
    """Return top-level comments for a YouTube video.

    Returns JSON list of {author, text, likes, published_at}.
    """
    key = _yt_key()
    if not key:
        return json.dumps({"error": "YouTube API key not configured."})

    params = {
        "part": "snippet",
        "videoId": video_id,
        "order": "relevance",
        "maxResults": min(int(max_results), 100),
        "key": key,
    }
    data = _get_json(f"{_YT_API_BASE}/commentThreads", params=params)
    if "error" in data:
        return json.dumps(data)

    results: list[dict] = []
    for item in data.get("items", []):
        top = (
            item.get("snippet", {})
            .get("topLevelComment", {})
            .get("snippet", {})
        )
        results.append({
            "author": top.get("authorDisplayName", ""),
            "text": top.get("textDisplay", ""),
            "likes": top.get("likeCount", 0),
            "published_at": top.get("publishedAt", ""),
        })
    return json.dumps(results)


# ---------------------------------------------------------------------------
# 7. estimate_youtube_cpm
# ---------------------------------------------------------------------------

_CPM_TABLE: dict[str, tuple[float, float]] = {
    "finance": (12.0, 45.0),
    "investing": (10.0, 40.0),
    "insurance": (14.0, 50.0),
    "real estate": (10.0, 35.0),
    "law": (8.0, 35.0),
    "software": (8.0, 30.0),
    "saas": (10.0, 35.0),
    "tech": (8.0, 25.0),
    "technology": (8.0, 25.0),
    "crypto": (6.0, 30.0),
    "marketing": (6.0, 25.0),
    "health": (10.0, 35.0),
    "medical": (7.0, 30.0),
    "fitness": (3.0, 12.0),
    "business": (5.0, 20.0),
    "education": (4.0, 18.0),
    "food": (2.0, 10.0),
    "cooking": (2.0, 10.0),
    "travel": (2.5, 12.0),
    "beauty": (2.5, 12.0),
    "fashion": (2.0, 10.0),
    "gaming": (2.0, 8.0),
    "entertainment": (1.0, 6.0),
    "music": (1.0, 5.0),
    "comedy": (1.0, 5.0),
    "general": (2.0, 5.0),
}


def estimate_youtube_cpm(niche: str) -> str:
    """Return estimated CPM range for a niche.

    Returns JSON {niche, cpm_low, cpm_high, annual_est_per_1k_subs}.
    annual_est_per_1k_subs assumes 1 video/week, 1k avg views/video, 45% ad rate.
    """
    key = niche.lower().strip()
    cpm_low, cpm_high = _CPM_TABLE.get(key, (0.0, 0.0))

    if cpm_low == 0.0:
        # Partial match
        for k, (lo, hi) in _CPM_TABLE.items():
            if k in key or key in k:
                cpm_low, cpm_high = lo, hi
                break
        else:
            cpm_low, cpm_high = 2.0, 5.0

    avg_cpm = (cpm_low + cpm_high) / 2.0
    # 52 videos * 1000 views * (avg_cpm/1000) * 0.45
    annual_est = round(52 * 1000 * (avg_cpm / 1000) * 0.45, 2)

    return json.dumps({
        "niche": niche,
        "cpm_low": cpm_low,
        "cpm_high": cpm_high,
        "avg_cpm": round(avg_cpm, 2),
        "annual_est_per_1k_subs": annual_est,
        "note": "Estimates based on industry averages; actual CPM varies by audience geography and season.",
    })


# ---------------------------------------------------------------------------
# 8. viral_score_estimator
# ---------------------------------------------------------------------------

_EMOTIONAL_WORDS = [
    "secret", "shocking", "amazing", "unbelievable", "insane", "exposed",
    "truth", "nobody", "everyone", "viral", "trending", "best", "worst",
    "never", "always", "instantly", "hack", "trick", "easy", "free",
    "ultimate", "complete", "proven", "guaranteed", "weird", "strange",
    "terrifying", "incredible", "mind-blowing", "epic", "brutal", "fail",
    "gone wrong", "dangerous", "scary", "hilarious",
]


def viral_score_estimator(
    title: str,
    description: str,
    tags: list[str] | str,
    thumbnail_has_face: bool = True,
) -> str:
    """Heuristic viral potential score from 0 to 100.

    Awards points for title optimisation, description quality, tags, and
    thumbnail.  Returns JSON {score, breakdown, suggestions}.
    """
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    score = 0
    breakdown: dict[str, int] = {}
    suggestions: list[str] = []

    # ---- Title (max 40 pts) ----
    t_score = 0
    tl = len(title)
    title_lower = title.lower()

    if 40 <= tl <= 70:
        t_score += 10
    elif 30 <= tl < 40 or 70 < tl <= 80:
        t_score += 5
    else:
        suggestions.append("Aim for a title length of 40-70 characters for best CTR.")

    # Number in title
    if re.search(r"\d+", title):
        t_score += 8
    else:
        suggestions.append("Include a number in the title (e.g. '7 Ways to…') to boost CTR.")

    # Emotional words
    hits = sum(1 for w in _EMOTIONAL_WORDS if w in title_lower)
    t_score += min(hits * 5, 15)
    if hits == 0:
        suggestions.append("Add an emotional/power word to the title (e.g. 'secret', 'shocking').")

    # Question mark
    if "?" in title:
        t_score += 7
    else:
        suggestions.append("A question in the title triggers curiosity and increases clicks.")

    breakdown["title"] = min(t_score, 40)

    # ---- Description (max 15 pts) ----
    d_score = 0
    if len(description) > 200:
        d_score += 5
    else:
        suggestions.append("Write a description longer than 200 characters to help SEO.")
    if re.search(r"https?://", description):
        d_score += 4
    if re.search(r"\d{1,2}:\d{2}", description):
        d_score += 4  # timestamps
    if len(re.findall(r"#\w+", description)) >= 3:
        d_score += 2
    breakdown["description"] = min(d_score, 15)

    # ---- Tags (max 15 pts) ----
    tag_score = 0
    if len(tags) > 10:
        tag_score += 8
    elif len(tags) >= 5:
        tag_score += 4
    else:
        suggestions.append("Use more than 10 tags (including long-tail phrases) for discoverability.")
    if any(len(t.split()) > 1 for t in tags):
        tag_score += 7  # multi-word / long-tail tags
    breakdown["tags"] = min(tag_score, 15)

    # ---- Thumbnail (max 20 pts) ----
    thumb_score = 10  # base
    if thumbnail_has_face:
        thumb_score += 10
    else:
        suggestions.append("Thumbnails with a visible human face improve CTR by up to 38%.")
    breakdown["thumbnail"] = min(thumb_score, 20)

    # ---- Bonus (max 10 pts) ----
    bonus = 0
    if re.search(r"[\U0001F300-\U0001FFFF]", title):  # emoji in title
        bonus += 3
    words = title.split()
    upper_ratio = sum(1 for w in words if w and w[0].isupper()) / max(len(words), 1)
    if 0.3 <= upper_ratio <= 0.8:
        bonus += 7
    breakdown["bonus"] = min(bonus, 10)

    total = sum(breakdown.values())
    score = min(total, 100)

    return json.dumps({
        "score": score,
        "grade": "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D",
        "breakdown": breakdown,
        "suggestions": suggestions,
    })


# ---------------------------------------------------------------------------
# 9. video_script_outline
# ---------------------------------------------------------------------------

def video_script_outline(topic: str, duration_minutes: int = 10) -> str:
    """Generate a structured video script outline.

    Returns JSON {sections: [{name, duration_seconds, key_points: []}]}.
    """
    total_secs = duration_minutes * 60
    hook_secs = 30
    intro_secs = 60
    outro_secs = 60
    cta_secs = 30
    body_secs = max(total_secs - hook_secs - intro_secs - outro_secs - cta_secs, 60)

    num_sections = max(3, min(5, duration_minutes // 2))
    section_secs = body_secs // num_sections

    body_names = [
        "Background & Context",
        "Key Point 1",
        "Key Point 2",
        "Key Point 3",
        "Key Point 4",
        "Common Mistakes",
        "Advanced Tips",
    ]

    sections: list[dict] = []

    sections.append({
        "name": "Hook",
        "duration_seconds": hook_secs,
        "key_points": [
            f"Open with a bold claim or surprising fact about {topic}",
            "Tease the most valuable insight from the video",
            "Keep viewer past the 15-second mark",
        ],
    })
    sections.append({
        "name": "Introduction",
        "duration_seconds": intro_secs,
        "key_points": [
            f"Who this video is for (target audience interested in {topic})",
            "What they will learn by the end",
            "Your credibility / proof point",
        ],
    })
    for i in range(num_sections):
        name = body_names[i] if i < len(body_names) else f"Section {i + 1}"
        sections.append({
            "name": name,
            "duration_seconds": section_secs,
            "key_points": [
                f"Core explanation or demonstration",
                "Example, story, or case study",
                "Transition to next point",
            ],
        })
    sections.append({
        "name": "Recap",
        "duration_seconds": outro_secs,
        "key_points": [
            "Summarise the top 3 takeaways",
            "Reinforce the main value proposition",
        ],
    })
    sections.append({
        "name": "Call to Action",
        "duration_seconds": cta_secs,
        "key_points": [
            "Ask viewer to like and subscribe",
            f"Prompt comment: 'What's your biggest challenge with {topic}?'",
            "Tease next video to retain session watch time",
        ],
    })

    return json.dumps({
        "topic": topic,
        "duration_minutes": duration_minutes,
        "sections": sections,
        "estimated_word_count": duration_minutes * 130,
    })


# ---------------------------------------------------------------------------
# 10. youtube_monetization_eligibility
# ---------------------------------------------------------------------------

def youtube_monetization_eligibility(subscribers: int, watch_hours: int) -> str:
    """Check YouTube Partner Program eligibility.

    Returns JSON {eligible, subs_needed, hours_needed, percentage_complete}.
    """
    sub_threshold = 1000
    hours_threshold = 4000

    subs_needed = max(0, sub_threshold - int(subscribers))
    hours_needed = max(0, hours_threshold - int(watch_hours))
    eligible = subs_needed == 0 and hours_needed == 0

    sub_pct = min(int(subscribers) / sub_threshold * 100, 100)
    hours_pct = min(int(watch_hours) / hours_threshold * 100, 100)
    overall_pct = round((sub_pct + hours_pct) / 2, 1)

    return json.dumps({
        "eligible": eligible,
        "subscribers": int(subscribers),
        "watch_hours": int(watch_hours),
        "subs_needed": subs_needed,
        "hours_needed": hours_needed,
        "percentage_complete": overall_pct,
        "message": (
            "Eligible for YouTube Partner Program. Apply via YouTube Studio."
            if eligible
            else f"Need {subs_needed:,} more subscribers and {hours_needed:,} more watch-hours."
        ),
    })


# ---------------------------------------------------------------------------
# 11. tiktok_trending_hashtags
# ---------------------------------------------------------------------------

def tiktok_trending_hashtags(region: str = "US") -> str:
    """Attempt to fetch TikTok trending hashtags.

    Scrapes the TikTok discover page.  Returns JSON list of
    {hashtag, estimated_views}.
    """
    url = "https://www.tiktok.com/discover"
    headers = {
        **_HEADERS,
        "Referer": "https://www.tiktok.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        html = resp.text

        # Try __NEXT_DATA__ JSON
        nd_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL
        )
        hashtags: list[dict] = []
        if nd_match:
            try:
                nd = json.loads(nd_match.group(1))
                items = (
                    nd.get("props", {})
                    .get("pageProps", {})
                    .get("topHashtagList", [])
                )
                for item in items:
                    hashtags.append({
                        "hashtag": "#" + item.get("hashtagName", item.get("name", "")),
                        "estimated_views": item.get("videoCount", item.get("views", 0)),
                    })
            except (json.JSONDecodeError, AttributeError):
                pass

        # Fallback: regex
        if not hashtags:
            raw_tags = re.findall(r'"hashtagName"\s*:\s*"([^"]{2,50})"', html)
            seen: set[str] = set()
            for tag in raw_tags:
                if tag not in seen:
                    seen.add(tag)
                    hashtags.append({"hashtag": "#" + tag, "estimated_views": 0})

        if not hashtags:
            # Static fallback of evergreen hashtags
            hashtags = [
                {"hashtag": "#fyp", "estimated_views": 0},
                {"hashtag": "#foryou", "estimated_views": 0},
                {"hashtag": "#viral", "estimated_views": 0},
                {"hashtag": "#trending", "estimated_views": 0},
                {"hashtag": "#tiktok", "estimated_views": 0},
            ]

        return json.dumps(hashtags[:25])
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to fetch TikTok trending: {exc}"})


# ---------------------------------------------------------------------------
# 12. shorts_ideas
# ---------------------------------------------------------------------------

_SHORTS_TEMPLATES = [
    ("5 {niche} secrets nobody tells you", "curiosity gap"),
    ("POV: you just discovered {niche}", "pov"),
    ("{niche} hack that changed my life", "story"),
    ("The fastest way to learn {niche}", "how-to"),
    ("Why 90% of people fail at {niche}", "shock"),
    ("I tried {niche} for 30 days – here's what happened", "challenge"),
    ("10 free {niche} tools you need right now", "list"),
    ("Rate my {niche} setup 🔥", "reaction"),
    ("Myth vs fact: {niche} edition", "myth-busting"),
    ("The {niche} skill that earns the most money", "motivation"),
    ("Things I wish I knew before starting {niche}", "reflection"),
    ("What no one tells you about {niche}", "curiosity gap"),
    ("How I made money with {niche} in 7 days", "transformation"),
    ("The ugly truth about {niche}", "shock"),
    ("{niche} for complete beginners – first steps", "how-to"),
    ("This {niche} mistake costs people thousands", "warning"),
    ("3 {niche} habits that compound over time", "self-improvement"),
    ("Story time: how {niche} changed my life", "story"),
    ("The most underrated {niche} tip nobody uses", "curiosity gap"),
    ("Day in the life using {niche} every day", "lifestyle"),
]


def shorts_ideas(niche: str, num: int = 10) -> str:
    """Generate YouTube Shorts / TikTok video ideas for a niche.

    Returns JSON list of {title, hook, format_type}.
    """
    ideas: list[dict] = []
    for template, fmt in _SHORTS_TEMPLATES:
        if len(ideas) >= int(num):
            break
        title = template.replace("{niche}", niche)
        hook = title.split("–")[0].split(":")[0].strip() + "..."
        ideas.append({
            "title": title,
            "hook": hook,
            "format_type": fmt,
        })
    return json.dumps(ideas[:int(num)])


# ---------------------------------------------------------------------------
# 13. vimeo_video_details
# ---------------------------------------------------------------------------

def vimeo_video_details(video_id: str, token: str = "") -> str:
    """Return metadata for a Vimeo video.

    Returns JSON {title, description, duration, plays, likes, embed_url}.
    """
    import os
    tok = token or os.environ.get("VIMEO_TOKEN", "") or get("vimeo", "token", "")
    if not tok:
        return json.dumps({"error": "No Vimeo access token. Set VIMEO_TOKEN env var or [vimeo] token in config."})

    headers = {**_HEADERS, "Authorization": f"bearer {tok}"}
    data = _get_json(f"{_VIMEO_API_BASE}/videos/{video_id}", headers=headers)
    if "error" in data:
        return json.dumps(data)

    embed = data.get("embed", {}).get("html", "")
    stats = data.get("stats", {})
    return json.dumps({
        "title": data.get("name", ""),
        "description": data.get("description", ""),
        "duration": data.get("duration", 0),
        "plays": stats.get("plays", 0),
        "likes": stats.get("likes", 0),
        "embed_url": f"https://player.vimeo.com/video/{video_id}",
        "embed_html": embed,
        "created_at": data.get("created_time", ""),
        "link": data.get("link", f"https://vimeo.com/{video_id}"),
    })


# ---------------------------------------------------------------------------
# 14. calculate_ad_revenue
# ---------------------------------------------------------------------------

def calculate_ad_revenue(
    views: float,
    cpm: float,
    ad_rate: float = 0.45,
) -> str:
    """Estimate YouTube ad revenue.

    Revenue = views * (cpm / 1000) * ad_rate.
    Returns JSON {estimated_revenue, views, effective_cpm}.
    """
    views = float(views)
    cpm = float(cpm)
    ad_rate = float(ad_rate)

    revenue = views * (cpm / 1000.0) * ad_rate
    effective_cpm = (revenue / views * 1000) if views > 0 else 0.0

    return json.dumps({
        "estimated_revenue": round(revenue, 2),
        "views": int(views),
        "cpm": cpm,
        "ad_rate": ad_rate,
        "effective_cpm": round(effective_cpm, 4),
        "monthly_est_at_current_rate": round(revenue * 30, 2) if views > 0 else 0,
    })
