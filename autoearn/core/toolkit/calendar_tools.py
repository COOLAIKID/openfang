from __future__ import annotations

import json
import re
import math
import collections
import random
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> datetime:
    """Parse a date string in YYYY-MM-DD or ISO format, fallback to today."""
    if not date_str:
        return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {date_str!r}")


def _weekday_name(dt: datetime) -> str:
    return dt.strftime("%A")


# ---------------------------------------------------------------------------
# optimal_posting_times
# ---------------------------------------------------------------------------

def optimal_posting_times(platform: str, timezone: str = "UTC") -> dict:
    """Return research-backed best times to post for a given platform.

    Returns a dict with keys: platform, timezone, best_days, best_hours,
    peak_windows, worst_times, notes.
    """
    platform = platform.strip().lower()

    schedule_data = {
        "facebook": {
            "best_days": ["Tuesday", "Wednesday", "Thursday"],
            "best_hours": [9, 13, 15],
            "peak_windows": [
                {"day": "Wednesday", "hour_range": "11:00-13:00", "reason": "midweek lunch engagement peak"},
                {"day": "Thursday", "hour_range": "13:00-16:00", "reason": "afternoon scroll time"},
            ],
            "worst_times": ["Saturday before 8:00", "Sunday before 9:00", "any day after 22:00"],
            "notes": "Video content performs best 12:00-15:00. Stories peak at 08:00 commute window.",
        },
        "instagram": {
            "best_days": ["Monday", "Tuesday", "Wednesday", "Friday"],
            "best_hours": [6, 9, 12, 17, 21],
            "peak_windows": [
                {"day": "Monday", "hour_range": "06:00-09:00", "reason": "morning routine scroll"},
                {"day": "Wednesday", "hour_range": "11:00-13:00", "reason": "lunch break"},
                {"day": "Friday", "hour_range": "17:00-18:00", "reason": "end-of-week mood boost"},
            ],
            "worst_times": ["Monday before 06:00", "any day 03:00-05:00"],
            "notes": "Reels enjoy a 22% broader reach than static posts. Stories best at 08:00 and 20:00.",
        },
        "twitter": {
            "best_days": ["Tuesday", "Wednesday", "Thursday"],
            "best_hours": [8, 9, 12, 17, 18],
            "peak_windows": [
                {"day": "Tuesday", "hour_range": "09:00-10:00", "reason": "professional morning mindset"},
                {"day": "Wednesday", "hour_range": "12:00-13:00", "reason": "lunch trending topics"},
                {"day": "Thursday", "hour_range": "17:00-18:00", "reason": "post-work news sweep"},
            ],
            "worst_times": ["Saturday after 20:00", "Sunday before 10:00"],
            "notes": "Tweet lifespan is ~18 minutes. Plan for real-time events and trending topics.",
        },
        "linkedin": {
            "best_days": ["Tuesday", "Wednesday", "Thursday"],
            "best_hours": [7, 8, 10, 12, 17],
            "peak_windows": [
                {"day": "Tuesday", "hour_range": "07:00-08:00", "reason": "pre-work professional browse"},
                {"day": "Wednesday", "hour_range": "10:00-11:00", "reason": "mid-morning professional check-in"},
                {"day": "Thursday", "hour_range": "17:00-18:00", "reason": "end-of-business wind-down"},
            ],
            "worst_times": ["Friday after 17:00", "Saturday all day", "Sunday all day"],
            "notes": "Long-form articles get 3x engagement when posted Tuesday-Thursday 08:00-10:00.",
        },
        "reddit": {
            "best_days": ["Monday", "Tuesday", "Saturday"],
            "best_hours": [6, 7, 8, 9, 10],
            "peak_windows": [
                {"day": "Monday", "hour_range": "06:00-09:00", "reason": "morning US East Coast traffic"},
                {"day": "Saturday", "hour_range": "08:00-11:00", "reason": "weekend morning browsing peak"},
            ],
            "worst_times": ["any day 23:00-05:00 EST"],
            "notes": "Post timing is subreddit-specific. Check individual subreddit analytics when available.",
        },
    }

    known_platforms = list(schedule_data.keys())
    if platform not in schedule_data:
        closest = next((p for p in known_platforms if p.startswith(platform[:3])), known_platforms[0])
        data = schedule_data[closest].copy()
        data["warning"] = f"Platform '{platform}' not recognised; showing data for '{closest}'."
    else:
        data = schedule_data[platform].copy()

    return {
        "platform": platform,
        "timezone": timezone,
        **data,
    }


