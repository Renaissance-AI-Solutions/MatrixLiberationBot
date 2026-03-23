"""
db/database.py
==============
Async SQLite database layer for Liberation Bot (Agentic).

Tables:
  - registered_users     : Dead Man's Switch registrations
  - user_profiles        : OSINT-relevant public profile data
  - emergency_vault      : AES-256-GCM encrypted emergency data
  - consensus_votes      : Group consensus vote tracking
  - audit_log            : Privacy-safe event log (no plaintext data)
  - chat_history         : [NEW] Full Matrix chat history for agent memory
  - agent_queries        : [NEW] Liberation Archives query/response log
  - video_sessions       : [NEW] Video planning session archive
  - video_style_library  : [NEW] Saved reusable visual style prompts

Security note: The agent has READ access to chat_history only.
It cannot access emergency_vault, and all agent interactions are
logged to agent_queries for auditability.
"""

import aiosqlite
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Core user registration table
CREATE TABLE IF NOT EXISTS registered_users (
    matrix_id           TEXT PRIMARY KEY,
    display_name        TEXT,
    missing_threshold_h INTEGER NOT NULL DEFAULT 72,
    last_active_ts      REAL NOT NULL,
    registration_ts     REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'ACTIVE'
                            CHECK(status IN ('ACTIVE','MISSING','ESCALATED','RELEASED')),
    osint_checked       INTEGER NOT NULL DEFAULT 0,
    osint_result_note   TEXT
);

-- Public social media handles and location for OSINT
CREATE TABLE IF NOT EXISTS user_profiles (
    matrix_id           TEXT PRIMARY KEY REFERENCES registered_users(matrix_id) ON DELETE CASCADE,
    location            TEXT,
    social_handles      TEXT  -- JSON-encoded dict: {"twitter": "@handle", "mastodon": "@h@server"}
);

-- Encrypted emergency data vault
-- The ciphertext and IV are stored; the plaintext NEVER touches disk.
CREATE TABLE IF NOT EXISTS emergency_vault (
    matrix_id           TEXT PRIMARY KEY REFERENCES registered_users(matrix_id) ON DELETE CASCADE,
    encrypted_data      BLOB NOT NULL,
    iv                  BLOB NOT NULL,
    created_ts          REAL NOT NULL,
    released_ts         REAL
);

-- Group consensus vote tracking
CREATE TABLE IF NOT EXISTS consensus_votes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    target_matrix_id    TEXT NOT NULL REFERENCES registered_users(matrix_id) ON DELETE CASCADE,
    voter_matrix_id     TEXT NOT NULL,
    voted_ts            REAL NOT NULL,
    UNIQUE(target_matrix_id, voter_matrix_id)
);

-- Audit log for all significant bot actions (privacy-safe, no plaintext data)
CREATE TABLE IF NOT EXISTS audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts            REAL NOT NULL,
    event_type          TEXT NOT NULL,
    actor_matrix_id     TEXT,
    target_matrix_id    TEXT,
    note                TEXT
);

-- [AGENTIC] Full Matrix chat history for agent context window
-- Stores all messages observed by the bot in monitored rooms.
-- The agent reads recent rows from this table for context.
-- Retention: rows older than CHAT_HISTORY_RETENTION_DAYS are purged on startup.
CREATE TABLE IF NOT EXISTS chat_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            TEXT UNIQUE,          -- Matrix event ID (dedup)
    room_id             TEXT NOT NULL,
    sender_id           TEXT NOT NULL,
    sender_display_name TEXT,
    timestamp_ts        REAL NOT NULL,        -- UTC Unix timestamp
    content             TEXT NOT NULL,        -- Plaintext message body
    indexed_at          REAL NOT NULL         -- When we stored it
);
CREATE INDEX IF NOT EXISTS idx_chat_history_room_ts
    ON chat_history(room_id, timestamp_ts DESC);

