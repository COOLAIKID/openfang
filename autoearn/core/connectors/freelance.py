"""Freelance platform connectors.

Platforms: Upwork, Fiverr, Toptal, PeoplePerHour, Guru, LinkedIn Jobs.
Each exposes job-search, profile-reading, and bid/proposal submission methods,
returning :class:`ConnectorResult` instances.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

import requests

from .base import Connector, ConnectorResult, register


# ---------------------------------------------------------------------------
# Upwork
# ---------------------------------------------------------------------------

@register
class UpworkConnector(Connector):
    name = "upwork"
    label = "Upwork"
    config_section = "upwork"
    required_keys = ("consumer_key", "consumer_secret", "access_token", "access_token_secret")
    capabilities = ("freelance",)

    _BASE = "https://www.upwork.com/api"

    def _session(self, cfg: dict) -> requests.Session:
        try:
            from requests_oauthlib import OAuth1  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "Upwork connector requires 'requests_oauthlib'. "
                "Install with: pip install requests-oauthlib"
            ) from exc
        sess = requests.Session()
        sess.auth = OAuth1(
            cfg["consumer_key"],
            cfg["consumer_secret"],
            cfg["access_token"],
            cfg["access_token_secret"],
        )
        return sess

    def search_jobs(
        self, keywords: str, min_budget: float = 0, job_type: str = "hourly"
    ) -> ConnectorResult:
        """Search for available jobs.

        Args:
            keywords: search query string.
            min_budget: minimum budget (0 for any).
            job_type: 'hourly' or 'fixed'.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            sess = self._session(cfg)
        except RuntimeError as exc:
            return ConnectorResult(ok=False, message=str(exc))
        params: dict[str, Any] = {
            "q": keywords,
            "job_type": job_type,
            "paging": "0;20",
        }
        if min_budget > 0:
            params["budget"] = f"[{int(min_budget)};]"
        resp = sess.get(
            f"{self._BASE}/profiles/v2/jobs/search.json",
            params=params,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Upwork search_jobs error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        jobs = data.get("jobs", {}).get("job", [])
        if isinstance(jobs, dict):
            jobs = [jobs]
        return ConnectorResult(
            ok=True,
            message=f"Upwork: {len(jobs)} jobs found for '{keywords}'.",
            data={"jobs": jobs},
        )

    def get_profile(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            sess = self._session(cfg)
        except RuntimeError as exc:
            return ConnectorResult(ok=False, message=str(exc))
        resp = sess.get(
            f"{self._BASE}/profiles/v1/contractors/me.json",
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Upwork get_profile error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("profile", resp.json())
        return ConnectorResult(
            ok=True,
            message=f"Upwork profile: {data.get('dev_first_name', '')} {data.get('dev_last_name', '')}.",
            data=data,
        )

    def get_active_contracts(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            sess = self._session(cfg)
        except RuntimeError as exc:
            return ConnectorResult(ok=False, message=str(exc))
        resp = sess.get(
            f"{self._BASE}/hr/v2/engagements.json",
            params={"status": "active", "limit": 50},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Upwork get_active_contracts error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        contracts = data.get("engagements", {}).get("engagement", [])
        if isinstance(contracts, dict):
            contracts = [contracts]
        return ConnectorResult(
            ok=True,
            message=f"Upwork: {len(contracts)} active contracts.",
            data={"contracts": contracts},
        )

    def submit_proposal(
        self, job_id: str, cover_letter: str, bid_rate: float
    ) -> ConnectorResult:
        """Submit a proposal for an Upwork job.

        Args:
            job_id: Upwork job reference ID.
            cover_letter: proposal cover letter text.
            bid_rate: hourly rate or fixed bid amount in USD.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        try:
            sess = self._session(cfg)
        except RuntimeError as exc:
            return ConnectorResult(ok=False, message=str(exc))
        resp = sess.post(
            f"{self._BASE}/hr/v3/jobs/{job_id}/applications.json",
            data={
                "cover_letter": cover_letter,
                "charge_rate": bid_rate,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Upwork submit_proposal error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Upwork proposal submitted for job {job_id} at ${bid_rate}/hr.",
            data=data,
        )


# ---------------------------------------------------------------------------
# Fiverr
# ---------------------------------------------------------------------------

@register
class FiverrConnector(Connector):
    name = "fiverr"
    label = "Fiverr"
    config_section = "fiverr"
    required_keys = ("username",)
    capabilities = ("freelance",)

    _BASE = "https://www.fiverr.com"

    def get_gigs(self) -> ConnectorResult:
        """Fetch gigs for the configured Fiverr username via public profile page."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        username = cfg["username"]
        resp = requests.get(
            f"{self._BASE}/{username}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Fiverr get_gigs error {resp.status_code}: {resp.text[:160]}",
            )
        # Parse gig titles from the profile page HTML
        matches = re.findall(r'"title"\s*:\s*"([^"]+)"', resp.text)
        gigs = list(dict.fromkeys(matches))[:20]
        return ConnectorResult(
            ok=True,
            message=f"Fiverr: {len(gigs)} gig titles found for @{username}.",
            data={"gigs": gigs, "username": username},
        )

    def get_gig_stats(self, gig_id: str) -> ConnectorResult:
        """Fetch public stats for a gig by its slug/ID.

        Args:
            gig_id: Fiverr gig slug or URL path fragment.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        url = f"{self._BASE}{gig_id}" if gig_id.startswith("/") else f"{self._BASE}/gig/{gig_id}"
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Fiverr get_gig_stats error {resp.status_code}: {resp.text[:160]}",
            )
        rating_match = re.search(r'"rating"\s*:\s*([\d.]+)', resp.text)
        review_match = re.search(r'"review_count"\s*:\s*(\d+)', resp.text)
        rating = float(rating_match.group(1)) if rating_match else None
        review_count = int(review_match.group(1)) if review_match else None
        return ConnectorResult(
            ok=True,
            message=f"Fiverr gig {gig_id}: rating={rating}, reviews={review_count}.",
            data={"gig_id": gig_id, "rating": rating, "review_count": review_count},
        )

    def search_gigs(self, category: str, query: str) -> ConnectorResult:
        """Search Fiverr gigs in a category.

        Args:
            category: Fiverr category slug e.g. 'programming-tech'.
            query: search keyword string.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/search/gigs",
            params={"query": query, "category_id": category, "source": "top-bar"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Fiverr search_gigs error {resp.status_code}: {resp.text[:160]}",
            )
        matches = re.findall(r'"title"\s*:\s*"([^"]+)"', resp.text)
        gigs = list(dict.fromkeys(matches))[:20]
        return ConnectorResult(
            ok=True,
            message=f"Fiverr search '{query}' in '{category}': {len(gigs)} results.",
            data={"gigs": gigs, "query": query, "category": category},
        )

    def get_orders(self) -> ConnectorResult:
        """Return a placeholder — Fiverr does not expose a public orders API."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        return ConnectorResult(
            ok=False,
            message=(
                "Fiverr does not provide a public Orders API. "
                "Use the Fiverr Seller Dashboard to view orders."
            ),
        )


# ---------------------------------------------------------------------------
# Toptal
# ---------------------------------------------------------------------------

@register
class ToptalConnector(Connector):
    name = "toptal"
    label = "Toptal"
    config_section = "toptal"
    required_keys = ()
    capabilities = ("freelance",)

    _BASE = "https://www.toptal.com"

    def get_job_listings(self, role: str = "developer") -> ConnectorResult:
        """Scrape public Toptal job listings for a given role type.

        Args:
            role: role keyword e.g. 'developer', 'designer', 'finance'.
        """
        resp = requests.get(
            f"{self._BASE}/jobs",
            params={"q": role},
            headers={"User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Toptal get_job_listings error {resp.status_code}: {resp.text[:160]}",
            )
        titles = re.findall(r'<h[23][^>]*>\s*([^<]{10,120})\s*</h[23]>', resp.text)
        titles = [html.unescape(t.strip()) for t in titles if role.lower() in t.lower() or True][:20]
        return ConnectorResult(
            ok=True,
            message=f"Toptal: {len(titles)} job listings found for role '{role}'.",
            data={"listings": titles, "role": role},
        )

    def get_rate_ranges(self, role: str = "developer") -> ConnectorResult:
        """Fetch publicly listed rate ranges from Toptal's hiring pages.

        Args:
            role: role type e.g. 'developer', 'designer', 'finance'.
        """
        resp = requests.get(
            f"{self._BASE}/hire/{role}s",
            headers={"User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Toptal get_rate_ranges error {resp.status_code}: {resp.text[:160]}",
            )
        # Look for rate mentions like "$X/hour" or "$X - $Y/hour"
        rate_matches = re.findall(r'\$[\d,]+\s*(?:[-–]\s*\$[\d,]+)?\s*(?:/\s*hour)?', resp.text)
        rates = list(dict.fromkeys(rate_matches))[:10]
        return ConnectorResult(
            ok=True,
            message=f"Toptal rate ranges for '{role}': {', '.join(rates) if rates else 'not found'}.",
            data={"rates": rates, "role": role},
        )


# ---------------------------------------------------------------------------
# PeoplePerHour
# ---------------------------------------------------------------------------

@register
class PeoplePerHourConnector(Connector):
    name = "peopleperhour"
    label = "PeoplePerHour"
    config_section = "peopleperhour"
    required_keys = ("api_key", "user_id")
    capabilities = ("freelance",)

    _BASE = "https://api.peopleperhour.com/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "X-API-KEY": cfg["api_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def search_projects(
        self, skills: list[str], min_budget: float = 0
    ) -> ConnectorResult:
        """Search for projects matching given skills.

        Args:
            skills: list of skill/keyword strings.
            min_budget: minimum budget in GBP/USD (0 = any).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        params: dict[str, Any] = {
            "q": " ".join(skills),
            "page": 1,
            "per_page": 20,
        }
        if min_budget > 0:
            params["budget_min"] = min_budget
        resp = requests.get(
            f"{self._BASE}/job/list",
            headers=self._headers(cfg),
            params=params,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"PeoplePerHour search_projects error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        jobs = data.get("data", data.get("jobs", []))
        return ConnectorResult(
            ok=True,
            message=f"PeoplePerHour: {len(jobs)} projects for skills {skills}.",
            data={"jobs": jobs},
        )

    def get_profile_stats(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/user/{cfg['user_id']}",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"PeoplePerHour get_profile_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", resp.json())
        rating = data.get("rating", {}).get("score", data.get("score", "?"))
        return ConnectorResult(
            ok=True,
            message=f"PeoplePerHour profile {cfg['user_id']}: rating={rating}.",
            data=data,
        )

    def submit_bid(
        self, project_id: str, amount: float, cover: str
    ) -> ConnectorResult:
        """Submit a bid on a PeoplePerHour project.

        Args:
            project_id: project ID to bid on.
            amount: bid amount in the project's currency.
            cover: cover letter / proposal text.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/proposal/create",
            headers=self._headers(cfg),
            json={
                "job_id": project_id,
                "amount": amount,
                "description": cover,
                "duration": 1,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"PeoplePerHour submit_bid error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json().get("data", resp.json())
        return ConnectorResult(
            ok=True,
            message=f"PeoplePerHour bid submitted on project {project_id} for ${amount:.2f}.",
            data=data,
        )


# ---------------------------------------------------------------------------
# Guru
# ---------------------------------------------------------------------------

@register
class GuruConnector(Connector):
    name = "guru"
    label = "Guru.com"
    config_section = "guru"
    required_keys = ("api_key",)
    capabilities = ("freelance",)

    _BASE = "https://www.guru.com/api/1.0"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"api_key {cfg['api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def search_jobs(self, skills: list[str], category: str = "") -> ConnectorResult:
        """Search for Guru.com jobs.

        Args:
            skills: list of skill keywords.
            category: optional Guru category string.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        params: dict[str, Any] = {"q": " ".join(skills), "page": 1, "pageSize": 20}
        if category:
            params["category"] = category
        resp = requests.get(
            f"{self._BASE}/jobs",
            headers=self._headers(cfg),
            params=params,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Guru search_jobs error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        jobs = data.get("items", data.get("jobs", []))
        return ConnectorResult(
            ok=True,
            message=f"Guru.com: {len(jobs)} jobs found for skills {skills}.",
            data={"jobs": jobs},
        )

    def get_categories(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/categories",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Guru get_categories error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        categories = data.get("items", data.get("categories", []))
        return ConnectorResult(
            ok=True,
            message=f"Guru.com: {len(categories)} categories.",
            data={"categories": categories},
        )

    def submit_quote(self, job_id: str, amount: float, cover: str) -> ConnectorResult:
        """Submit a quote/proposal on a Guru.com job.

        Args:
            job_id: Guru.com job ID.
            amount: proposed amount in USD.
            cover: proposal / cover letter text.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/jobs/{job_id}/quotes",
            headers=self._headers(cfg),
            json={
                "bidAmount": amount,
                "coverLetter": cover,
                "currency": "USD",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Guru submit_quote error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Guru.com quote submitted for job {job_id} at ${amount:.2f}.",
            data=data,
        )


# ---------------------------------------------------------------------------
# LinkedIn Jobs (scrape — no auth)
# ---------------------------------------------------------------------------

@register
class LinkedInJobsConnector(Connector):
    name = "linkedin_jobs"
    label = "LinkedIn Jobs"
    config_section = "linkedin_jobs"
    required_keys = ()
    capabilities = ("freelance",)

    _BASE = "https://www.linkedin.com/jobs"
    _API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def search_jobs(
        self, keywords: str, location: str = "remote", job_type: str = "contract"
    ) -> ConnectorResult:
        """Search for LinkedIn job listings (public, no auth required).

        Args:
            keywords: search keyword string.
            location: location filter e.g. 'remote', 'New York', 'United Kingdom'.
            job_type: 'contract', 'full-time', 'part-time', 'internship'.
        """
        # Map job_type to LinkedIn's f_JT codes
        jt_map = {
            "full-time": "F",
            "part-time": "P",
            "contract": "C",
            "temporary": "T",
            "internship": "I",
            "volunteer": "V",
        }
        f_jt = jt_map.get(job_type.lower(), "C")
        resp = requests.get(
            "https://www.linkedin.com/jobs/search",
            params={
                "keywords": keywords,
                "location": location,
                "f_JT": f_jt,
                "start": 0,
                "count": 25,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"LinkedIn Jobs search error {resp.status_code}: {resp.text[:160]}",
            )
        # Extract job card data from HTML
        titles = re.findall(
            r'class="base-search-card__title"[^>]*>\s*([^<]{5,120})\s*<',
            resp.text,
        )
        companies = re.findall(
            r'class="base-search-card__subtitle"[^>]*>\s*([^<]{3,80})\s*<',
            resp.text,
        )
        job_ids_raw = re.findall(r'data-entity-urn="urn:li:jobPosting:(\d+)"', resp.text)
        jobs = [
            {
                "title": html.unescape(titles[i].strip()) if i < len(titles) else "",
                "company": html.unescape(companies[i].strip()) if i < len(companies) else "",
                "job_id": job_ids_raw[i] if i < len(job_ids_raw) else "",
            }
            for i in range(max(len(titles), len(job_ids_raw)))
        ]
        return ConnectorResult(
            ok=True,
            message=f"LinkedIn Jobs: {len(jobs)} listings for '{keywords}' ({location}).",
            data={"jobs": jobs, "keywords": keywords, "location": location},
        )

    def get_job_details(self, job_id: str) -> ConnectorResult:
        """Fetch details for a specific LinkedIn job posting.

        Args:
            job_id: LinkedIn job posting numeric ID.
        """
        resp = requests.get(
            f"https://www.linkedin.com/jobs/view/{job_id}",
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"LinkedIn Jobs get_job_details error {resp.status_code}: {resp.text[:160]}",
            )
        # Extract structured data if present
        json_ld_matches = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', resp.text, re.DOTALL
        )
        job_data: dict[str, Any] = {"job_id": job_id}
        for block in json_ld_matches:
            try:
                parsed = json.loads(block)
                if parsed.get("@type") == "JobPosting":
                    job_data = parsed
                    break
            except (json.JSONDecodeError, ValueError):
                continue
        title = job_data.get("title", "")
        company = (
            job_data.get("hiringOrganization", {}).get("name", "")
            if isinstance(job_data.get("hiringOrganization"), dict)
            else ""
        )
        return ConnectorResult(
            ok=True,
            message=f"LinkedIn job {job_id}: '{title}' at '{company}'.",
            data=job_data,
        )