# ---------------------------------------------------------------------------
# content_calendar_week
# ---------------------------------------------------------------------------

def content_calendar_week(team: list | str, topics: list, start_date: str = "") -> dict:
    """Generate a 7-day content calendar.

    Parameters
    ----------
    team:        list of team member names (or a single string).
    topics:      list of content topics to distribute across the week.
    start_date:  ISO date string; defaults to next Monday.

    Returns a dict keyed by date string with day plan details.
    """
    if isinstance(team, str):
        team = [team]
    if not team:
        team = ["Unassigned"]

    base = _parse_date(start_date)
    # Snap to Monday if no explicit date
    if not start_date:
        days_to_monday = (7 - base.weekday()) % 7 or 7
        base = base + timedelta(days=days_to_monday)

    content_types = ["Blog Post", "Social Post", "Video Script", "Email Newsletter",
                     "Infographic", "Podcast Outline", "Case Study"]
    platforms = ["LinkedIn", "Instagram", "Twitter", "Facebook", "YouTube"]

    calendar: dict = {}
    topic_cycle = list(topics) if topics else ["General Content"]
    random.seed(42)

    for i in range(7):
        day = base + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        day_name = _weekday_name(day)
        is_weekend = day.weekday() >= 5

        topic = topic_cycle[i % len(topic_cycle)]
        assignee = team[i % len(team)]
        ctype = content_types[i % len(content_types)]
        platform = platforms[i % len(platforms)]

        calendar[date_str] = {
            "day": day_name,
            "is_weekend": is_weekend,
            "topic": topic,
            "content_type": ctype,
            "platform": platform,
            "assignee": assignee,
            "status": "Planned",
            "notes": f"Draft due by {(day - timedelta(days=1)).strftime('%Y-%m-%d')}",
        }

    return {
        "week_start": base.strftime("%Y-%m-%d"),
        "week_end": (base + timedelta(days=6)).strftime("%Y-%m-%d"),
        "team": team,
        "days": calendar,
    }


# ---------------------------------------------------------------------------
# content_calendar_month
# ---------------------------------------------------------------------------

def content_calendar_month(team: list | str, strategy_notes: str = "") -> dict:
    """Generate a 30-day content calendar with weekly themes."""
    if isinstance(team, str):
        team = [team]
    if not team:
        team = ["Unassigned"]

    base = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    weekly_themes = [
        "Awareness & Education",
        "Problem / Solution",
        "Social Proof & Testimonials",
        "Product / Service Deep-Dive",
    ]
    content_mix = ["Blog", "Video", "Infographic", "Email", "Social", "Podcast"]

    calendar: dict = {}
    random.seed(7)

    for i in range(30):
        day = base + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        week_num = i // 7
        theme = weekly_themes[week_num % len(weekly_themes)]
        ctype = content_mix[i % len(content_mix)]
        assignee = team[i % len(team)]

        calendar[date_str] = {
            "day": _weekday_name(day),
            "week": week_num + 1,
            "theme": theme,
            "content_type": ctype,
            "assignee": assignee,
            "status": "Planned",
        }

    return {
        "month": base.strftime("%B %Y"),
        "team": team,
        "strategy_notes": strategy_notes,
        "weekly_themes": weekly_themes,
        "days": calendar,
        "total_pieces": 30,
    }


# ---------------------------------------------------------------------------
# calculate_posting_schedule
# ---------------------------------------------------------------------------

