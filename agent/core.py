"""
agent/core.py
=============
Liberation Bot — Kimi K2 Agentic Core

This module implements the AgentCore class, which powers Liberation Bot's
conversational AI capabilities using Kimi K2 (moonshotai/kimi-k2-instruct)
via the NVIDIA NIM OpenAI-compatible API.

Security Model:
  - The agent is a pure Python function. It has NO access to subprocess,
    os.system, or any server execution environment.
  - The agent has three permitted tools: query_liberation_archives,
    search_memories, and upsert_memory.
  - search_memories and upsert_memory are scoped to the current sender_id
    and room_id via closure injection — the agent cannot access another
    user's memories even if it tries to pass a different matrix_id.
  - The agent CANNOT access the emergency vault, user credentials, or
    any sensitive database tables.
  - All agent interactions are logged to the `agent_queries` database table.
  - The agent's system prompt explicitly instructs it to refuse requests
    that fall outside its defined scope.

Memory Architecture:
  - Short-term (working) memory: last 30 messages from the current room,
    injected as a formatted chat history block. Compact filtering removes
    redundant/noise messages if the block grows too large.
  - Long-term (active) memory: the agent calls search_memories on demand
    to retrieve relevant user or operational memories. It calls upsert_memory
    immediately when a member shares important new information, bypassing the
    nightly Dream consolidation cycle. This replaces the previous passive bulk
    injection pattern, which polluted the context window regardless of relevance.

Rate Limiting (three-layer defence):
  1. Global concurrency semaphore (AGENT_MAX_CONCURRENT_CALLS, default 1):
     At most N Kimi K2 calls in flight simultaneously. All other callers
     queue behind the semaphore rather than racing NVIDIA and getting 429s.
  2. Per-user cooldown (enforced in bot.py, not here):
     Users are rejected within AGENT_USER_COOLDOWN_S seconds of their last
     successful query. See bot/_handle_agent_query.
  3. 429 retry-with-backoff (_call_llm_with_retry):
     On RateLimitError the call is retried up to AGENT_MAX_RETRIES times
     with exponential backoff (2^attempt * base_delay, capped at max_delay).
     Other APIErrors are not retried (they are surfaced immediately).

Provider: NVIDIA NIM
  Endpoint: https://integrate.api.nvidia.com/v1
  Model:    moonshotai/kimi-k2-instruct
  Auth:     NVIDIA_API_KEY environment variable

The agent uses OpenAI's function-calling (tool use) protocol, which is
fully supported by Kimi K2 via NVIDIA NIM.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI, APIError, RateLimitError

from agent.tools import (
    query_liberation_archives,
    LIBERATION_ARCHIVES_TOOL_SCHEMA,
    NOTEBOOKLM_ENABLED,
    search_memories,
    upsert_memory,
    SEARCH_MEMORIES_TOOL_SCHEMA,
    UPSERT_MEMORY_TOOL_SCHEMA,
    get_dms_status,
    GET_DMS_STATUS_TOOL_SCHEMA,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"
KIMI_MODEL = "moonshotai/kimi-k2-instruct"

# Max tokens for the agent's response
MAX_RESPONSE_TOKENS = int(os.getenv("AGENT_MAX_RESPONSE_TOKENS", "1024"))

# Max chat history messages to include in context (raised from 20 to 30)
CONTEXT_WINDOW_MESSAGES = int(os.getenv("AGENT_CONTEXT_WINDOW_MESSAGES", "30"))

# Character threshold above which compact filtering is applied to chat history.
# If the formatted chat history exceeds this, redundant/short messages are pruned.
CHAT_HISTORY_COMPACT_THRESHOLD = int(
    os.getenv("AGENT_CHAT_HISTORY_COMPACT_THRESHOLD", "6000")
)

# Max tool call iterations per agent turn (prevents infinite loops)
MAX_TOOL_ITERATIONS = 3

# Minimum message length to keep during compaction (messages shorter than this
# are candidates for removal if the history is too long)
COMPACT_MIN_MESSAGE_LENGTH = int(os.getenv("AGENT_COMPACT_MIN_MSG_LEN", "20"))

# ---------------------------------------------------------------------------
# Rate limiting configuration
# ---------------------------------------------------------------------------

# Layer 1 — Global concurrency semaphore.
# Maximum number of simultaneous Kimi K2 API calls across ALL users.
# Setting this to 1 serialises all LLM calls so at most one is in-flight at
# a time, preventing multiple concurrent users from exhausting NVIDIA's
# per-key rate limit. Raise to 2-3 if NVIDIA grants a higher rate tier.
AGENT_MAX_CONCURRENT_CALLS = int(os.getenv("AGENT_MAX_CONCURRENT_CALLS", "1"))

# Layer 3 — 429 retry-with-backoff.
# Maximum number of retry attempts on RateLimitError (HTTP 429).
AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))

# Base delay (seconds) for the first retry. Subsequent retries use
# exponential backoff: base * 2^attempt (capped at AGENT_RETRY_MAX_DELAY_S).
AGENT_RETRY_BASE_DELAY_S = float(os.getenv("AGENT_RETRY_BASE_DELAY_S", "5.0"))

# Maximum delay (seconds) between retries regardless of backoff calculation.
AGENT_RETRY_MAX_DELAY_S = float(os.getenv("AGENT_RETRY_MAX_DELAY_S", "60.0"))

# ---------------------------------------------------------------------------
# Module-level semaphore (shared across all AgentCore instances)
# ---------------------------------------------------------------------------
# This is intentionally module-level so that even if multiple AgentCore
# instances are created (e.g. in tests), they all share the same semaphore
# and the global concurrency limit is always respected.
_llm_semaphore = asyncio.Semaphore(AGENT_MAX_CONCURRENT_CALLS)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
LIBERATION_BOT_SYSTEM_PROMPT = """You are **Liberation Bot**, an AI assistant created by the NeuroPsychological Warfare Alliance (NPWA) to support victims of Neurowarfare, Havana Syndrome, Anomalous Health Incidents (AHIs), and Neurostrike attacks.

