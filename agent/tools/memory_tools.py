"""
agent/tools/memory_tools.py
============================
Liberation Bot — Active Memory Tools

Provides two async tool functions that the Kimi K2 agent can call during
a conversation to interact with the long-term memory stores:

  search_memories  — Retrieve relevant memories on demand (replaces passive
                     bulk injection).  The agent calls this proactively when
                     a member's question may relate to their history or when
                     the group asks about ongoing strategy/intelligence.

  upsert_memory    — Write a new memory immediately, bypassing the nightly
                     Dream consolidation cycle.  The agent calls this when a
                     member explicitly shares new important information about
                     themselves or the group.

Security invariants (enforced in code, not just documentation):
  - sender_id and room_id are injected as closures — the agent cannot pass
    them as arguments and therefore cannot read or write another user's data.
  - search_memories only queries rows where is_deleted = 0.
  - upsert_memory validates category/topic against the allowed enum before
    writing; invalid values return an error string rather than silently
    writing with a bad category.
  - Confidence is clamped to [0.3, 1.0] — never rejected.
  - No access to emergency_vault, consensus_votes, or registered_users.

Usage pattern (inside AgentCore._execute_tool_call):
    result = await search_memories(
        query="legal status",
        memory_type="user",
        db=self.db,
        sender_id=sender_id,
        room_id=room_id,
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from db.database import Database

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed category / topic enums (must stay in sync with dreamer.py and db)
# ---------------------------------------------------------------------------

VALID_USER_CATEGORIES = {
    "symptoms",
    "legal_status",
    "personal_history",
    "preferences",
    "triggers",
    "relationships",
    "notes",
}

VALID_OPERATIONAL_TOPICS = {
    "neurowarfare_programs",
    "countermeasures",
    "legal_strategy",
    "operational_planning",
    "threat_actors",
    "resources",
    "brainstorming",
}

# Maximum results the agent may request (hard cap regardless of schema value)
_MAX_LIMIT = 10

# ---------------------------------------------------------------------------
# Helper: format a timestamp as a human-readable date string
# ---------------------------------------------------------------------------

def _fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return "unknown date"
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "unknown date"


# ---------------------------------------------------------------------------
# Tool: search_memories
# ---------------------------------------------------------------------------

async def search_memories(
    query: str,
    db: "Database",
    sender_id: str,
    room_id: str,
    memory_type: str = "both",
    limit: int = 5,
) -> str:
    """
    Search long-term memories for the current member and/or the group.

    The agent calls this to recall specific facts about the member's history,
    symptoms, legal situation, or the group's documented intelligence and
    strategy.  sender_id and room_id are injected by the tool executor and
    are NOT exposed in the tool schema — the agent cannot search another
    user's memories.

    Args:
        query:       Keywords or phrase to search for (SQLite LIKE matching).
        db:          Database instance (injected, not from agent args).
        sender_id:   Matrix ID of the current user (injected, not from agent args).
        room_id:     Matrix room ID of the current conversation (injected).
        memory_type: "user", "operational", or "both".
        limit:       Max results to return (1–10, hard-capped at 10).

    Returns:
        A formatted plain-text string of matching memories, or a message
        indicating no relevant memories were found.
    """
    if not db:
        return "[Memory Search Unavailable] Memory tools are not configured."

    # Normalise and validate inputs
    memory_type = (memory_type or "both").lower().strip()
    if memory_type not in ("user", "operational", "both"):
        memory_type = "both"

    limit = max(1, min(int(limit), _MAX_LIMIT))
    like_pattern = f"%{query.strip()}%"

    results: list[str] = []

    # ------------------------------------------------------------------
    # User memories — scoped strictly to sender_id
    # ------------------------------------------------------------------
    if memory_type in ("user", "both"):
        try:
            async with db._conn.execute(
                """
                SELECT category, memory_text, confidence, updated_ts
                FROM user_memories
                WHERE matrix_id = ?
                  AND is_deleted = 0
                  AND memory_text LIKE ?
                GROUP BY category
                HAVING version = MAX(version)
                ORDER BY updated_ts DESC
                LIMIT ?
                """,
                (sender_id, like_pattern, limit),
            ) as cur:
                rows = await cur.fetchall()

            if rows:
                results.append("**Your personal memories:**")
                for row in rows:
                    results.append(
                        f"  [{row['category']}] (confidence: {row['confidence']:.2f}, "
                        f"updated: {_fmt_ts(row['updated_ts'])})\n"
                        f"  {row['memory_text']}"
                    )
        except Exception as exc:
            logger.error("search_memories (user) failed for %s: %s", sender_id, exc)
            results.append("[Error searching personal memories — please try again]")

    # ------------------------------------------------------------------
    # Operational memories — scoped to room_id or org-wide (room_id IS NULL)
    # ------------------------------------------------------------------
    if memory_type in ("operational", "both"):
        try:
            async with db._conn.execute(
                """
                SELECT topic, memory_text, confidence, updated_ts
                FROM operational_memories
                WHERE (room_id = ? OR room_id IS NULL)
                  AND is_deleted = 0
                  AND memory_text LIKE ?
                GROUP BY topic
                HAVING version = MAX(version)
                ORDER BY updated_ts DESC
                LIMIT ?
                """,
                (room_id, like_pattern, limit),
            ) as cur:
                rows = await cur.fetchall()

            if rows:
                results.append("**Group operational memories:**")
                for row in rows:
                    results.append(
                        f"  [{row['topic']}] (confidence: {row['confidence']:.2f}, "
                        f"updated: {_fmt_ts(row['updated_ts'])})\n"
                        f"  {row['memory_text']}"
                    )
        except Exception as exc:
            logger.error("search_memories (operational) failed for room %s: %s", room_id, exc)
            results.append("[Error searching group memories — please try again]")

    if not results:
        return (
            f"No memories found matching '{query}'. "
            "This member or group may not have any relevant long-term memories yet, "
            "or the search terms may need to be broader."
        )

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Tool: upsert_memory
# ---------------------------------------------------------------------------

async def upsert_memory(
    memory_type: str,
    category: str,
    memory_text: str,
    db: "Database",
    sender_id: str,
    room_id: str,
    confidence: float = 0.8,
) -> str:
    """
    Save an important new fact to long-term memory immediately.

    The agent calls this when a member explicitly shares new important
    information about themselves (symptoms, legal situation, personal history)
    or when the group discusses something that should be remembered
    organizationally.  sender_id and room_id are injected by the tool
    executor — the agent cannot write memories for other users.

    Args:
        memory_type:  "user" (personal to this member) or "operational" (group-wide).
        category:     For user: one of VALID_USER_CATEGORIES.
                      For operational: one of VALID_OPERATIONAL_TOPICS.
        memory_text:  The memory in plain English, written as a factual statement.
        db:           Database instance (injected, not from agent args).
        sender_id:    Matrix ID of the current user (injected).
        room_id:      Matrix room ID of the current conversation (injected).
        confidence:   Agent's confidence score 0.0–1.0 (clamped to [0.3, 1.0]).

    Returns:
        A short confirmation string on success, or an error message on failure.
    """
    if not db:
        return "[Memory Write Unavailable] Memory tools are not configured."

    # Normalise inputs
    memory_type = (memory_type or "").lower().strip()
    category = (category or "").lower().strip()
    memory_text = (memory_text or "").strip()

    # Clamp confidence — never reject, just clamp
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.8
    confidence = max(0.3, min(1.0, confidence))

    # Validate memory_text
    if not memory_text:
        return "[Memory Write Error] memory_text cannot be empty."

    if len(memory_text) > 3000:
        return (
            "[Memory Write Error] memory_text is too long "
            f"({len(memory_text)} chars). Please keep it under 3000 characters."
        )

    # ------------------------------------------------------------------
    # User memory write — strictly scoped to sender_id
    # ------------------------------------------------------------------
    if memory_type == "user":
        if category not in VALID_USER_CATEGORIES:
            valid = ", ".join(sorted(VALID_USER_CATEGORIES))
            return (
                f"[Memory Write Error] Invalid category '{category}' for user memory. "
                f"Valid categories are: {valid}"
            )
        try:
            row_id = await db.upsert_user_memory(
                matrix_id=sender_id,
                category=category,
                memory_text=memory_text,
                confidence=confidence,
                source_event_ids="[]",   # agent-written, not from Dream batch
                is_user_edited=False,
            )
            logger.info(
                "Agent wrote user memory for %s [%s] (id=%d, conf=%.2f)",
                sender_id, category, row_id, confidence,
            )
            return (
                f"Memory saved. [{category}] stored for this member "
                f"(confidence: {confidence:.2f})."
            )
        except Exception as exc:
            logger.error(
                "upsert_memory (user) failed for %s / %s: %s", sender_id, category, exc
            )
            return f"[Memory Write Error] Failed to save memory: {exc}"

    # ------------------------------------------------------------------
    # Operational memory write — scoped to room_id
    # ------------------------------------------------------------------
    elif memory_type == "operational":
        if category not in VALID_OPERATIONAL_TOPICS:
            valid = ", ".join(sorted(VALID_OPERATIONAL_TOPICS))
            return (
                f"[Memory Write Error] Invalid topic '{category}' for operational memory. "
                f"Valid topics are: {valid}"
            )
        try:
            row_id = await db.upsert_operational_memory(
                topic=category,
                memory_text=memory_text,
                room_id=room_id,
                confidence=confidence,
                source_event_ids="[]",
            )
            logger.info(
                "Agent wrote operational memory [%s] for room %s (id=%d, conf=%.2f)",
                category, room_id, row_id, confidence,
            )
            return (
                f"Memory saved. [{category}] stored in group operational memory "
                f"(confidence: {confidence:.2f})."
            )
        except Exception as exc:
            logger.error(
                "upsert_memory (operational) failed for %s / %s: %s", room_id, category, exc
            )
            return f"[Memory Write Error] Failed to save memory: {exc}"

    else:
        return (
            f"[Memory Write Error] Invalid memory_type '{memory_type}'. "
            "Must be 'user' or 'operational'."
        )


# ---------------------------------------------------------------------------
# OpenAI Tool Schemas
# ---------------------------------------------------------------------------

SEARCH_MEMORIES_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_memories",
        "description": (
            "Search long-term memories about this member or the group's operational "
            "context. Use this to recall specific facts about the member's history, "
            "symptoms, legal situation, or the group's documented intelligence and "
            "strategy. Call this proactively when a member's question may relate to "
            "their history, or when the group asks about ongoing strategy or intelligence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keywords or a phrase to search for in long-term memory. "
                        "Examples: 'legal status', 'Havana Syndrome symptoms', "
                        "'threat actor', 'legal strategy', 'tinnitus'."
                    ),
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["user", "operational", "both"],
                    "description": (
                        "Which memory store to search. "
                        "'user' = this member's personal memories only. "
                        "'operational' = group-wide intelligence and strategy only. "
                        "'both' = search both (default)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (1–10). Default 5.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
}

UPSERT_MEMORY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "upsert_memory",
        "description": (
            "Save an important new fact to long-term memory immediately, without "
            "waiting for the nightly Dream consolidation cycle. Use this when the "
            "member explicitly tells you something significant about themselves "
            "(symptoms, legal situation, personal history) or when the group discusses "
            "something that should be remembered organizationally. "
            "Do NOT use this for conversational filler — only for genuinely important "
            "new information that should persist across sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "enum": ["user", "operational"],
                    "description": (
                        "'user' = personal to this member (stored under their Matrix ID). "
                        "'operational' = group-level intelligence or planning "
                        "(stored under the current room)."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "For user memories, one of: symptoms, legal_status, "
                        "personal_history, preferences, triggers, relationships, notes. "
                        "For operational memories, one of: neurowarfare_programs, "
                        "countermeasures, legal_strategy, operational_planning, "
                        "threat_actors, resources, brainstorming."
                    ),
                },
                "memory_text": {
                    "type": "string",
                    "description": (
                        "The memory to store, written in plain English as a factual "
                        "statement (not a quote). Be concise but complete. "
                        "Example: 'Member reports persistent high-pitched tinnitus "
                        "in the left ear since the incident in March 2024.'"
                    ),
                },
                "confidence": {
                    "type": "number",
                    "description": (
                        "Your confidence that this information is accurate and important "
                        "enough to store (0.0–1.0). Default 0.8. Use lower values for "
                        "uncertain or inferred information."
                    ),
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            "required": ["memory_type", "category", "memory_text"],
        },
    },
}