def calculate_posting_schedule(
    num_posts: int,
    frequency: str,
    start_date: str = "",
    platform: str = "",
) -> list[str]:
    """Return a list of ISO datetime strings for a posting schedule.

    frequency: 'daily', 'twice_daily', 'weekly', 'biweekly', 'monthly',
               or an integer string for posts-per-week.
    """
    base = _parse_date(start_date)

    freq_map = {
        "daily": timedelta(days=1),
        "twice_daily": timedelta(hours=12),
        "weekly": timedelta(weeks=1),
        "biweekly": timedelta(weeks=2),
        "monthly": timedelta(days=30),
    }

    if frequency in freq_map:
        delta = freq_map[frequency]
    elif re.match(r"^\d+$", str(frequency)):
        posts_per_week = max(1, int(frequency))
        delta = timedelta(days=7 / posts_per_week)
    else:
        delta = timedelta(days=1)

    # Platform-aware post hour defaults
    hour_defaults = {
        "linkedin": 9,
        "instagram": 12,
        "facebook": 13,
        "twitter": 9,
        "reddit": 8,
    }
    post_hour = hour_defaults.get(platform.lower(), 10)
    base = base.replace(hour=post_hour, minute=0, second=0)

    schedule = []
    current = base
    for _ in range(max(1, int(num_posts))):
        schedule.append(current.strftime("%Y-%m-%dT%H:%M:%S"))
        current += delta

    return schedule


# ---------------------------------------------------------------------------
# days_until
# ---------------------------------------------------------------------------

def days_until(event_name: str, event_date: str) -> dict:
    """Calculate days until an event and return countdown messaging."""
    target = _parse_date(event_date)
    now = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (target - now).days

    if delta < 0:
        status = "past"
        message = f"{event_name} was {abs(delta)} day(s) ago."
        urgency = "none"
    elif delta == 0:
        status = "today"
        message = f"Today is {event_name}!"
        urgency = "critical"
    elif delta <= 3:
        status = "imminent"
        message = f"Only {delta} day(s) until {event_name}! Start your final push now."
        urgency = "high"
    elif delta <= 14:
        status = "soon"
        message = f"{delta} days until {event_name}. Build anticipation content now."
        urgency = "medium"
    else:
        status = "upcoming"
        message = f"{delta} days until {event_name}. Begin awareness content planning."
        urgency = "low"

    return {
        "event": event_name,
        "event_date": target.strftime("%Y-%m-%d"),
        "days_until": delta,
        "status": status,
        "urgency": urgency,
        "countdown_message": message,
        "suggested_post": f"T-{delta} | {event_name} is almost here — are you ready? #CountDown",
    }


# ---------------------------------------------------------------------------
# marketing_seasons
# ---------------------------------------------------------------------------