-- [AGENTIC] Liberation Archives query/response log
-- Every query sent to NotebookLM and every agent response is recorded here.
-- This forms the agent's "knowledge memory" — a separate store from chat history.
CREATE TABLE IF NOT EXISTS agent_queries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    query_ts            REAL NOT NULL,
    room_id             TEXT NOT NULL,
    user_matrix_id      TEXT NOT NULL,
    user_query          TEXT NOT NULL,
    notebooklm_query    TEXT,                 -- The refined query sent to NotebookLM (may differ)
    notebooklm_response TEXT,                 -- Raw NotebookLM answer
    agent_response      TEXT NOT NULL,        -- Final synthesized agent response
    tool_calls_made     TEXT,                 -- JSON list of tool calls made
    latency_ms          INTEGER               -- Total response time in milliseconds
);
CREATE INDEX IF NOT EXISTS idx_agent_queries_user_ts
    ON agent_queries(user_matrix_id, query_ts DESC);

-- [VIDEO] Completed video planning sessions archive
-- Stores the full record of each brainstorming session and the prompts used.
CREATE TABLE IF NOT EXISTS video_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts          REAL NOT NULL,
    completed_ts        REAL,
    room_id             TEXT NOT NULL,
    started_by          TEXT NOT NULL,
    title               TEXT,
    style_key           TEXT,                 -- Key from VIDEO_STYLE_NAMES
    custom_prompt       TEXT,                 -- Content prompt (without CTA suffix)
    full_prompt         TEXT,                 -- Full prompt sent to NotebookLM
    brainstorm_notes    TEXT,                 -- JSON array of brainstorming messages
    status              TEXT NOT NULL DEFAULT 'BRAINSTORMING'
                            CHECK(status IN ('BRAINSTORMING','CONFIRMING','IN_PROGRESS','COMPLETED','FAILED','CANCELLED')),
    notebooklm_task_id  TEXT,
    video_download_path TEXT,
    error_note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_sessions_room_ts
    ON video_sessions(room_id, created_ts DESC);