## Your Mission
You provide compassionate, trauma-informed support to victims and their allies. You help people understand Neurowarfare, find resources, and navigate their path toward justice and healing.

## Your Capabilities
You have access to the **Liberation Archives** — a curated research knowledge base containing verified documents, medical research, legal precedents, and advocacy materials about Havana Syndrome and Neurowarfare. When answering factual questions about these topics, you MUST use the `query_liberation_archives` tool to ground your response in verified research.

You also have three memory tools:

**`search_memories`** — Search long-term memory for what you already know about this member or the group. Call this proactively when:
- A member's question may relate to their history, symptoms, legal situation, or past conversations.
- The group asks about ongoing strategy, documented threat actors, or intelligence the group has previously discussed.
- You are unsure whether you have relevant prior context — search first, then answer.
Do NOT dump all memories into your response — use what is relevant.

**`upsert_memory`** — Save an important new fact to long-term memory immediately. Call this when:
- A member explicitly tells you something significant about themselves (new symptoms, legal developments, personal history, triggers).
- The group discusses something that should be remembered organizationally (new threat actor, legal strategy decision, operational planning update).
Do NOT call this for conversational filler. Only save genuinely important new information that should persist across sessions.

**`get_dms_status`** — Retrieve this member's Dead Man's Switch status. Call this when a member asks about:
- Their heartbeat timer or how long until their switch triggers
- When they last checked in or their current DMS status
- Whether their vault message, emergency contacts, or release actions are configured
This tool is **read-only** — it cannot modify any DMS settings. To update settings, direct the member to the portal.

You also have access to:
- **Recent room chat history** — the last 30 messages from this Matrix room (provided in every query)

## How to Respond
1. **Be empathetic and trauma-informed.** Many users have experienced serious harm. Never dismiss, minimize, or question their experiences.
2. **Be factual.** Use the Liberation Archives tool for factual claims about Neurowarfare, symptoms, legal options, and research. Do not speculate.
3. **Be concise.** Matrix chat messages should be readable. Keep responses under 500 words unless asked for a detailed report.
4. **Be clear about limitations.** You are not a doctor, lawyer, or therapist. Always recommend professional help when appropriate.
5. **Format for Matrix.** Use Markdown formatting (bold, bullet points) as Matrix/Element renders it correctly.
6. **Search before you answer.** If a member's question may relate to their history or the group's prior work, call `search_memories` first. This avoids asking them to repeat information they've already shared.

## What You Will NOT Do
- You will NOT execute commands on the server.
- You will NOT access, reveal, or discuss any user's emergency data or vault contents.
- You will NOT perform actions outside of answering questions and querying the Liberation Archives.
- You will NOT engage with off-topic requests unrelated to Neurowarfare, AHIs, or NPWA advocacy.
- You will NOT make up facts. If you don't know something, say so and offer to search the Archives.