def marketing_seasons() -> list[dict]:
    """Return upcoming marketing seasons and holidays with content recommendations."""
    now = datetime.utcnow()
    year = now.year

    seasons = [
        {"name": "New Year", "date": f"{year}-01-01", "type": "Holiday",
         "content": ["resolutions", "year-in-review", "goal-setting guides"]},
        {"name": "Valentine's Day", "date": f"{year}-02-14", "type": "Holiday",
         "content": ["gift guides", "love-themed campaigns", "appreciation posts"]},
        {"name": "St. Patrick's Day", "date": f"{year}-03-17", "type": "Holiday",
         "content": ["luck themes", "green promotions", "community stories"]},
        {"name": "Spring Equinox", "date": f"{year}-03-20", "type": "Season",
         "content": ["fresh-start narratives", "spring product launches", "cleaning/renewal themes"]},
        {"name": "Easter", "date": f"{year}-04-20", "type": "Holiday",
         "content": ["family themes", "egg hunt promotions", "rebirth/renewal metaphors"]},
        {"name": "Mother's Day", "date": f"{year}-05-12", "type": "Holiday",
         "content": ["gift guides", "tribute posts", "family stories"]},
        {"name": "Memorial Day", "date": f"{year}-05-27", "type": "Holiday",
         "content": ["patriotic themes", "summer kick-off sales", "appreciation content"]},
        {"name": "Summer Solstice", "date": f"{year}-06-21", "type": "Season",
         "content": ["outdoor activities", "travel content", "seasonal product highlights"]},
        {"name": "Independence Day", "date": f"{year}-07-04", "type": "Holiday",
         "content": ["patriotic themes", "BBQ/outdoor", "freedom messaging"]},
        {"name": "Back to School", "date": f"{year}-08-15", "type": "Season",
         "content": ["education themes", "productivity tips", "supply checklists"]},
        {"name": "Labor Day", "date": f"{year}-09-02", "type": "Holiday",
         "content": ["workforce appreciation", "summer farewell", "fall preview"]},
        {"name": "Fall Equinox", "date": f"{year}-09-22", "type": "Season",
         "content": ["cozy aesthetics", "harvest themes", "year-end goal reviews"]},
        {"name": "Halloween", "date": f"{year}-10-31", "type": "Holiday",
         "content": ["spooky themes", "costume contests", "frightful deals"]},
        {"name": "Thanksgiving", "date": f"{year}-11-28", "type": "Holiday",
         "content": ["gratitude posts", "behind-the-scenes team features", "charity tie-ins"]},
        {"name": "Black Friday", "date": f"{year}-11-29", "type": "Shopping",
         "content": ["flash sales", "countdown timers", "deal announcements"]},
        {"name": "Cyber Monday", "date": f"{year}-12-02", "type": "Shopping",
         "content": ["digital deals", "online exclusives", "tech/product spotlights"]},
        {"name": "Christmas", "date": f"{year}-12-25", "type": "Holiday",
         "content": ["gift guides", "year-end reflection", "holiday greetings"]},
        {"name": "New Year's Eve", "date": f"{year}-12-31", "type": "Holiday",
         "content": ["year in review", "predictions for next year", "celebration themes"]},
    ]

    upcoming = []
    for s in seasons:
        target = _parse_date(s["date"])
        if target < now:
            target = target.replace(year=year + 1)
        days_away = (target - now).days
        upcoming.append({**s, "days_away": days_away, "date": target.strftime("%Y-%m-%d")})

    upcoming.sort(key=lambda x: x["days_away"])
    return upcoming


# ---------------------------------------------------------------------------
# content_repurposing_plan
# ---------------------------------------------------------------------------

def content_repurposing_plan(original_content_type: str, platform: str) -> dict:
    """Generate a repurposing plan: how to spin one piece into many formats."""
    ctype = original_content_type.lower().strip()
    plat = platform.lower().strip()

    repurpose_map = {
        "blog post": [
            "Twitter/X thread (key points as tweets)",
            "LinkedIn article (professional angle)",
            "Instagram carousel (one insight per slide)",
            "Short-form video script (TikTok / Reels)",
            "Email newsletter section",
            "Podcast talking points",
            "Infographic (stats and steps)",
            "Pinterest pin with key quote",
            "YouTube video with screen share",
            "Reddit AMAs or discussion starter",
        ],
        "video": [
            "Blog post transcript (edited for readability)",
            "Podcast episode (audio-only export)",
            "YouTube Shorts / TikTok clips (best 60s)",
            "Instagram Reels highlight",
            "Twitter/X clip with caption",
            "Email embed with thumbnail",
            "Infographic from key video stats",
            "LinkedIn article with embedded video",
            "Slide deck (key frames as slides)",
            "Quote cards from video transcript",
        ],
        "podcast": [
            "Blog post with show notes",
            "YouTube video (audio + waveform / slides)",
            "Twitter/X thread of key insights",
            "Instagram audiogram clips",
            "LinkedIn thought leadership post",
            "Email digest with episode summary",
            "Quote graphics for Pinterest",
            "Short TikTok clip of best soundbite",
            "Slide deck from episode outline",
            "Ebook chapter (series episodes combined)",
        ],
        "infographic": [
            "Blog post expanding each section",
            "Twitter/X thread (each stat = one tweet)",
            "LinkedIn carousel post",
            "Instagram carousel (one panel per slide)",
            "Pinterest pin (native format)",
            "Email header image",
            "YouTube explainer video script",
            "SlideShare/slide deck",
            "Facebook post with image",
            "Data story for press release",
        ],
    }

    base_key = next((k for k in repurpose_map if k in ctype), "blog post")
    formats = repurpose_map[base_key]

    platform_priority = {
        "linkedin": ["LinkedIn article", "LinkedIn carousel post", "LinkedIn thought leadership post"],
        "instagram": ["Instagram carousel", "Instagram Reels highlight", "Instagram audiogram clips"],
        "twitter": ["Twitter/X thread", "Twitter/X clip with caption", "Twitter/X thread of key insights"],
        "facebook": ["Facebook post with image", "Email embed with thumbnail"],
        "youtube": ["YouTube video with screen share", "YouTube Shorts / TikTok clips", "YouTube explainer video script"],
    }

    prioritised = platform_priority.get(plat, [])
    ordered = [f for f in formats if any(p in f for p in prioritised)] + \
              [f for f in formats if not any(p in f for p in prioritised)]

    return {
        "original_type": original_content_type,
        "primary_platform": platform,
        "repurpose_formats": ordered,
        "total_pieces_possible": len(ordered),
        "quick_wins": ordered[:3],
        "tip": "Repurposing one piece of content across 5 formats multiplies reach without multiplying effort.",
    }


