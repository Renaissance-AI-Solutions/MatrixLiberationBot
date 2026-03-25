"""
agent/dreamer.py
================
Liberation Bot Dream Engine — Nightly Memory Consolidation

Inspired by Claude Code's "Auto-dream" feature and the UC Berkeley
"Sleep-time Compute" paper (arXiv:2504.13171), this module mimics
human REM sleep memory consolidation:

  1. Accumulate: raw chat history is stored in chat_history (90-day window)
  2. Trigger:    a cron job fires daily at 03:00 UTC (low-activity window)
  3. Extract:    an LLM sub-agent reviews transcripts for relevant information
  4. Categorize: extracted memories are routed to user_memories or
                 operational_memories tables
  5. Merge:      new insights are synthesized with existing memories,
                 resolving contradictions and incrementing version numbers
  6. Log:        every cycle is recorded in dream_cycles for auditability

Security boundaries (HARD — never bypass):
  - The Dream Engine NEVER reads emergency_vault data
  - OTP challenge messages are stripped before LLM processing
  - Bot commands (lines starting with !) are excluded from transcripts
  - Minimum message threshold prevents wasteful API calls on quiet days
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# LLM endpoint — reuse the same NVIDIA NIM / Kimi K2 setup as AgentCore
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_API_BASE = os.getenv(
    "NVIDIA_API_BASE", "https://integrate.api.nvidia.com/v1"
)

# Model for extraction pass (use a faster/cheaper model if available)
DREAM_EXTRACTION_MODEL = os.getenv("DREAM_EXTRACTION_MODEL", "moonshotai/kimi-k2-instruct")
# Model for merge/synthesis pass (same or richer model)
DREAM_MERGE_MODEL = os.getenv("DREAM_MERGE_MODEL", "moonshotai/kimi-k2-instruct")

# Minimum number of new messages required to trigger a Dream cycle
DREAM_MIN_MESSAGES = int(os.getenv("DREAM_MIN_MESSAGES", "10"))

# Maximum messages to process in a single batch (prevents oversized API calls)
DREAM_BATCH_SIZE = int(os.getenv("DREAM_BATCH_SIZE", "500"))

# Stale lock threshold: if a RUNNING cycle is older than this many seconds,
# treat it as crashed and allow a new cycle to start
DREAM_LOCK_STALE_SECONDS = int(os.getenv("DREAM_LOCK_STALE_SECONDS", "7200"))  # 2 hours

# Regex patterns for pre-processing filters
_BOT_COMMAND_RE = re.compile(r"^\s*!", re.MULTILINE)
_OTP_RE = re.compile(r"\b\d{6,8}\b")  # Strip 6-8 digit OTP codes

# Valid memory categories and topics (used for validation)
VALID_USER_CATEGORIES = {
    "symptoms", "legal_status", "personal_history",
    "preferences", "triggers", "relationships", "notes",
}
VALID_OP_TOPICS = {
    "neurowarfare_programs", "countermeasures", "legal_strategy",
    "operational_planning", "threat_actors", "resources", "brainstorming",
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are the Liberation Bot Memory Consolidation Agent for the NeuroPsychological Warfare Alliance (NPWA). Your sole purpose during this session is to review raw Matrix chat transcripts and extract information that should be preserved in long-term memory.

## Mission Context
The NPWA supports victims of neurowarfare, Havana Syndrome, and Anomalous Health Incidents (AHIs). Our members are activists, researchers, and survivors. Long-term memory is critical for:
1. Remembering individual members' situations, symptoms, and needs across sessions
2. Tracking organizational strategies and activism planning
3. Documenting neurowarfare programs, threat actors, and countermeasures

## Extraction Rules

**IGNORE (do not extract):**
- Greetings, farewells, casual small talk
- Bot commands (messages starting with !)
- Transient status updates ("I'll be back in 5 minutes")
- Questions that were already answered and have no lasting relevance
- Redundant information already captured in a previous message in this transcript

**EXTRACT for USER MEMORY** (information about a specific person):
- Personal health disclosures: symptoms, medical history, diagnoses related to AHIs/neurowarfare
- Legal situations: ongoing cases, attorneys, jurisdictions, legal strategies being pursued
- Personal history: background relevant to their activism or victimization
- Communication preferences: how they prefer to receive information, their expertise level
- Trauma triggers: topics or situations that cause distress (handle with care)
- Relationships: key contacts, allies, adversaries mentioned by or about this person
- General notes: anything else personally relevant that doesn't fit above categories

**EXTRACT for OPERATIONAL MEMORY** (information about the group's work):
- Neurowarfare program documentation: named programs, agencies, technologies, incidents
- Countermeasures: strategies, tools, or techniques for protection or mitigation
- Legal strategy: group-level legal approaches, precedents, contacts, filings
- Operational planning: activism plans, campaigns, events, timelines
- Threat actors: identified individuals, organizations, or agencies acting against the group
- Resources: useful documents, contacts, websites, tools discovered by the group
- Brainstorming: significant ideas or proposals discussed that merit follow-up

**NEVER EXTRACT:**
- Emergency vault contents, passwords, authentication codes
- Anything explicitly marked as private by the sender
- Personal identifying information beyond what is necessary for context

## Output Format
Return ONLY valid JSON. No preamble, no explanation, no markdown fencing.

{
  "user_memories": [
    {
      "matrix_id": "@user:homeserver.tld",
      "category": "symptoms|legal_status|personal_history|preferences|triggers|relationships|notes",
      "memory_text": "Concise third-person summary. Prefer brevity; use more words only when complexity demands it (max 500 words).",
      "confidence": 0.0,
      "source_event_ids": ["$eventid1", "$eventid2"]
    }
  ],
  "operational_memories": [
    {
      "room_id": "!roomid:homeserver.tld or null for org-wide",
      "topic": "neurowarfare_programs|countermeasures|legal_strategy|operational_planning|threat_actors|resources|brainstorming",
      "memory_text": "Concise summary. Prefer brevity; use more words only when complexity demands it (max 500 words).",
      "confidence": 0.0,
      "source_event_ids": ["$eventid1"]
    }
  ]
}

If there is nothing worth extracting, return: {"user_memories": [], "operational_memories": []}
"""