-- [VIDEO] Saved reusable visual style prompts
-- Styles that produce high-quality videos are saved here for reuse.
CREATE TABLE IF NOT EXISTS video_style_library (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,         -- Short memorable name
    style_key   TEXT NOT NULL,                -- Key from VIDEO_STYLE_NAMES
    notes       TEXT,                         -- Why this style works well
    created_by  TEXT NOT NULL,               -- Matrix user ID
    created_ts  REAL NOT NULL,
    use_count   INTEGER NOT NULL DEFAULT 0
);
"""

# How many days of chat history to retain (configurable via env)
CHAT_HISTORY_RETENTION_DAYS = 90


class Database:
    """Async SQLite database wrapper for Liberation Bot (Agentic)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Open the database connection, initialise schema, and purge old history."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        await self._purge_old_chat_history()
        logger.info("Database connected and schema initialised: %s", self.db_path)

    async def close(self):
        if self._conn:
            await self._conn.close()
            logger.info("Database connection closed.")

    async def _purge_old_chat_history(self):
        """Remove chat history older than CHAT_HISTORY_RETENTION_DAYS."""
        cutoff = datetime.now(timezone.utc).timestamp() - (
            CHAT_HISTORY_RETENTION_DAYS * 86400
        )
        await self._conn.execute(
            "DELETE FROM chat_history WHERE timestamp_ts < ?", (cutoff,)
        )
        await self._conn.commit()
        logger.debug("Purged chat history older than %d days.", CHAT_HISTORY_RETENTION_DAYS)

    # ------------------------------------------------------------------
    # Registered Users
    # ------------------------------------------------------------------

    async def register_user(
        self,
        matrix_id: str,
        display_name: str,
        missing_threshold_h: int,
    ) -> bool:
        """Insert or replace a user registration. Returns True on success."""
        now = datetime.now(timezone.utc).timestamp()
        try:
            await self._conn.execute(
                """
                INSERT INTO registered_users
                    (matrix_id, display_name, missing_threshold_h, last_active_ts, registration_ts, status)
                VALUES (?, ?, ?, ?, ?, 'ACTIVE')
                ON CONFLICT(matrix_id) DO UPDATE SET
                    display_name        = excluded.display_name,
                    missing_threshold_h = excluded.missing_threshold_h,
                    last_active_ts      = excluded.last_active_ts,
                    status              = 'ACTIVE',
                    osint_checked       = 0,
                    osint_result_note   = NULL
                """,
                (matrix_id, display_name, missing_threshold_h, now, now),
            )
            await self._conn.commit()
            return True
        except Exception as exc:
            logger.error("register_user failed for %s: %s", matrix_id, exc)
            return False

    async def get_user(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM registered_users WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_all_active_users(self) -> List[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM registered_users WHERE status IN ('ACTIVE','MISSING')"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def update_last_active(self, matrix_id: str):
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            "UPDATE registered_users SET last_active_ts = ?, status = 'ACTIVE', "
            "osint_checked = 0, osint_result_note = NULL WHERE matrix_id = ?",
            (now, matrix_id),
        )
        await self._conn.commit()

    async def set_user_status(self, matrix_id: str, status: str, note: str = None):
        await self._conn.execute(
            "UPDATE registered_users SET status = ?, osint_result_note = ? WHERE matrix_id = ?",
            (status, note, matrix_id),
        )
        await self._conn.commit()

    async def mark_osint_checked(self, matrix_id: str, note: str):
        await self._conn.execute(
            "UPDATE registered_users SET osint_checked = 1, osint_result_note = ? WHERE matrix_id = ?",
            (note, matrix_id),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # User Profiles
    # ------------------------------------------------------------------

    async def upsert_profile(self, matrix_id: str, location: str, social_handles: str):
        """social_handles should be a JSON string."""
        await self._conn.execute(
            """
            INSERT INTO user_profiles (matrix_id, location, social_handles)
            VALUES (?, ?, ?)
            ON CONFLICT(matrix_id) DO UPDATE SET
                location       = excluded.location,
                social_handles = excluded.social_handles
            """,
            (matrix_id, location, social_handles),
        )
        await self._conn.commit()

    async def get_profile(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM user_profiles WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Emergency Vault
    # ------------------------------------------------------------------

    async def store_emergency_data(
        self, matrix_id: str, encrypted_data: bytes, iv: bytes
    ):
        """Store AES-GCM encrypted emergency data. Overwrites any previous entry."""
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            """
            INSERT INTO emergency_vault (matrix_id, encrypted_data, iv, created_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(matrix_id) DO UPDATE SET
                encrypted_data = excluded.encrypted_data,
                iv             = excluded.iv,
                created_ts     = excluded.created_ts,
                released_ts    = NULL
            """,
            (matrix_id, encrypted_data, iv, now),
        )
        await self._conn.commit()

    async def get_emergency_data(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM emergency_vault WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def mark_vault_released(self, matrix_id: str):
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            "UPDATE emergency_vault SET released_ts = ? WHERE matrix_id = ?",
            (now, matrix_id),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Consensus Votes
    # ------------------------------------------------------------------

    async def add_vote(self, target_matrix_id: str, voter_matrix_id: str) -> bool:
        """Record a consensus vote. Returns True if vote was new, False if duplicate."""
        now = datetime.now(timezone.utc).timestamp()
        try:
            await self._conn.execute(
                """
                INSERT INTO consensus_votes (target_matrix_id, voter_matrix_id, voted_ts)
                VALUES (?, ?, ?)
                """,
                (target_matrix_id, voter_matrix_id, now),
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False  # Duplicate vote

    async def count_votes(self, target_matrix_id: str) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM consensus_votes WHERE target_matrix_id = ?",
            (target_matrix_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def clear_votes(self, target_matrix_id: str):
        await self._conn.execute(
            "DELETE FROM consensus_votes WHERE target_matrix_id = ?",
            (target_matrix_id,),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Audit Log
    # ------------------------------------------------------------------

    async def log_event(
        self,
        event_type: str,
        actor_matrix_id: str = None,
        target_matrix_id: str = None,
        note: str = None,
    ):
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            """
            INSERT INTO audit_log (event_ts, event_type, actor_matrix_id, target_matrix_id, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (now, event_type, actor_matrix_id, target_matrix_id, note),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # [AGENTIC] Chat History
    # ------------------------------------------------------------------

    async def save_message(
        self,
        event_id: str,
        room_id: str,
        sender_id: str,
        content: str,
        timestamp_ts: float,
        sender_display_name: str = None,
    ) -> bool:
        """
        Persist a Matrix message to chat_history.
        Returns True if saved, False if duplicate (event_id already exists).
        """
        now = datetime.now(timezone.utc).timestamp()
        try:
            await self._conn.execute(
                """
                INSERT INTO chat_history
                    (event_id, room_id, sender_id, sender_display_name, timestamp_ts, content, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, room_id, sender_id, sender_display_name, timestamp_ts, content, now),
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False  # Duplicate event_id

    async def get_recent_messages(
        self,
        room_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve the most recent `limit` messages from a room, ordered oldest-first
        so they can be fed directly into an LLM context window.
        """
        async with self._conn.execute(
            """
            SELECT sender_id, sender_display_name, timestamp_ts, content
            FROM chat_history
            WHERE room_id = ?
            ORDER BY timestamp_ts DESC
            LIMIT ?
            """,
            (room_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        # Reverse to chronological order for the LLM
        return [dict(r) for r in reversed(rows)]

    async def get_message_count(self, room_id: str) -> int:
        """Return the total number of stored messages for a room."""
        async with self._conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE room_id = ?", (room_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    # ------------------------------------------------------------------
    # [AGENTIC] Agent Queries (Liberation Archives Knowledge Base)
    # ------------------------------------------------------------------

    async def log_agent_query(
        self,
        room_id: str,
        user_matrix_id: str,
        user_query: str,
        agent_response: str,
        notebooklm_query: str = None,
        notebooklm_response: str = None,
        tool_calls_made: str = None,
        latency_ms: int = None,
    ):
        """
        Record a full agent interaction to the knowledge base log.
        `tool_calls_made` should be a JSON-serialized list of tool call names.
        """
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            """
            INSERT INTO agent_queries
                (query_ts, room_id, user_matrix_id, user_query,
                 notebooklm_query, notebooklm_response, agent_response,
                 tool_calls_made, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now, room_id, user_matrix_id, user_query,
                notebooklm_query, notebooklm_response, agent_response,
                tool_calls_made, latency_ms,
            ),
        )
        await self._conn.commit()

    async def search_agent_queries(
        self,
        search_term: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Full-text search over past agent queries and NotebookLM responses.
        Used to surface relevant past answers before querying NotebookLM again.
        """
        pattern = f"%{search_term}%"
        async with self._conn.execute(
            """
            SELECT query_ts, user_query, notebooklm_response, agent_response
            FROM agent_queries
            WHERE user_query LIKE ? OR notebooklm_response LIKE ?
            ORDER BY query_ts DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_agent_queries(
        self,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return the most recent agent query/response pairs."""
        async with self._conn.execute(
            """
            SELECT query_ts, user_matrix_id, user_query, agent_response
            FROM agent_queries
            ORDER BY query_ts DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # [VIDEO] Video Session Archive
    # ------------------------------------------------------------------

    async def create_video_session(
        self,
        room_id: str,
        started_by: str,
    ) -> int:
        """Insert a new BRAINSTORMING video session record. Returns the row ID."""
        now = datetime.now(timezone.utc).timestamp()
        async with self._conn.execute(
            """
            INSERT INTO video_sessions
                (created_ts, room_id, started_by, status)
            VALUES (?, ?, ?, 'BRAINSTORMING')
            """,
            (now, room_id, started_by),
        ) as cur:
            row_id = cur.lastrowid
        await self._conn.commit()
        return row_id

    async def update_video_session(
        self,
        session_db_id: int,
        title: str = None,
        style_key: str = None,
        custom_prompt: str = None,
        full_prompt: str = None,
        brainstorm_notes_json: str = None,
        status: str = None,
        notebooklm_task_id: str = None,
        video_download_path: str = None,
        error_note: str = None,
    ):
        """Update fields on an existing video session record."""
        now = datetime.now(timezone.utc).timestamp()
        fields = []
        values = []

        if title is not None:
            fields.append("title = ?"); values.append(title)
        if style_key is not None:
            fields.append("style_key = ?"); values.append(style_key)
        if custom_prompt is not None:
            fields.append("custom_prompt = ?"); values.append(custom_prompt)
        if full_prompt is not None:
            fields.append("full_prompt = ?"); values.append(full_prompt)
        if brainstorm_notes_json is not None:
            fields.append("brainstorm_notes = ?"); values.append(brainstorm_notes_json)
        if status is not None:
            fields.append("status = ?"); values.append(status)
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                fields.append("completed_ts = ?"); values.append(now)
        if notebooklm_task_id is not None:
            fields.append("notebooklm_task_id = ?"); values.append(notebooklm_task_id)
        if video_download_path is not None:
            fields.append("video_download_path = ?"); values.append(video_download_path)
        if error_note is not None:
            fields.append("error_note = ?"); values.append(error_note)

        if not fields:
            return

        values.append(session_db_id)
        await self._conn.execute(
            f"UPDATE video_sessions SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await self._conn.commit()

    async def get_recent_video_sessions(
        self,
        room_id: str = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return recent video sessions, optionally filtered by room."""
        if room_id:
            sql = """
                SELECT id, created_ts, completed_ts, room_id, started_by,
                       title, style_key, status, video_download_path
                FROM video_sessions
                WHERE room_id = ?
                ORDER BY created_ts DESC LIMIT ?
            """
            params = (room_id, limit)
        else:
            sql = """
                SELECT id, created_ts, completed_ts, room_id, started_by,
                       title, style_key, status, video_download_path
                FROM video_sessions
                ORDER BY created_ts DESC LIMIT ?
            """
            params = (limit,)
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # [VIDEO] Style Library
    # ------------------------------------------------------------------

    async def save_style(
        self,
        name: str,
        style_key: str,
        created_by: str,
        notes: str = None,
    ) -> bool:
        """Save a named style to the library. Returns True on success."""
        now = datetime.now(timezone.utc).timestamp()
        try:
            await self._conn.execute(
                """
                INSERT INTO video_style_library
                    (name, style_key, notes, created_by, created_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    style_key  = excluded.style_key,
                    notes      = excluded.notes,
                    created_by = excluded.created_by,
                    created_ts = excluded.created_ts
                """,
                (name, style_key, notes, created_by, now),
            )
            await self._conn.commit()
            return True
        except Exception as exc:
            logger.error("save_style failed for %s: %s", name, exc)
            return False

    async def get_style(self, name: str) -> Optional[Dict[str, Any]]:
        """Look up a saved style by name."""
        async with self._conn.execute(
            "SELECT * FROM video_style_library WHERE name = ?", (name,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_styles(self) -> List[Dict[str, Any]]:
        """Return all saved styles ordered by use count descending."""
        async with self._conn.execute(
            "SELECT * FROM video_style_library ORDER BY use_count DESC, name ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def increment_style_use_count(self, name: str):
        """Increment the use counter for a saved style."""
        await self._conn.execute(
            "UPDATE video_style_library SET use_count = use_count + 1 WHERE name = ?",
            (name,),
        )
        await self._conn.commit()