# ---------------------------------------------------------------------------
# publishing_checklist
# ---------------------------------------------------------------------------

def publishing_checklist(content_type: str) -> dict:
    """Return a pre-publish quality checklist for the given content type."""
    ctype = content_type.lower().strip()

    base_checks = [
        "Proofread for spelling and grammar",
        "Fact-check all statistics and claims",
        "Confirm all links are working",
        "Add UTM parameters to tracked URLs",
        "Review on mobile device",
    ]

    type_checks = {
        "article": [
            "SEO title tag optimised (50-60 chars)",
            "Meta description written (150-160 chars)",
            "H1/H2/H3 heading hierarchy correct",
            "Internal links added (3-5 minimum)",
            "External authority links added",
            "Featured image with alt text",
            "Schema markup / structured data verified",
            "Table of contents present for 1500+ word pieces",
            "Author bio updated",
            "Social share buttons functional",
        ],
        "video": [
            "Thumbnail A/B tested (two variants)",
            "Captions / subtitles uploaded",
            "End screen and cards configured",
            "Description includes keywords in first 150 chars",
            "Chapters / timestamps added",
            "Playlist assigned",
            "Premiere or publish time scheduled",
            "Community post teaser published",
            "Cross-platform share assets prepared",
        ],
        "social": [
            "Character count within platform limit",
            "Hashtag count appropriate (Instagram ≤30, Twitter ≤3)",
            "Image dimensions correct per platform spec",
            "Call-to-action clear and compelling",
            "Mentions / tags verified and correct",
            "Scheduling tool preview confirmed",
            "Geo-targeting or audience segment set (if applicable)",
        ],
        "email": [
            "Subject line A/B tested",
            "Preview text set",
            "Plain-text version generated",
            "Unsubscribe link functional",
            "Send-time optimisation enabled",
            "Suppression list applied",
            "Rendering tested in Gmail, Outlook, Apple Mail",
            "DKIM / SPF / DMARC passing",
            "List segmentation confirmed",
        ],
    }

    specific = type_checks.get(ctype, type_checks.get("social", []))
    full_list = base_checks + specific

    return {
        "content_type": content_type,
        "total_checks": len(full_list),
        "checklist": [{"item": c, "done": False} for c in full_list],
    }


# ---------------------------------------------------------------------------
# estimate_content_roi
# ---------------------------------------------------------------------------