MERGE_SYSTEM_PROMPT = """You are updating a long-term memory record for the NeuroPsychological Warfare Alliance's Liberation Bot. Your task is to synthesize an existing memory with new information from a recent conversation.

## Instructions
- If the new information **contradicts** the existing memory, prefer the newer information and note the change
- If the new information **adds to** the existing memory, merge them into a single coherent summary
- If the new information is **redundant** (already captured), return the existing memory text unchanged
- Write in **third person** ("The member...", "This user...", "The group...")
- Replace any **relative date references** ("recently", "yesterday", "last week") with the actual date provided
- **Prefer concise summaries.** Use the minimum words needed to capture the full meaning. For simple memories, aim for 1-3 sentences. For complex situations with multiple facets, you may use up to 500 words — but never pad unnecessarily.
- Do **not** include meta-commentary about what you changed or why

## Output
Return ONLY the updated memory text. No JSON, no explanation, no markdown.
"""

# ---------------------------------------------------------------------------
# DreamEngine
# ---------------------------------------------------------------------------


class DreamEngine:
    """
    Liberation Bot Dream Engine.

    Runs nightly at 03:00 UTC to consolidate raw chat history into
    structured long-term memory stores (user_memories and
    operational_memories).

    Usage (called from the APScheduler in bot.py):
        dream_engine = DreamEngine(db)
        await dream_engine.run_dream_cycle()
    """

    def __init__(self, db):
        """
        Args:
            db: An initialized Database instance (from db/database.py).
        """
        self.db = db
        self.client = AsyncOpenAI(
            api_key=NVIDIA_API_KEY or "not-set",
            base_url=NVIDIA_API_BASE,
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run_dream_cycle(self) -> None:
        """
        Main entry point for the Dream cycle. Orchestrates the full
        consolidation pipeline. Safe to call from a scheduler — handles
        its own locking, gating, and error recovery.
        """
        logger.info("=== Dream Engine: cycle starting ===")

        # --- Gate 1: Check for stale or active lock ---
        if not await self._acquire_lock():
            logger.info("Dream Engine: cycle skipped (lock active or stale).")
            return

        cycle_id = await self.db.create_dream_cycle()
        stats = {
            "messages_processed": 0,
            "user_memories_created": 0,
            "user_memories_updated": 0,
            "op_memories_created": 0,
            "op_memories_updated": 0,
        }

        try:
            # --- Gate 2: Determine since_ts from last successful cycle ---
            last_cycle = await self.db.get_last_successful_dream()
            since_ts = last_cycle["run_ts"] if last_cycle else 0.0
            since_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            logger.info(
                "Dream Engine: processing messages since %s", since_dt
            )

            # --- Gate 3: Fetch unprocessed messages ---
            messages = await self.db.get_messages_since(since_ts, limit=DREAM_BATCH_SIZE)
            stats["messages_processed"] = len(messages)

            if len(messages) < DREAM_MIN_MESSAGES:
                logger.info(
                    "Dream Engine: only %d new messages (min %d). Skipping.",
                    len(messages), DREAM_MIN_MESSAGES,
                )
                await self.db.complete_dream_cycle(cycle_id, status="SKIPPED")
                return

            logger.info("Dream Engine: %d messages to process.", len(messages))

            # --- Step 1: Pre-process and format transcript ---
            transcript, event_map = self._build_transcript(messages)

            # --- Step 2: Extract memories via LLM ---
            extracted = await self._extract_memories(transcript, since_dt)

            if not extracted:
                logger.warning("Dream Engine: extraction returned no data.")
                await self.db.complete_dream_cycle(
                    cycle_id, status="SUCCESS", **stats
                )
                return

            # --- Step 3: Merge user memories ---
            for um in extracted.get("user_memories", []):
                if not self._validate_user_memory(um):
                    continue
                created, updated = await self._merge_user_memory(um)
                stats["user_memories_created"] += created
                stats["user_memories_updated"] += updated

            # --- Step 4: Merge operational memories ---
            for om in extracted.get("operational_memories", []):
                if not self._validate_op_memory(om):
                    continue
                created, updated = await self._merge_operational_memory(om)
                stats["op_memories_created"] += created
                stats["op_memories_updated"] += updated

            # --- Step 5: Complete cycle ---
            await self.db.complete_dream_cycle(
                cycle_id, status="SUCCESS", **stats
            )
            logger.info(
                "=== Dream Engine: cycle complete. "
                "Processed %d messages. "
                "User memories: +%d created, ~%d updated. "
                "Op memories: +%d created, ~%d updated. ===",
                stats["messages_processed"],
                stats["user_memories_created"],
                stats["user_memories_updated"],
                stats["op_memories_created"],
                stats["op_memories_updated"],
            )

        except Exception as exc:
            logger.error("Dream Engine: cycle FAILED: %s", exc, exc_info=True)
            await self.db.complete_dream_cycle(
                cycle_id,
                status="FAILED",
                error_note=str(exc)[:500],
                **stats,
            )

    # ------------------------------------------------------------------
    # Lock management
    # ------------------------------------------------------------------

    async def _acquire_lock(self) -> bool:
        """
        Check for an active Dream cycle lock.
        Returns True if we can proceed, False if another cycle is running.
        Stale locks (older than DREAM_LOCK_STALE_SECONDS) are ignored.
        """
        running = await self.db.get_running_dream_cycle()
        if running:
            age = time.time() - running["run_ts"]
            if age < DREAM_LOCK_STALE_SECONDS:
                logger.warning(
                    "Dream Engine: active lock found (cycle #%d, age %.0fs). Skipping.",
                    running["id"], age,
                )
                return False
            else:
                logger.warning(
                    "Dream Engine: stale lock found (cycle #%d, age %.0fs). Overriding.",
                    running["id"], age,
                )
                # Mark the stale cycle as failed
                await self.db.complete_dream_cycle(
                    running["id"],
                    status="FAILED",
                    error_note="Stale lock — overridden by new cycle.",
                )
        return True

    # ------------------------------------------------------------------
    # Transcript building
    # ------------------------------------------------------------------

    def _build_transcript(
        self, messages: list[dict]
    ) -> tuple[str, dict[str, str]]:
        """
        Format raw chat_history rows into a readable transcript string
        for the LLM, applying security pre-processing filters.

        Returns:
            transcript: Formatted string with [TIMESTAMP] SENDER: content lines
            event_map:  Dict mapping display lines to event_ids (for source tracking)
        """
        lines = []
        event_map: dict[str, str] = {}

        for msg in messages:
            content = msg.get("content", "").strip()
            if not content:
                continue

            # Filter 1: Skip bot commands
            if content.startswith("!"):
                continue

            # Filter 2: Strip OTP codes from content (replace with [OTP_REDACTED])
            content = _OTP_RE.sub("[OTP_REDACTED]", content)

            # Filter 3: Skip very short/noise messages (single emoji, "ok", "lol", etc.)
            if len(content) < 4:
                continue

            sender = msg.get("sender_display_name") or msg.get("sender_id", "Unknown")
            matrix_id = msg.get("sender_id", "")
            ts = msg.get("timestamp_ts", 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            event_id = msg.get("event_id", "")
            room_id = msg.get("room_id", "")

            line = f"[{dt}] [{room_id}] {sender} ({matrix_id}): {content}"
            lines.append(line)
            if event_id:
                event_map[line[:80]] = event_id  # Map first 80 chars for source tracking

        transcript = "\n".join(lines)
        return transcript, event_map

    # ------------------------------------------------------------------
    # LLM extraction pass
    # ------------------------------------------------------------------

    async def _extract_memories(
        self, transcript: str, since_label: str
    ) -> Optional[dict]:
        """
        Send the transcript to the LLM with the extraction prompt.
        Returns parsed JSON dict or None on failure.
        """
        if not transcript.strip():
            return {"user_memories": [], "operational_memories": []}

        user_prompt = (
            f"TRANSCRIPT DATE RANGE: {since_label} to "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"TRANSCRIPT:\n{transcript}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=DREAM_EXTRACTION_MODEL,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4096,
                temperature=0.2,  # Low temperature for consistent extraction
            )
            raw = response.choices[0].message.content or ""
            # Strip markdown code fences if the model added them
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
            raw = re.sub(r"\s*```$", "", raw.strip())
            parsed = json.loads(raw)
            logger.info(
                "Dream Engine: extracted %d user memories, %d operational memories.",
                len(parsed.get("user_memories", [])),
                len(parsed.get("operational_memories", [])),
            )
            return parsed
        except json.JSONDecodeError as exc:
            logger.error(
                "Dream Engine: extraction JSON parse failed: %s | raw: %s",
                exc, raw[:500],
            )
            return None
        except Exception as exc:
            logger.error("Dream Engine: extraction LLM call failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Merge passes
    # ------------------------------------------------------------------

    async def _merge_user_memory(self, um: dict) -> tuple[int, int]:
        """
        Merge a newly extracted user memory with any existing memory
        for the same (matrix_id, category). Returns (created, updated) counts.
        """
        matrix_id = um["matrix_id"]
        category = um["category"]
        new_text = um["memory_text"]
        confidence = float(um.get("confidence", 1.0))
        source_ids = json.dumps(um.get("source_event_ids", []))

        existing = await self.db.get_user_memory_by_category(matrix_id, category)

        if not existing:
            # New memory — insert directly
            await self.db.upsert_user_memory(
                matrix_id=matrix_id,
                category=category,
                memory_text=new_text,
                confidence=confidence,
                source_event_ids=source_ids,
            )
            logger.debug(
                "Dream Engine: created new user memory [%s / %s]",
                matrix_id, category,
            )
            return 1, 0
        else:
            # Existing memory — run merge pass
            merged_text = await self._run_merge_pass(
                existing_text=existing["memory_text"],
                new_text=new_text,
                existing_version=existing["version"],
                context_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            )
            if merged_text is None:
                # Merge failed — skip to avoid data loss
                return 0, 0

            # Only write if the text actually changed
            if merged_text.strip() == existing["memory_text"].strip():
                logger.debug(
                    "Dream Engine: user memory [%s / %s] unchanged after merge.",
                    matrix_id, category,
                )
                return 0, 0

            await self.db.upsert_user_memory(
                matrix_id=matrix_id,
                category=category,
                memory_text=merged_text,
                confidence=confidence,
                source_event_ids=source_ids,
            )
            logger.debug(
                "Dream Engine: updated user memory [%s / %s] to v%d",
                matrix_id, category, existing["version"] + 1,
            )
            return 0, 1

    async def _merge_operational_memory(self, om: dict) -> tuple[int, int]:
        """
        Merge a newly extracted operational memory with any existing memory
        for the same topic (and optionally room_id). Returns (created, updated).
        """
        topic = om["topic"]
        room_id = om.get("room_id")  # May be None for org-wide memories
        new_text = om["memory_text"]
        confidence = float(om.get("confidence", 1.0))
        source_ids = json.dumps(om.get("source_event_ids", []))

        existing = await self.db.get_operational_memory_by_topic(topic, room_id)

        if not existing:
            await self.db.upsert_operational_memory(
                topic=topic,
                memory_text=new_text,
                room_id=room_id,
                confidence=confidence,
                source_event_ids=source_ids,
            )
            logger.debug("Dream Engine: created new operational memory [%s]", topic)
            return 1, 0
        else:
            merged_text = await self._run_merge_pass(
                existing_text=existing["memory_text"],
                new_text=new_text,
                existing_version=existing["version"],
                context_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            )
            if merged_text is None:
                return 0, 0

            if merged_text.strip() == existing["memory_text"].strip():
                logger.debug(
                    "Dream Engine: operational memory [%s] unchanged after merge.", topic
                )
                return 0, 0

            await self.db.upsert_operational_memory(
                topic=topic,
                memory_text=merged_text,
                room_id=room_id,
                confidence=confidence,
                source_event_ids=source_ids,
            )
            logger.debug(
                "Dream Engine: updated operational memory [%s] to v%d",
                topic, existing["version"] + 1,
            )
            return 0, 1

    async def _run_merge_pass(
        self,
        existing_text: str,
        new_text: str,
        existing_version: int,
        context_date: str,
    ) -> Optional[str]:
        """
        Call the LLM to synthesize existing and new memory text.
        Returns the merged text string, or None on failure.
        """
        user_prompt = (
            f"EXISTING MEMORY (version {existing_version}):\n{existing_text}\n\n"
            f"NEW INFORMATION (from {context_date}):\n{new_text}"
        )
        try:
            response = await self.client.chat.completions.create(
                model=DREAM_MERGE_MODEL,
                messages=[
                    {"role": "system", "content": MERGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1024,
                temperature=0.1,
            )
            merged = (response.choices[0].message.content or "").strip()
            return merged if merged else existing_text
        except Exception as exc:
            logger.error("Dream Engine: merge LLM call failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_user_memory(self, um: dict) -> bool:
        """Validate a user memory dict from the extraction output."""
        if not um.get("matrix_id") or not um.get("category") or not um.get("memory_text"):
            logger.warning("Dream Engine: skipping invalid user memory: %s", um)
            return False
        if um["category"] not in VALID_USER_CATEGORIES:
            logger.warning(
                "Dream Engine: unknown user memory category '%s'. Mapping to 'notes'.",
                um["category"],
            )
            um["category"] = "notes"
        confidence = float(um.get("confidence", 1.0))
        if confidence < 0.3:
            logger.debug(
                "Dream Engine: skipping low-confidence user memory (%.2f): %s",
                confidence, um.get("matrix_id"),
            )
            return False
        return True

    def _validate_op_memory(self, om: dict) -> bool:
        """Validate an operational memory dict from the extraction output."""
        if not om.get("topic") or not om.get("memory_text"):
            logger.warning("Dream Engine: skipping invalid operational memory: %s", om)
            return False
        if om["topic"] not in VALID_OP_TOPICS:
            logger.warning(
                "Dream Engine: unknown operational memory topic '%s'. Mapping to 'brainstorming'.",
                om["topic"],
            )
            om["topic"] = "brainstorming"
        confidence = float(om.get("confidence", 1.0))
        if confidence < 0.3:
            logger.debug(
                "Dream Engine: skipping low-confidence operational memory (%.2f): %s",
                confidence, om.get("topic"),
            )
            return False
        return True
