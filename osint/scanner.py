"""
osint/scanner.py
================
Open-Source Intelligence (OSINT) scanning module.

Strictly limited to:
  1. Searching public social media handles provided explicitly by the user.
  2. Searching public news and obituary databases via SerpAPI.

This module does NOT attempt to access private accounts, bypass authentication,
or scrape data beyond what the user has explicitly provided consent for.
"""

import asyncio
import json
import logging
import aiohttp
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


class OSINTScanner:
    """
    Performs ethical, consent-limited OSINT checks for a registered user.

    All searches are based solely on:
      - Social media handles the user explicitly registered.
      - The user's self-reported location and display name.
      - Public web search results (via SerpAPI).
    """

    SERPAPI_BASE = "https://serpapi.com/search.json"

    # Known public profile URL patterns for common platforms
    PLATFORM_URL_TEMPLATES = {
        "twitter":   "https://twitter.com/{handle}",
        "mastodon":  "https://{server}/@{handle}",
        "bluesky":   "https://bsky.app/profile/{handle}",
        "instagram": "https://www.instagram.com/{handle}/",
        "facebook":  "https://www.facebook.com/{handle}",
        "linkedin":  "https://www.linkedin.com/in/{handle}",
        "github":    "https://github.com/{handle}",
        "reddit":    "https://www.reddit.com/user/{handle}",
    }

    def __init__(self, serpapi_key: Optional[str] = None):
        self.serpapi_key = serpapi_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_full_scan(
        self,
        display_name: str,
        location: Optional[str],
        social_handles: Optional[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Run all OSINT checks and return a consolidated result dict.

        Returns:
            {
                "found_activity": bool,
                "summary": str,
                "details": list[str],
            }
        """
        details: List[str] = []
        found_activity = False

        # Step 1: Check public social media profiles
        if social_handles:
            social_result = await self._check_social_handles(social_handles)
            details.extend(social_result["details"])
            if social_result["found"]:
                found_activity = True

        # Step 2: Web search for news / obituaries
        if self.serpapi_key:
            news_result = await self._search_news_obituaries(display_name, location)
            details.extend(news_result["details"])
            if news_result["found_concerning"]:
                details.append(
                    "WARNING: Potentially concerning content found in news/obituary search."
                )
            if news_result["found_activity"]:
                found_activity = True
        else:
            details.append(
                "OSINT: SerpAPI key not configured — skipping news/obituary search."
            )

        summary = (
            "Recent public activity detected — absence may be intentional."
            if found_activity
            else "No recent public activity found across checked sources."
        )

        logger.info(
            "OSINT scan complete for '%s': found_activity=%s, detail_count=%d",
            display_name,
            found_activity,
            len(details),
        )
        return {"found_activity": found_activity, "summary": summary, "details": details}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_social_handles(
        self, social_handles: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Check each provided public social media handle for recent activity.
        Uses HTTP HEAD requests to verify profile existence; does NOT scrape
        private content.
        """
        details = []
        found = False
        session = await self._get_session()

        for platform, handle in social_handles.items():
            if not handle:
                continue
            platform_lower = platform.lower()
            url = self._build_profile_url(platform_lower, handle)
            if not url:
                details.append(f"Social [{platform}]: URL template not known, skipping.")
                continue
            try:
                async with session.head(url, allow_redirects=True) as resp:
                    if resp.status == 200:
                        details.append(
                            f"Social [{platform}]: Profile page reachable at {url} (HTTP 200)."
                        )
                        # A reachable profile is not itself "activity" — we note it
                    elif resp.status == 404:
                        details.append(
                            f"Social [{platform}]: Profile not found at {url} (HTTP 404)."
                        )
                    else:
                        details.append(
                            f"Social [{platform}]: Unexpected HTTP {resp.status} for {url}."
                        )
            except aiohttp.ClientError as exc:
                details.append(f"Social [{platform}]: Network error checking {url}: {exc}")

        # For a more thorough check, use SerpAPI to search for recent posts
        if self.serpapi_key:
            for platform, handle in social_handles.items():
                serp_result = await self._serpapi_search(
                    f'site:{platform.lower()}.com "{handle}" after:7days',
                    description=f"recent {platform} posts for @{handle}",
                )
                if serp_result.get("organic_results"):
                    found = True
                    details.append(
                        f"Social [{platform}]: Recent public posts found for @{handle} via web search."
                    )
                else:
                    details.append(
                        f"Social [{platform}]: No recent public posts found for @{handle} via web search."
                    )

        return {"found": found, "details": details}

    async def _search_news_obituaries(
        self, display_name: str, location: Optional[str]
    ) -> Dict[str, Any]:
        """
        Search public news sources and obituary databases for the user's name.
        """
        details = []
        found_activity = False
        found_concerning = False

        # Build targeted search queries
        location_str = location or ""
        queries = [
            f'"{display_name}" {location_str} obituary',
            f'"{display_name}" {location_str} missing person',
            f'"{display_name}" {location_str} news',
        ]

        concerning_keywords = [
            "obituary", "obit", "passed away", "died", "death", "deceased",
            "missing", "missing person", "last seen", "foul play",
        ]
        activity_keywords = [
            "posted", "tweeted", "said", "announced", "published", "shared",
            "spoke", "appeared",
        ]

        for query in queries:
            result = await self._serpapi_search(query, description=query)
            organic = result.get("organic_results", [])
            if organic:
                for item in organic[:3]:
                    snippet = (item.get("snippet") or "").lower()
                    title = (item.get("title") or "").lower()
                    combined = snippet + " " + title

                    if any(kw in combined for kw in concerning_keywords):
                        found_concerning = True
                        details.append(
                            f"NEWS/OBIT: Potentially concerning result: '{item.get('title')}' — {item.get('link')}"
                        )
                    elif any(kw in combined for kw in activity_keywords):
                        found_activity = True
                        details.append(
                            f"NEWS: Recent activity mention found: '{item.get('title')}' — {item.get('link')}"
                        )
            else:
                details.append(f"NEWS: No results for query: {query}")

        return {
            "found_activity": found_activity,
            "found_concerning": found_concerning,
            "details": details,
        }

    async def _serpapi_search(self, query: str, description: str = "") -> Dict[str, Any]:
        """Make a SerpAPI Google search request and return the JSON response."""
        if not self.serpapi_key:
            return {}
        session = await self._get_session()
        params = {
            "q": query,
            "api_key": self.serpapi_key,
            "num": 5,
            "hl": "en",
            "gl": "us",
        }
        try:
            async with session.get(self.SERPAPI_BASE, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug("SerpAPI query '%s' returned %d organic results.",
                                 description, len(data.get("organic_results", [])))
                    return data
                else:
                    logger.warning("SerpAPI returned HTTP %d for query: %s", resp.status, description)
                    return {}
        except aiohttp.ClientError as exc:
            logger.error("SerpAPI request failed for '%s': %s", description, exc)
            return {}

    def _build_profile_url(self, platform: str, handle: str) -> Optional[str]:
        """Build a public profile URL for a given platform and handle."""
        template = self.PLATFORM_URL_TEMPLATES.get(platform)
        if not template:
            return None
        # Handle Mastodon format: user@server
        if platform == "mastodon" and "@" in handle:
            parts = handle.lstrip("@").split("@", 1)
            if len(parts) == 2:
                return f"https://{parts[1]}/@{parts[0]}"
        return template.format(handle=handle.lstrip("@"))