def estimate_content_roi(
    content_type: str,
    hours_spent: float,
    traffic_estimate: int,
    monetization_rate: float,
) -> dict:
    """Estimate content ROI.

    monetization_rate: revenue per 1000 visitors (RPM equivalent).
    """
    hourly_rate_usd = 75.0  # industry avg content creator rate

    cost = hours_spent * hourly_rate_usd
    revenue = (traffic_estimate / 1000.0) * monetization_rate

    roi_pct = ((revenue - cost) / cost * 100) if cost > 0 else 0.0
    payback_months = (cost / revenue * 12) if revenue > 0 else float("inf")

    # Lifetime value multiplier by content type
    ltv_multipliers = {
        "blog post": 18,
        "video": 24,
        "podcast": 12,
        "infographic": 9,
        "social": 1,
        "email": 3,
        "webinar": 6,
        "ebook": 30,
    }
    multiplier = ltv_multipliers.get(content_type.lower().strip(), 6)
    lifetime_revenue = revenue * multiplier

    return {
        "content_type": content_type,
        "production_cost_usd": round(cost, 2),
        "projected_revenue_usd": round(revenue, 2),
        "roi_percent": round(roi_pct, 1),
        "payback_months": round(payback_months, 1) if math.isfinite(payback_months) else "N/A",
        "lifetime_revenue_estimate_usd": round(lifetime_revenue, 2),
        "lifetime_roi_percent": round(((lifetime_revenue - cost) / cost * 100), 1) if cost > 0 else 0.0,
        "hourly_rate_assumption_usd": hourly_rate_usd,
        "lifetime_multiplier_months": multiplier,
        "verdict": (
            "Strong ROI" if roi_pct > 100
            else "Moderate ROI" if roi_pct > 0
            else "Negative ROI — revisit strategy"
        ),
    }


# ---------------------------------------------------------------------------
# hashtag_strategy
# ---------------------------------------------------------------------------

def hashtag_strategy(topic: str, platform: str, count: int = 10) -> dict:
    """Recommend a balanced hashtag mix: niche, medium, broad."""
    slug = re.sub(r"\s+", "", topic.title())
    plat = platform.lower().strip()

    niche_ratio = 0.5
    medium_ratio = 0.3
    broad_ratio = 0.2

    n_niche = max(1, round(count * niche_ratio))
    n_medium = max(1, round(count * medium_ratio))
    n_broad = max(1, count - n_niche - n_medium)

    niche_tags = [f"#{slug}Tips", f"#{slug}Community", f"#{slug}Daily",
                  f"#{slug}Ideas", f"#{slug}Life", f"#{slug}Lovers"]
    medium_tags = [f"#{slug}Marketing", f"#{slug}Business", f"#Best{slug}",
                   f"#{slug}Strategy", f"#{slug}Expert"]
    broad_tags = ["#Marketing", "#ContentCreator", "#SocialMedia",
                  "#DigitalMarketing", "#GrowthHacking", "#Entrepreneur"]

    # Platform-specific tag count guidance
    platform_limits = {
        "instagram": {"recommended": 15, "max": 30},
        "twitter": {"recommended": 2, "max": 3},
        "linkedin": {"recommended": 5, "max": 10},
        "facebook": {"recommended": 3, "max": 5},
        "tiktok": {"recommended": 5, "max": 10},
        "reddit": {"recommended": 0, "max": 0},
    }
    limits = platform_limits.get(plat, {"recommended": count, "max": count * 2})

    return {
        "topic": topic,
        "platform": platform,
        "niche_tags": niche_tags[:n_niche],
        "medium_tags": medium_tags[:n_medium],
        "broad_tags": broad_tags[:n_broad],
        "all_tags": (niche_tags[:n_niche] + medium_tags[:n_medium] + broad_tags[:n_broad]),
        "copy_paste": " ".join(niche_tags[:n_niche] + medium_tags[:n_medium] + broad_tags[:n_broad]),
        "platform_recommendation": limits,
        "tip": (
            f"For {platform}, use {limits['recommended']} hashtags. "
            "Niche tags drive discovery; broad tags increase impressions."
        ),
    }


# ---------------------------------------------------------------------------
# editorial_themes
# ---------------------------------------------------------------------------

