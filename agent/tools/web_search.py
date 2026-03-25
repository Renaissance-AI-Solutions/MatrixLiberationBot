"""
agent/tools/web_search.py
=========================
Liberation Bot — Tiered Web Search Tool

Implements a three-tier fallback search strategy designed for cost-efficiency:

  Tier 1 — DuckDuckGo (FREE, no API key)
    Uses the `duckduckgo-search` library for instant, zero-cost searches.
    Rate-limited by DDG; suitable for most queries.

  Tier 2 — Serper.dev (LOW COST, ~$0.001/query)
    Falls back to Serper when DDG is rate-limited or returns no results.
    Requires SERPER_API_KEY in .env.

  Tier 3 — Tavily (MODERATE COST, ~$0.01/query)
    Falls back to Tavily when Serper also fails.
    Provides AI-extracted, structured search results with source citations.
    Requires TAVILY_API_KEY in .env.

The tool is exposed to the AgentCore as a callable function and also used
directly by the FOIA dialogue agent for real-time agency contact lookups.

Watched Topics
--------------
A configurable list of topics is checked on a schedule (via the bot scheduler).
When new results are found for a watched topic, the bot posts a summary to the
configured group room. This enables proactive monitoring of:
  - New AHI/Havana Syndrome research
  - FOIA-related legal developments
  - Neurowarfare program disclosures
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Maximum number of results to return per search
DEFAULT_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))

# Minimum seconds between DDG calls to avoid rate-limiting
DDG_THROTTLE_SECONDS = float(os.getenv("DDG_THROTTLE_SECONDS", "2.0"))

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single search result from any tier."""
    title: str
    url: str
    snippet: str
    source_tier: str  # "ddg", "serper", or "tavily"


@dataclass
class SearchResponse:
    """The full response from a web search call."""
    query: str
    results: List[SearchResult] = field(default_factory=list)
    tier_used: str = ""
    error: Optional[str] = None
    latency_ms: int = 0

    @property
    def success(self) -> bool:
        return bool(self.results) and not self.error

    def format_for_agent(self) -> str:
        """
        Format results as a compact Markdown string suitable for injection
        into an LLM context window.
        """
        if not self.success:
            return f"Web search for '{self.query}' returned no results. {self.error or ''}"
        lines = [f"**Web Search Results** for: _{self.query}_ (via {self.tier_used})\n"]
        for i, r in enumerate(self.results, 1):
            lines.append(f"{i}. **{r.title}**")
            lines.append(f"   {r.snippet}")
            lines.append(f"   Source: {r.url}\n")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier 1: DuckDuckGo
# ---------------------------------------------------------------------------

_ddg_last_call_ts: float = 0.0


