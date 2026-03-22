"""
agent/tools/liberation_archives.py
====================================
Liberation Archives Tool — NotebookLM Query Interface

This module provides the `query_liberation_archives` async function, which
queries a designated Google NotebookLM notebook (the "Liberation Archives")
containing research on Havana Syndrome, Neurowarfare, Anomalous Health
Incidents (AHIs), and Neurostrike attacks.

It uses the `notebooklm-py` unofficial Python API (teng-lin/notebooklm-py).

Authentication:
  Set one of the following environment variables:
    - NOTEBOOKLM_AUTH_JSON : Inline JSON of the auth storage state (preferred for servers)
    - NOTEBOOKLM_HOME      : Path to directory containing storage_state.json

  The notebook ID is set via:
    - LIBERATION_ARCHIVES_NOTEBOOK_ID : The NotebookLM notebook ID to query

  To obtain auth credentials, run `notebooklm login` on a machine with a browser,
  then copy the resulting ~/.notebooklm/storage_state.json content into
  the NOTEBOOKLM_AUTH_JSON environment variable.

Security:
  This tool is READ-ONLY. It only calls client.chat.ask() and never modifies
  the notebook or uploads documents. Source management is done manually by
  NPWA administrators.
"""

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# The NotebookLM notebook ID for the Liberation Archives.
# Set this in your .env file.
LIBERATION_ARCHIVES_NOTEBOOK_ID = os.getenv("LIBERATION_ARCHIVES_NOTEBOOK_ID", "")

# Maximum time to wait for a NotebookLM response (seconds)
NOTEBOOKLM_TIMEOUT_SECS = int(os.getenv("NOTEBOOKLM_TIMEOUT_SECS", "60"))

# Whether NotebookLM integration is enabled
NOTEBOOKLM_ENABLED = bool(
    os.getenv("NOTEBOOKLM_AUTH_JSON") or os.getenv("NOTEBOOKLM_HOME")
)


class LiberationArchivesError(Exception):
    """Raised when the Liberation Archives tool fails."""
    pass


async def query_liberation_archives(query: str) -> str:
    """
    Query the Liberation Archives NotebookLM notebook with a research question.

    This is the primary tool exposed to the Kimi K2 agent for grounding its
    responses in verified research about Neurowarfare and Havana Syndrome.

    Args:
        query: A natural language research question. Should be specific and
               focused on Havana Syndrome, Neurowarfare, AHIs, directed energy
               weapons, symptoms, legal precedents, or related topics.

    Returns:
        A string containing the NotebookLM answer, grounded in the Liberation
        Archives source documents. Returns an error message string if the
        query fails (so the agent can gracefully handle failures).

    Example:
        answer = await query_liberation_archives(
            "What are the neurological symptoms most commonly reported by "
            "Havana Syndrome victims?"
        )
    """
    if not NOTEBOOKLM_ENABLED:
        logger.warning(
            "NotebookLM integration is not configured. "
            "Set NOTEBOOKLM_AUTH_JSON or NOTEBOOKLM_HOME to enable."
        )
        return (
            "[Liberation Archives Unavailable] NotebookLM authentication is not "
            "configured on this server. Please contact the NPWA administrator to "
            "set up the Liberation Archives connection."
        )

    if not LIBERATION_ARCHIVES_NOTEBOOK_ID:
        logger.warning("LIBERATION_ARCHIVES_NOTEBOOK_ID is not set.")
        return (
            "[Liberation Archives Unavailable] The Liberation Archives notebook ID "
            "has not been configured. Please contact the NPWA administrator."
        )

    try:
        # Import here to avoid hard dependency if notebooklm-py is not installed
        from notebooklm import NotebookLMClient, RPCError
    except ImportError:
        logger.error(
            "notebooklm-py is not installed. Run: pip install notebooklm-py"
        )
        return (
            "[Liberation Archives Unavailable] The notebooklm-py library is not "
            "installed on this server. Please run: pip install notebooklm-py"
        )

    start_time = time.monotonic()
    logger.info(
        "Querying Liberation Archives: %s",
        query[:100] + ("..." if len(query) > 100 else ""),
    )

    try:
        async with await NotebookLMClient.from_storage() as client:
            result = await asyncio.wait_for(
                client.chat.ask(LIBERATION_ARCHIVES_NOTEBOOK_ID, query),
                timeout=NOTEBOOKLM_TIMEOUT_SECS,
            )
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.info(
                "Liberation Archives query completed in %dms. Answer length: %d chars.",
                elapsed_ms,
                len(result.answer),
            )
            return result.answer

    except asyncio.TimeoutError:
        logger.error(
            "Liberation Archives query timed out after %ds.", NOTEBOOKLM_TIMEOUT_SECS
        )
        return (
            f"[Liberation Archives Timeout] The query to the Liberation Archives "
            f"timed out after {NOTEBOOKLM_TIMEOUT_SECS} seconds. Please try again "
            f"with a more specific question."
        )
    except Exception as exc:
        logger.error("Liberation Archives query failed: %s", exc, exc_info=True)
        return (
            f"[Liberation Archives Error] The query failed: {type(exc).__name__}. "
            f"The archives may be temporarily unavailable. Please try again later."
        )


async def list_liberation_archives_topics() -> str:
    """
    Get a description of the Liberation Archives notebook, including
    its AI-generated summary and suggested research topics.

    Returns:
        A formatted string with the notebook summary and topic suggestions.
    """
    if not NOTEBOOKLM_ENABLED or not LIBERATION_ARCHIVES_NOTEBOOK_ID:
        return "[Liberation Archives Unavailable] Not configured."

    try:
        from notebooklm import NotebookLMClient
    except ImportError:
        return "[Liberation Archives Unavailable] notebooklm-py not installed."

    try:
        async with await NotebookLMClient.from_storage() as client:
            desc = await client.notebooks.get_description(
                LIBERATION_ARCHIVES_NOTEBOOK_ID
            )
            lines = ["**Liberation Archives — Overview**", "", desc.summary, ""]
            if desc.suggested_topics:
                lines.append("**Suggested Research Topics:**")
                for topic in desc.suggested_topics[:8]:
                    lines.append(f"- {topic.question}")
            return "\n".join(lines)
    except Exception as exc:
        logger.error("Failed to get Liberation Archives description: %s", exc)
        return f"[Liberation Archives Error] Could not retrieve topics: {exc}"


# ---------------------------------------------------------------------------
# OpenAI Tool Schema
# This is the JSON schema used to register this function as a tool
# with the Kimi K2 agent via the OpenAI function-calling API.
# ---------------------------------------------------------------------------

LIBERATION_ARCHIVES_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_liberation_archives",
        "description": (
            "Query the Liberation Archives — a curated research knowledge base "
            "containing verified documents, case studies, medical research, legal "
            "precedents, and advocacy materials about Havana Syndrome, Neurowarfare, "
            "Anomalous Health Incidents (AHIs), and Neurostrike attacks. "
            "Use this tool whenever a user asks a factual question about these topics. "
            "Always cite this tool as your source when using its output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A specific, focused research question to ask the Liberation "
                        "Archives. Examples: 'What neurological symptoms are most "
                        "commonly reported by Havana Syndrome victims?', 'What legal "
                        "remedies are available to AHI victims under US law?', "
                        "'What directed energy weapons are known to cause AHIs?'"
                    ),
                }
            },
            "required": ["query"],
        },
    },
}