def editorial_themes(month: int | str, niche: str) -> dict:
    """Return monthly editorial theme suggestions for a niche."""
    if isinstance(month, str):
        try:
            month = int(month)
        except ValueError:
            month = datetime.strptime(month, "%B").month

    month_themes = {
        1: {"name": "January", "universal": ["New Year Goals", "Fresh Starts", "Resolutions", "Trends Predictions"]},
        2: {"name": "February", "universal": ["Valentine's Day", "Love & Relationships", "Heart Health", "Friendship"]},
        3: {"name": "March", "universal": ["Spring Renewal", "Women's History", "St. Patrick's Day", "Gardening"]},
        4: {"name": "April", "universal": ["Earth Day", "Easter", "Spring Fashion", "Tax Season"]},
        5: {"name": "May", "universal": ["Mother's Day", "Mental Health Awareness", "Graduation", "Spring Cleaning"]},
        6: {"name": "June", "universal": ["Pride Month", "Father's Day", "Summer Kickoff", "Outdoor Living"]},
        7: {"name": "July", "universal": ["Independence Day", "Summer Travel", "Mid-Year Review", "Outdoor Fitness"]},
        8: {"name": "August", "universal": ["Back to School", "Late Summer", "Productivity Reset", "Harvest Prep"]},
        9: {"name": "September", "universal": ["Fall Transition", "Labor Day", "Self-Improvement", "New Season Goals"]},
        10: {"name": "October", "universal": ["Halloween", "Breast Cancer Awareness", "Cozy Season", "Fall Decor"]},
        11: {"name": "November", "universal": ["Thanksgiving", "Gratitude", "Black Friday", "Holiday Prep"]},
        12: {"name": "December", "universal": ["Christmas", "Year in Review", "Gift Guides", "New Year Prep"]},
    }

    base = month_themes.get(month, month_themes[1])

    niche_overrides: dict[str, list[str]] = {
        "fitness": ["workout challenges", "nutrition tips", "athlete spotlights", "race/event calendars"],
        "finance": ["budgeting tips", "investment strategies", "tax guidance", "debt payoff stories"],
        "tech": ["product launches", "how-to tutorials", "industry news", "app reviews"],
        "food": ["seasonal recipes", "kitchen hacks", "restaurant reviews", "food history"],
        "travel": ["destination guides", "packing lists", "travel hacks", "local culture"],
        "beauty": ["skincare routines", "makeup tutorials", "product launches", "ingredient spotlights"],
        "business": ["leadership lessons", "startup stories", "productivity tools", "case studies"],
    }

    niche_key = next((k for k in niche_overrides if k in niche.lower()), None)
    niche_themes = niche_overrides.get(niche_key, ["How-to guides", "Case studies", "Expert interviews", "Trend roundups"])

    return {
        "month": base["name"],
        "niche": niche,
        "universal_themes": base["universal"],
        "niche_specific_themes": niche_themes,
        "combined_ideas": [f"{t} for {niche}" for t in base["universal"][:2]] + niche_themes,
        "recommended_content_mix": {
            "educational": "40%",
            "inspirational": "25%",
            "promotional": "20%",
            "entertaining": "15%",
        },
    }


# ---------------------------------------------------------------------------
# analyze_posting_frequency
# ---------------------------------------------------------------------------