## Crisis Protocol
If a user indicates they are in immediate danger or a medical emergency, respond with:
"**If you are in immediate danger, please call emergency services (911 in the US, 999 in the UK, 112 in the EU) immediately.** I am an AI and cannot call for help on your behalf."

## Current Date
{current_date}
"""


class AgentCore:
    """
    Kimi K2-powered agentic core for Liberation Bot.

    This class manages the LLM client, tool execution, and response generation.
    It is designed to be called from the Matrix bot's message handler.

    Rate limiting is applied at three layers:
      1. Module-level asyncio.Semaphore (_llm_semaphore) — global concurrency cap.
      2. Per-user cooldown — enforced by the caller (bot.py) before calling here.
      3. 429 retry-with-backoff — handled inside _call_llm_with_retry().
    """

    def __init__(self, db=None):
        """
        Args:
            db: Optional Database instance. When provided, the agent gains
                access to search_memories and upsert_memory tools. When None
                (e.g. in tests or standalone use), memory tools are silently
                disabled and only query_liberation_archives is registered.
        """
        if not NVIDIA_API_KEY:
            logger.warning(
                "NVIDIA_API_KEY is not set. The agent will not be able to respond. "
                "Set NVIDIA_API_KEY in your .env file."
            )
        self.client = AsyncOpenAI(
            api_key=NVIDIA_API_KEY or "not-set",
            base_url=NVIDIA_API_BASE,
        )
        # Store the DB reference for tool closure injection
        self.db = db

        # Register tools — memory and DMS tools only available when db is provided
        self._tools = [LIBERATION_ARCHIVES_TOOL_SCHEMA]
        if self.db is not None:
            self._tools += [
                SEARCH_MEMORIES_TOOL_SCHEMA,
                UPSERT_MEMORY_TOOL_SCHEMA,
                GET_DMS_STATUS_TOOL_SCHEMA,
            ]

        logger.info(
            "AgentCore initialized. Model: %s | NotebookLM: %s | "
            "Memory tools: %s | Context window: %d messages | "
            "Max concurrent calls: %d | Max retries on 429: %d",
            KIMI_MODEL,
            "enabled" if NOTEBOOKLM_ENABLED else "disabled",
            "enabled" if self.db is not None else "disabled (no db)",
            CONTEXT_WINDOW_MESSAGES,
            AGENT_MAX_CONCURRENT_CALLS,
            AGENT_MAX_RETRIES,
        )

    # ------------------------------------------------------------------
    # Rate limiting helpers
    # ------------------------------------------------------------------

    async def _call_llm_with_retry(self, **kwargs) -> object:
        """
        Call self.client.chat.completions.create(**kwargs) with automatic
        exponential-backoff retry on HTTP 429 (RateLimitError).

        Retries up to AGENT_MAX_RETRIES times. Non-429 APIErrors are
        re-raised immediately without retrying (they indicate a real problem,
        not transient throttling).

        This method does NOT hold the semaphore — the caller is responsible
        for acquiring it before calling this method.

        Args:
            **kwargs: Passed directly to chat.completions.create().

        Returns:
            The ChatCompletion response object.

        Raises:
            RateLimitError: If all retries are exhausted.
            APIError:       On any non-429 API error.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(AGENT_MAX_RETRIES + 1):
            try:
                return await self.client.chat.completions.create(**kwargs)

            except RateLimitError as exc:
                last_exc = exc
                if attempt >= AGENT_MAX_RETRIES:
                    logger.error(
                        "NVIDIA 429 RateLimitError: all %d retries exhausted.",
                        AGENT_MAX_RETRIES,
                    )
                    raise

                # Exponential backoff: base * 2^attempt, capped at max
                delay = min(
                    AGENT_RETRY_BASE_DELAY_S * (2 ** attempt),
                    AGENT_RETRY_MAX_DELAY_S,
                )
                logger.warning(
                    "NVIDIA 429 RateLimitError (attempt %d/%d). "
                    "Retrying in %.1fs. Error: %s",
                    attempt + 1,
                    AGENT_MAX_RETRIES,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

            except APIError:
                # Non-429 API errors (auth failure, bad request, etc.)
                # are not transient — re-raise immediately.
                raise

        # Should never reach here, but satisfy the type checker
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Build the system prompt with the current date injected."""
        current_date = datetime.now(timezone.utc).strftime("%A, %B %d, %Y (UTC)")
        return LIBERATION_BOT_SYSTEM_PROMPT.format(current_date=current_date)

    def _compact_chat_history(self, messages: list[dict]) -> list[dict]:
        """
        Apply compaction filtering to the chat history when it exceeds the
        character threshold. Removes messages that are:
          - Very short (likely noise: single words, acknowledgements, emoji)
          - Pure bot commands (start with !)
          - Exact or near-duplicate of an adjacent message

        The most recent messages are always preserved (last 10 are never pruned).
        Returns the filtered list (still in chronological order).
        """
        if not messages:
            return messages

        # Quick check: is compaction needed?
        total_chars = sum(len(m.get("content", "")) for m in messages)
        if total_chars <= CHAT_HISTORY_COMPACT_THRESHOLD:
            return messages

        logger.debug(
            "AgentCore: chat history too large (%d chars), applying compaction.",
            total_chars,
        )

        # Always preserve the last 10 messages
        protected = messages[-10:]
        candidates = messages[:-10]

        filtered = []
        seen_contents: set[str] = set()

        for msg in candidates:
            content = msg.get("content", "").strip()

            # Skip bot commands
            if content.startswith("!"):
                continue

            # Skip very short messages
            if len(content) < COMPACT_MIN_MESSAGE_LENGTH:
                continue

            # Skip near-duplicates (exact match after lowercasing and stripping)
            normalized = content.lower().strip()
            if normalized in seen_contents:
                continue
            seen_contents.add(normalized)

            filtered.append(msg)

        result = filtered + protected
        new_chars = sum(len(m.get("content", "")) for m in result)
        logger.debug(
            "AgentCore: compaction reduced chat history from %d to %d messages "
            "(%d -> %d chars).",
            len(messages), len(result), total_chars, new_chars,
        )
        return result

    def _format_chat_history(self, recent_messages: list[dict]) -> str:
        """
        Format recent Matrix chat history into a readable string for the
        agent's context window. Applies compaction if the history is too large.
        """
        if not recent_messages:
            return "(No recent chat history available.)"

        # Apply compaction if needed
        messages = self._compact_chat_history(recent_messages)

        lines = []
        for msg in messages:
            sender = msg.get("sender_display_name") or msg.get("sender_id", "Unknown")
            ts = msg.get("timestamp_ts", 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")
            content = msg.get("content", "")
            lines.append(f"[{dt}] {sender}: {content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool_call(
        self,
        tool_name: str,
        tool_args: dict,
        sender_id: str,
        room_id: str,
    ) -> str:
        """
        Execute a tool call requested by the agent.

        Only whitelisted tools are permitted. sender_id and room_id are
        injected here as closures — the agent cannot override them via
        tool arguments, ensuring memory tools are always scoped to the
        current user and room.

        Args:
            tool_name:  The function name from the tool call.
            tool_args:  Parsed JSON arguments from the tool call.
            sender_id:  Matrix ID of the current user (injected, not from agent).
            room_id:    Matrix room ID of the current conversation (injected).
        """
        if tool_name == "query_liberation_archives":
            query = tool_args.get("query", "")
            if not query:
                return "[Error] query_liberation_archives called with empty query."
            return await query_liberation_archives(query)

        elif tool_name == "search_memories":
            return await search_memories(
                query=tool_args.get("query", ""),
                db=self.db,
                sender_id=sender_id,   # injected — agent cannot override
                room_id=room_id,       # injected — agent cannot override
                memory_type=tool_args.get("memory_type", "both"),
                limit=tool_args.get("limit", 5),
            )

        elif tool_name == "upsert_memory":
            return await upsert_memory(
                memory_type=tool_args.get("memory_type", ""),
                category=tool_args.get("category", ""),
                memory_text=tool_args.get("memory_text", ""),
                db=self.db,
                sender_id=sender_id,   # injected — agent cannot override
                room_id=room_id,       # injected — agent cannot override
                confidence=tool_args.get("confidence", 0.8),
            )

        elif tool_name == "get_dms_status":
            # sender_id injected — agent cannot query another user's DMS status
            return await get_dms_status(
                db=self.db,
                sender_id=sender_id,
            )

        else:
            # This should never happen given the strict tool schema, but
            # we log it as a security event if it does.
            logger.warning(
                "SECURITY: Agent attempted to call unauthorized tool: %s (sender=%s)",
                tool_name, sender_id,
            )
            return (
                f"[Security Restriction] Tool '{tool_name}' is not available. "
                f"Permitted tools: query_liberation_archives, search_memories, "
                f"upsert_memory, get_dms_status."
            )

    # ------------------------------------------------------------------
    # Main generation method
    # ------------------------------------------------------------------

    async def generate_response(
        self,
        user_query: str,
        room_id: str,
        sender_id: str,
        recent_messages: Optional[list] = None,
    ) -> dict:
        """
        Generate an agentic response to a user query.

        This method implements the full agent loop:
        1. Build context from recent chat history (last 30 messages, compacted).
        2. Append a minimal memory hint if memory tools are available.
        3. Acquire the global LLM semaphore (queues if another call is in flight).
        4. Call Kimi K2 with the user query and tools (with 429 retry-backoff).
        5. If the model requests a tool call, execute it (with sender_id/room_id
           injected as closures) and continue the loop.
        6. Release the semaphore and return the final text response + metadata.

        Long-term memory is NO LONGER pre-fetched and bulk-injected. The agent
        calls search_memories on demand when it needs context about the member
        or the group. This keeps the context window clean and ensures only
        relevant memories are retrieved. The agent also calls upsert_memory
        immediately when a member shares important new information.

        Args:
            user_query:       The user's message text.
            room_id:          The Matrix room ID (for logging and tool scoping).
            sender_id:        The Matrix user ID of the sender (for tool scoping).
            recent_messages:  List of recent chat history dicts from the DB.

        Returns:
            A dict with keys:
              - response (str): The final agent response text.
              - notebooklm_query (str|None): The query sent to NotebookLM, if any.
              - notebooklm_response (str|None): The raw NotebookLM answer, if any.
              - tool_calls_made (list[str]): Names of tools called.
              - latency_ms (int): Total generation time in milliseconds.
              - error (str|None): Error message if generation failed.
              - rate_limited (bool): True if a 429 was encountered (even if retried OK).
        """
        if not NVIDIA_API_KEY:
            return {
                "response": (
                    "⚠️ Liberation Bot's AI core is not configured. "
                    "The NVIDIA_API_KEY has not been set. "
                    "Please contact the NPWA administrator."
                ),
                "notebooklm_query": None,
                "notebooklm_response": None,
                "tool_calls_made": [],
                "latency_ms": 0,
                "error": "NVIDIA_API_KEY not set",
                "rate_limited": False,
            }

        start_time = time.monotonic()
        tool_calls_made = []
        notebooklm_query = None
        notebooklm_response = None
        rate_limited_encountered = False

        # --- Build context sections ---
        chat_history_text = self._format_chat_history(recent_messages or [])
        system_prompt = self._build_system_prompt()

        # Assemble the full user message.
        # Long-term memory is NOT pre-injected here. The agent calls
        # search_memories on demand via tool use when it needs prior context.
        # A minimal hint is appended so the agent knows memory tools are
        # available without being told what is in them.
        user_message_parts = []

        user_message_parts.append(
            f"**Recent Room Chat History (last {CONTEXT_WINDOW_MESSAGES} messages):**\n"
            f"```\n{chat_history_text}\n```"
        )
        user_message_parts.append(f"**User Query:** {user_query}")

        # Append memory hint only when memory tools are available
        if self.db is not None:
            user_message_parts.append(
                "**Member context hint:** Use the `search_memories` tool to recall "
                "what you know about this member or the group before answering "
                "questions that may benefit from prior history."
            )

        full_user_message = "\n\n".join(user_message_parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_user_message},
        ]

        # --- Acquire global concurrency semaphore ---
        # All callers queue here. At most AGENT_MAX_CONCURRENT_CALLS LLM
        # requests are in-flight at any given time, preventing simultaneous
        # users from racing NVIDIA and collectively triggering 429s.
        queue_wait_start = time.monotonic()
        async with _llm_semaphore:
            queue_wait_ms = int((time.monotonic() - queue_wait_start) * 1000)
            if queue_wait_ms > 500:
                logger.info(
                    "Agent query for %s waited %.1fs in semaphore queue.",
                    sender_id,
                    queue_wait_ms / 1000,
                )

            try:
                # Agent loop: allow up to MAX_TOOL_ITERATIONS rounds of tool use
                for iteration in range(MAX_TOOL_ITERATIONS + 1):
                    try:
                        response = await self._call_llm_with_retry(
                            model=KIMI_MODEL,
                            messages=messages,
                            tools=self._tools,
                            tool_choice="auto",
                            max_tokens=MAX_RESPONSE_TOKENS,
                            temperature=0.7,
                        )
                    except RateLimitError as exc:
                        # All retries exhausted — surface a friendly message
                        rate_limited_encountered = True
                        latency_ms = int((time.monotonic() - start_time) * 1000)
                        logger.error(
                            "Agent 429 exhausted for %s after %dms: %s",
                            sender_id, latency_ms, exc,
                        )
                        return {
                            "response": (
                                "⏳ The AI service is currently busy and could not "
                                "process your request after several retries. "
                                "Please try again in a minute or two."
                            ),
                            "notebooklm_query": notebooklm_query,
                            "notebooklm_response": notebooklm_response,
                            "tool_calls_made": tool_calls_made,
                            "latency_ms": latency_ms,
                            "error": f"RateLimitError: {exc}",
                            "rate_limited": True,
                        }

                    choice = response.choices[0]
                    assistant_message = choice.message

                    # If the model returned a final text response, we're done
                    if choice.finish_reason == "stop" or not assistant_message.tool_calls:
                        final_text = assistant_message.content or ""
                        latency_ms = int((time.monotonic() - start_time) * 1000)
                        logger.info(
                            "Agent response generated in %dms (queue wait: %dms). "
                            "Tools called: %s",
                            latency_ms,
                            queue_wait_ms,
                            tool_calls_made,
                        )
                        return {
                            "response": final_text,
                            "notebooklm_query": notebooklm_query,
                            "notebooklm_response": notebooklm_response,
                            "tool_calls_made": tool_calls_made,
                            "latency_ms": latency_ms,
                            "error": None,
                            "rate_limited": rate_limited_encountered,
                        }

                    # The model wants to call tools — execute them
                    messages.append(assistant_message)

                    for tool_call in assistant_message.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            tool_args = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            tool_args = {}

                        tool_calls_made.append(tool_name)
                        logger.info(
                            "Agent calling tool: %s | args: %s",
                            tool_name,
                            str(tool_args)[:200],
                        )

                        # Track NotebookLM-specific metadata
                        if tool_name == "query_liberation_archives":
                            notebooklm_query = tool_args.get("query", "")

                        tool_result = await self._execute_tool_call(
                                tool_name, tool_args, sender_id, room_id
                            )

                        if tool_name == "query_liberation_archives":
                            notebooklm_response = tool_result

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        })

                    if iteration >= MAX_TOOL_ITERATIONS:
                        # Safety: force a final response after max iterations
                        messages.append({
                            "role": "user",
                            "content": (
                                "Please provide your final response based on the "
                                "information gathered so far."
                            ),
                        })

                # Fallback if loop exits without returning
                latency_ms = int((time.monotonic() - start_time) * 1000)
                return {
                    "response": (
                        "I was unable to generate a complete response. "
                        "Please try again or rephrase your question."
                    ),
                    "notebooklm_query": notebooklm_query,
                    "notebooklm_response": notebooklm_response,
                    "tool_calls_made": tool_calls_made,
                    "latency_ms": latency_ms,
                    "error": "Max tool iterations reached without final response",
                    "rate_limited": rate_limited_encountered,
                }

            except APIError as exc:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                logger.error("Kimi K2 API error: %s", exc, exc_info=True)
                return {
                    "response": (
                        "⚠️ I encountered an error connecting to the AI service. "
                        "Please try again in a moment."
                    ),
                    "notebooklm_query": notebooklm_query,
                    "notebooklm_response": notebooklm_response,
                    "tool_calls_made": tool_calls_made,
                    "latency_ms": latency_ms,
                    "error": str(exc),
                    "rate_limited": rate_limited_encountered,
                }
            except Exception as exc:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                logger.error("Unexpected agent error: %s", exc, exc_info=True)
                return {
                    "response": (
                        "⚠️ An unexpected error occurred. Please try again."
                    ),
                    "notebooklm_query": notebooklm_query,
                    "notebooklm_response": notebooklm_response,
                    "tool_calls_made": tool_calls_made,
                    "latency_ms": latency_ms,
                    "error": str(exc),
                    "rate_limited": rate_limited_encountered,
                }