def _search_ddg(query: str, max_results: int) -> List[SearchResult]:
    """Search via DuckDuckGo (free, no API key). Applies throttle."""
    global _ddg_last_call_ts
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        logger.warning("duckduckgo-search not installed. Skipping DDG tier.")
        return []

    # Throttle to avoid DDG rate-limiting
    elapsed = time.time() - _ddg_last_call_ts
    if elapsed < DDG_THROTTLE_SECONDS:
        time.sleep(DDG_THROTTLE_SECONDS - elapsed)

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        _ddg_last_call_ts = time.time()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
                source_tier="ddg",
            )
            for r in raw
            if r.get("href")
        ]
    except Exception as exc:
        logger.warning("DDG search failed for '%s': %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Tier 2: Serper.dev
# ---------------------------------------------------------------------------

def _search_serper(query: str, max_results: int) -> List[SearchResult]:
    """Search via Serper.dev (~$0.001/query). Requires SERPER_API_KEY."""
    if not SERPER_API_KEY:
        logger.debug("SERPER_API_KEY not set; skipping Serper tier.")
        return []
    try:
        import requests as _requests
        resp = _requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": max_results},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        organic = data.get("organic", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
                source_tier="serper",
            )
            for r in organic[:max_results]
            if r.get("link")
        ]
    except Exception as exc:
        logger.warning("Serper search failed for '%s': %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Tier 3: Tavily
# ---------------------------------------------------------------------------

def _search_tavily(query: str, max_results: int) -> List[SearchResult]:
    """Search via Tavily (~$0.01/query). Requires TAVILY_API_KEY."""
    if not TAVILY_API_KEY:
        logger.debug("TAVILY_API_KEY not set; skipping Tavily tier.")
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.search(query=query, max_results=max_results)
        results = resp.get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                source_tier="tavily",
            )
            for r in results[:max_results]
            if r.get("url")
        ]
    except Exception as exc:
        logger.warning("Tavily search failed for '%s': %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Public API: tiered_search
# ---------------------------------------------------------------------------

def tiered_search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    force_tier: Optional[str] = None,
) -> SearchResponse:
    """
    Execute a web search using the tiered fallback strategy.

    Parameters
    ----------
    query : str
        The search query.
    max_results : int
        Maximum number of results to return.
    force_tier : str, optional
        Force a specific tier: "ddg", "serper", or "tavily".
        Used for testing or when a specific source is required.

    Returns
    -------
    SearchResponse
        Contains results, the tier used, and timing metadata.
    """
    start_ts = time.time()
    results: List[SearchResult] = []
    tier_used = ""
    error = None

    tiers = (
        [force_tier] if force_tier
        else ["ddg", "serper", "tavily"]
    )

    for tier in tiers:
        if tier == "ddg":
            results = _search_ddg(query, max_results)
            tier_used = "DuckDuckGo"
        elif tier == "serper":
            results = _search_serper(query, max_results)
            tier_used = "Serper.dev"
        elif tier == "tavily":
            results = _search_tavily(query, max_results)
            tier_used = "Tavily"
        else:
            logger.warning("Unknown search tier: %s", tier)
            continue

        if results:
            break
        logger.info("Tier '%s' returned no results for '%s'; trying next tier.", tier, query)

    if not results:
        error = "All search tiers exhausted with no results."
        logger.warning("Web search failed for query: %s", query)

    latency_ms = int((time.time() - start_ts) * 1000)
    return SearchResponse(
        query=query,
        results=results,
        tier_used=tier_used,
        error=error,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Watched Topics
# ---------------------------------------------------------------------------

# Default watched topics for proactive monitoring.
# These can be overridden via the FOIA_WATCHED_TOPICS env var (comma-separated).
DEFAULT_WATCHED_TOPICS = [
    "Havana Syndrome new research 2025",
    "Anomalous Health Incidents directed energy weapons",
    "FOIA neurowarfare declassified documents",
    "CIA AHI investigation update",
    "directed energy weapons civilian victims",
]


def get_watched_topics() -> List[str]:
    """
    Return the list of topics to monitor. Reads from FOIA_WATCHED_TOPICS
    env var if set, otherwise uses the defaults.
    """
    env_topics = os.getenv("FOIA_WATCHED_TOPICS", "")
    if env_topics:
        return [t.strip() for t in env_topics.split(",") if t.strip()]
    return DEFAULT_WATCHED_TOPICS


async def run_watched_topic_scan(
    on_results: callable,
    max_results_per_topic: int = 3,
) -> None:
    """
    Scan all watched topics and call `on_results(topic, response)` for each
    topic that returns results. Designed to be called by the APScheduler.

    Parameters
    ----------
    on_results : async callable (topic: str, response: SearchResponse) -> None
        Called for each topic that has results. The caller (bot.py) is
        responsible for posting summaries to the group room.
    max_results_per_topic : int
        Maximum results to fetch per topic.
    """
    import asyncio
    topics = get_watched_topics()
    logger.info("Running watched topic scan for %d topics.", len(topics))
    for topic in topics:
        try:
            # Run the blocking search in a thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda t=topic: tiered_search(t, max_results=max_results_per_topic)
            )
            if response.success:
                await on_results(topic, response)
        except Exception as exc:
            logger.error("Watched topic scan failed for '%s': %s", topic, exc)


# ---------------------------------------------------------------------------
# OpenAI Function-Calling Tool Schema
# ---------------------------------------------------------------------------

WEB_SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information about Neurowarfare, Havana Syndrome, "
            "AHIs, directed energy weapons, FOIA-related legal developments, or any other "
            "topic relevant to the NPWA's mission. Use this when the Liberation Archives "
            "do not contain sufficiently current or specific information. "
            "Results are returned as a formatted list of titles, snippets, and URLs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The web search query. Be specific and targeted. "
                        "Example: 'CIA Havana Syndrome investigation 2025' or "
                        "'FOIA appeal denial directed energy weapons'."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (1-10). Defaults to 5.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
}