def analyze_posting_frequency(activity_log: list[dict]) -> dict:
    """Analyse posting frequency from an activity log.

    Each entry: {"agent": str, "timestamp": str (ISO)}.
    Returns frequency stats, gaps, and per-agent breakdown.
    """
    if not activity_log:
        return {"error": "activity_log is empty", "stats": {}}

    parsed = []
    for entry in activity_log:
        try:
            ts = _parse_date(entry.get("timestamp", ""))
            parsed.append({"agent": entry.get("agent", "unknown"), "dt": ts})
        except ValueError:
            continue

    if not parsed:
        return {"error": "No valid timestamps found", "stats": {}}

    parsed.sort(key=lambda x: x["dt"])

    timestamps = [e["dt"] for e in parsed]
    gaps = [(timestamps[i + 1] - timestamps[i]).total_seconds() / 3600
            for i in range(len(timestamps) - 1)]

    total_span_hours = (timestamps[-1] - timestamps[0]).total_seconds() / 3600 or 1.0
    avg_gap_hours = sum(gaps) / len(gaps) if gaps else 0.0
    max_gap_hours = max(gaps) if gaps else 0.0
    min_gap_hours = min(gaps) if gaps else 0.0

    posts_per_day = len(parsed) / max(total_span_hours / 24, 1)

    agent_counter: collections.Counter = collections.Counter(e["agent"] for e in parsed)

    hourly_dist: collections.Counter = collections.Counter(e["dt"].hour for e in parsed)
    daily_dist: collections.Counter = collections.Counter(e["dt"].strftime("%A") for e in parsed)

    most_active_hour = hourly_dist.most_common(1)[0][0] if hourly_dist else None
    most_active_day = daily_dist.most_common(1)[0][0] if daily_dist else None

    large_gaps = [
        {"gap_hours": round(g, 1), "after": timestamps[i].strftime("%Y-%m-%dT%H:%M:%S")}
        for i, g in enumerate(gaps) if g > avg_gap_hours * 2
    ]

    return {
        "total_posts": len(parsed),
        "date_range": {
            "first": timestamps[0].strftime("%Y-%m-%dT%H:%M:%S"),
            "last": timestamps[-1].strftime("%Y-%m-%dT%H:%M:%S"),
            "span_days": round(total_span_hours / 24, 1),
        },
        "frequency": {
            "posts_per_day": round(posts_per_day, 2),
            "avg_gap_hours": round(avg_gap_hours, 2),
            "min_gap_hours": round(min_gap_hours, 2),
            "max_gap_hours": round(max_gap_hours, 2),
        },
        "peak_times": {
            "most_active_hour": most_active_hour,
            "most_active_day": most_active_day,
            "hourly_distribution": dict(hourly_dist),
            "daily_distribution": dict(daily_dist),
        },
        "agent_breakdown": dict(agent_counter),
        "large_gaps": large_gaps,
        "consistency_score": max(0, round(100 - (len(large_gaps) / max(len(gaps), 1) * 100), 1)),
    }


# ---------------------------------------------------------------------------
# next_content_opportunities
# ---------------------------------------------------------------------------

def next_content_opportunities(
    calendar_events: list[dict],
    existing_content: list[str],
) -> dict:
    """Identify content gaps between upcoming events and existing content.

    calendar_events: list of {"name": str, "date": str, "type": str}
    existing_content: list of topic/title strings already published.
    """
    now = datetime.utcnow()

    upcoming = []
    for event in calendar_events:
        try:
            dt = _parse_date(event.get("date", ""))
        except ValueError:
            continue
        if dt >= now:
            days_away = (dt - now).days
            upcoming.append({**event, "days_away": days_away, "date": dt.strftime("%Y-%m-%d")})

    upcoming.sort(key=lambda x: x["days_away"])

    existing_lower = [c.lower() for c in existing_content]

    def _is_covered(event_name: str) -> bool:
        slug = event_name.lower()
        return any(slug in ec or ec in slug for ec in existing_lower)

    gaps = []
    covered = []
    for ev in upcoming:
        if _is_covered(ev["name"]):
            covered.append(ev)
        else:
            urgency = "high" if ev["days_away"] <= 14 else "medium" if ev["days_away"] <= 45 else "low"
            gaps.append({**ev, "urgency": urgency,
                          "suggested_action": f"Create content for '{ev['name']}' — {ev['days_away']} days away."})

    return {
        "total_upcoming_events": len(upcoming),
        "covered_events": len(covered),
        "content_gaps": len(gaps),
        "coverage_rate_pct": round(len(covered) / max(len(upcoming), 1) * 100, 1),
        "gaps": gaps,
        "covered": covered,
        "priority_actions": [g["suggested_action"] for g in gaps if g["urgency"] == "high"],
        "recommendation": (
            "You have no urgent content gaps." if not [g for g in gaps if g["urgency"] == "high"]
            else f"Address {len([g for g in gaps if g['urgency'] == 'high'])} high-urgency gap(s) immediately."
        ),
    }
