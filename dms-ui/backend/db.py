"""
dms-ui/backend/db.py
====================
Async SQLite layer for the DMS Web UI.

This module opens the SAME database file as Liberation Bot (read/write).

=============================================================================
TABLE OWNERSHIP BOUNDARY — READ THIS BEFORE ADDING NEW QUERIES
=============================================================================

OWNED BY THE BOT (db/database.py — Database class):
  - registered_users     : Bot is the authoritative writer.
                           UI reads via get_registered_user().
                           UI writes via update_last_active() and update_threshold()
                           which are now DELEGATED to the Database class.
  - user_profiles        : Bot is the authoritative writer.
                           UI reads via get_bot_profile().
                           UI writes via upsert_bot_profile() which is now
                           DELEGATED to the Database class.
  - emergency_vault      : Bot is the ONLY writer. UI reads metadata only
                           (created_ts, released_ts) via get_vault_meta().
                           The encrypted blob is NEVER returned to the UI.
  - audit_log            : Bot is the authoritative writer.
                           UI reads via get_audit_log().
                           UI writes via log_event() which is now DELEGATED
                           to the Database class.
  - chat_history         : Bot only. UI never reads or writes this table.
  - agent_queries        : Bot only. UI never reads or writes this table.
  - user_memories        : Bot writes (Dream Engine + upsert_memory tool).
                           UI reads and edits via get_user_memories(),
                           update_user_memory(), soft_delete_user_memory(),
                           restore_user_memory(). These are UI-portal-specific
                           operations not duplicated in the bot.
  - operational_memories : Bot only. UI never reads or writes this table.
  - dream_cycles         : Bot only. UI reads status via get_dream_cycles().

OWNED BY THE UI (this file — DMSDB class):
  - dms_otp_challenges   : UI only. Bot never reads or writes this table.
  - dms_ui_profiles      : UI only. Bot reads emergency_contacts count and
                           release_actions flag via Database.get_dms_status()
                           (count only, no sensitive fields).

DELEGATION PATTERN:
  The four methods that previously duplicated bot write logic now accept an
  optional `bot_db` parameter (a Database instance). When provided, they
  delegate to the canonical Database method. When not provided (e.g., in
  tests or standalone scripts), they fall back to direct SQL — this ensures
  backward compatibility without requiring a full refactor of main.py.

  In production (main.py), bot_db is always passed. The fallback path is
  a safety net, not the primary code path.
=============================================================================
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from db.database import Database

logger = logging.getLogger(__name__)

# Additional tables owned by the UI backend
UI_SCHEMA = """
CREATE TABLE IF NOT EXISTS dms_otp_challenges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matrix_id   TEXT NOT NULL,
    otp_hash    TEXT NOT NULL,
    expires_ts  REAL NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_otp_matrix_id ON dms_otp_challenges(matrix_id, expires_ts);

CREATE TABLE IF NOT EXISTS dms_ui_profiles (
    matrix_id           TEXT PRIMARY KEY,
    legal_name          TEXT,
    date_of_birth       TEXT,
    physical_address    TEXT,
    emergency_contacts  TEXT NOT NULL DEFAULT '[]',
    social_media        TEXT NOT NULL DEFAULT '[]',
    vault_text          TEXT,
    release_actions     TEXT NOT NULL DEFAULT '[]',
    updated_ts          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS user_memory_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   INTEGER NOT NULL,
    matrix_id   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    memory_text TEXT NOT NULL,
    archived_ts REAL NOT NULL,
    archived_by TEXT NOT NULL DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS idx_memory_history_memory_id
    ON user_memory_history(memory_id, matrix_id, version);
"""


class DMSDB:
    """
    Async SQLite wrapper for the DMS UI backend.

    Owns: dms_otp_challenges, dms_ui_profiles, user_memory_history (UI edits).
    Reads (but does not own writes for): registered_users, user_profiles,
        emergency_vault, audit_log, user_memories, dream_cycles.
    """

    def __init__(self, path: str):
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None
        # Set by main.py after both DB instances are created.
        # When set, write operations on bot-owned tables are delegated here.
        self._bot_db: Optional["Database"] = None

    def set_bot_db(self, bot_db: "Database") -> None:
        """
        Inject the canonical Database instance so write operations on
        bot-owned tables (registered_users, user_profiles, audit_log)
        are delegated to the single authoritative implementation.

        Call this from main.py after both db instances are connected.
        """
        self._bot_db = bot_db

    async def connect(self):
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(UI_SCHEMA)
        await self._conn.commit()
        logger.info("DMS UI database connected: %s", self._path)

    async def close(self):
        if self._conn:
            await self._conn.close()

    # ------------------------------------------------------------------
    # OTP Challenges (UI-owned table — no delegation needed)
    # ------------------------------------------------------------------

    async def create_otp(self, matrix_id: str, otp_hash: str, expires_ts: float):
        """Create a new OTP challenge, invalidating any previous unused ones."""
        await self._conn.execute(
            "UPDATE dms_otp_challenges SET used = 1 WHERE matrix_id = ? AND used = 0",
            (matrix_id,),
        )
        await self._conn.execute(
            "INSERT INTO dms_otp_challenges (matrix_id, otp_hash, expires_ts) VALUES (?, ?, ?)",
            (matrix_id, otp_hash, expires_ts),
        )
        await self._conn.commit()

    async def get_valid_otp(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        now = datetime.now(timezone.utc).timestamp()
        async with self._conn.execute(
            """SELECT * FROM dms_otp_challenges
               WHERE matrix_id = ? AND used = 0 AND expires_ts > ?
               ORDER BY expires_ts DESC LIMIT 1""",
            (matrix_id, now),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def consume_otp(self, otp_id: int):
        await self._conn.execute(
            "UPDATE dms_otp_challenges SET used = 1 WHERE id = ?", (otp_id,)
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Bot-owned tables — READ operations (no delegation needed)
    # ------------------------------------------------------------------

    async def get_registered_user(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        """Read a registered user row. Bot owns writes; UI reads only."""
        async with self._conn.execute(
            "SELECT * FROM registered_users WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_bot_profile(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        """Read user_profiles row. Bot owns writes; UI reads only."""
        async with self._conn.execute(
            "SELECT * FROM user_profiles WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_vault_meta(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        """
        Return vault metadata (created_ts, released_ts) WITHOUT the encrypted blob.
        The encrypted_data and iv columns are intentionally excluded.
        """
        async with self._conn.execute(
            "SELECT matrix_id, created_ts, released_ts FROM emergency_vault WHERE matrix_id = ?",
            (matrix_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_audit_log(self, matrix_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Read audit log entries for a user. Bot owns writes; UI reads only."""
        async with self._conn.execute(
            """SELECT * FROM audit_log
               WHERE actor_matrix_id = ? OR target_matrix_id = ?
               ORDER BY event_ts DESC LIMIT ?""",
            (matrix_id, matrix_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Bot-owned tables — WRITE operations (delegated to Database)
    #
    # These methods previously duplicated the bot's write logic.
    # They now delegate to the canonical Database instance when available,
    # falling back to direct SQL only when bot_db is not set (e.g., tests).
    # ------------------------------------------------------------------

    async def update_last_active(self, matrix_id: str):
        """
        Record a portal check-in — same semantics as the bot's !checkin command.
        DELEGATED to Database.update_last_active() when bot_db is set.
        """
        if self._bot_db is not None:
            await self._bot_db.update_last_active(matrix_id)
            return
        # Fallback (tests / standalone scripts only)
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            "UPDATE registered_users SET last_active_ts = ?, status = 'ACTIVE' WHERE matrix_id = ?",
            (now, matrix_id),
        )
        await self._conn.commit()

    async def update_threshold(self, matrix_id: str, threshold_h: int):
        """
        Update the missing threshold for a user.
        DELEGATED to Database.set_user_status() / direct update when bot_db is set.
        """
        if self._bot_db is not None:
            # Database doesn't have a standalone update_threshold method;
            # execute directly via the bot's connection to keep one write path.
            await self._bot_db._conn.execute(
                "UPDATE registered_users SET missing_threshold_h = ? WHERE matrix_id = ?",
                (threshold_h, matrix_id),
            )
            await self._bot_db._conn.commit()
            return
        # Fallback (tests / standalone scripts only)
        await self._conn.execute(
            "UPDATE registered_users SET missing_threshold_h = ? WHERE matrix_id = ?",
            (threshold_h, matrix_id),
        )
        await self._conn.commit()

    async def upsert_bot_profile(self, matrix_id: str, location: str, social_handles: str):
        """
        Update the bot's user_profiles table (location + social_handles JSON).
        DELEGATED to Database.upsert_profile() when bot_db is set.
        """
        if self._bot_db is not None:
            await self._bot_db.upsert_profile(matrix_id, location, social_handles)
            return
        # Fallback (tests / standalone scripts only)
        await self._conn.execute(
            """INSERT INTO user_profiles (matrix_id, location, social_handles)
               VALUES (?, ?, ?)
               ON CONFLICT(matrix_id) DO UPDATE SET
                   location       = excluded.location,
                   social_handles = excluded.social_handles""",
            (matrix_id, location, social_handles),
        )
        await self._conn.commit()

    async def log_event(
        self,
        event_type: str,
        actor_matrix_id: str = None,
        target_matrix_id: str = None,
        note: str = None,
    ):
        """
        Append an audit log event.
        DELEGATED to Database.log_event() when bot_db is set.
        """
        if self._bot_db is not None:
            await self._bot_db.log_event(
                event_type=event_type,
                actor_matrix_id=actor_matrix_id,
                target_matrix_id=target_matrix_id,
                note=note,
            )
            return
        # Fallback (tests / standalone scripts only)
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            """INSERT INTO audit_log (event_ts, event_type, actor_matrix_id, target_matrix_id, note)
               VALUES (?, ?, ?, ?, ?)""",
            (now, event_type, actor_matrix_id, target_matrix_id, note),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # UI-specific extended profile (UI-owned table — no delegation)
    # ------------------------------------------------------------------

    async def get_ui_profile(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM dms_ui_profiles WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_ui_profile(
        self,
        matrix_id: str,
        legal_name: Optional[str],
        date_of_birth: Optional[str],
        physical_address: Optional[str],
        emergency_contacts: list,
        social_media: list,
        vault_text: Optional[str],
        release_actions: list,
    ):
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            """INSERT INTO dms_ui_profiles
                   (matrix_id, legal_name, date_of_birth, physical_address,
                    emergency_contacts, social_media, vault_text, release_actions, updated_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(matrix_id) DO UPDATE SET
                   legal_name          = excluded.legal_name,
                   date_of_birth       = excluded.date_of_birth,
                   physical_address    = excluded.physical_address,
                   emergency_contacts  = excluded.emergency_contacts,
                   social_media        = excluded.social_media,
                   vault_text          = excluded.vault_text,
                   release_actions     = excluded.release_actions,
                   updated_ts          = excluded.updated_ts""",
            (
                matrix_id,
                legal_name,
                date_of_birth,
                physical_address,
                json.dumps(emergency_contacts),
                json.dumps(social_media),
                vault_text,
                json.dumps(release_actions),
                now,
            ),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Dream Memory — User Memories
    # (Bot writes via Dream Engine + upsert_memory tool.
    #  UI reads and allows user-initiated edits/deletes/restores.)
    # ------------------------------------------------------------------

    async def get_user_memories(
        self,
        matrix_id: str,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return all long-term memories for a specific user.
        By default, soft-deleted memories are excluded.
        """
        if include_deleted:
            query = """
                SELECT * FROM user_memories
                WHERE matrix_id = ?
                ORDER BY category, updated_ts DESC
            """
        else:
            query = """
                SELECT * FROM user_memories
                WHERE matrix_id = ? AND is_deleted = 0
                ORDER BY category, updated_ts DESC
            """
        async with self._conn.execute(query, (matrix_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_user_memory_by_id(
        self, memory_id: int, matrix_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single user memory by ID, scoped to the owning user."""
        async with self._conn.execute(
            "SELECT * FROM user_memories WHERE id = ? AND matrix_id = ?",
            (memory_id, matrix_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_user_memory_history(
        self, memory_id: int, matrix_id: str
    ) -> List[Dict[str, Any]]:
        """
        Return the full version history for a user memory.
        Ordered from oldest to newest version.
        """
        async with self._conn.execute(
            """SELECT * FROM user_memory_history
               WHERE memory_id = ? AND matrix_id = ?
               ORDER BY version ASC""",
            (memory_id, matrix_id),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def update_user_memory(
        self,
        memory_id: int,
        matrix_id: str,
        new_text: str,
        edited_by: str = "user",
    ) -> Optional[Dict[str, Any]]:
        """
        Update the text of a user memory (user-initiated edit via portal).

        This:
          1. Saves the current version to user_memory_history.
          2. Increments the version number.
          3. Updates the memory text and marks it as user-edited.

        Returns the updated memory dict, or None if not found.
        """
        existing = await self.get_user_memory_by_id(memory_id, matrix_id)
        if not existing:
            return None

        now = datetime.now(timezone.utc).timestamp()

        # Archive the current version
        await self._conn.execute(
            """INSERT INTO user_memory_history
                   (memory_id, matrix_id, version, memory_text, archived_ts, archived_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                memory_id,
                matrix_id,
                existing["version"],
                existing["memory_text"],
                now,
                edited_by,
            ),
        )

        # Update the live memory
        new_version = existing["version"] + 1
        await self._conn.execute(
            """UPDATE user_memories
               SET memory_text     = ?,
                   version         = ?,
                   updated_ts      = ?,
                   is_user_edited  = 1
               WHERE id = ? AND matrix_id = ?""",
            (new_text, new_version, now, memory_id, matrix_id),
        )
        await self._conn.commit()

        return await self.get_user_memory_by_id(memory_id, matrix_id)

    async def soft_delete_user_memory(
        self, memory_id: int, matrix_id: str
    ) -> bool:
        """
        Soft-delete a user memory. The record is retained with is_deleted=1
        so version history is preserved.
        Returns True if a row was updated, False if not found.
        """
        now = datetime.now(timezone.utc).timestamp()
        async with self._conn.execute(
            """UPDATE user_memories
               SET is_deleted = 1, updated_ts = ?
               WHERE id = ? AND matrix_id = ? AND is_deleted = 0""",
            (now, memory_id, matrix_id),
        ) as cur:
            changed = cur.rowcount
        await self._conn.commit()
        return changed > 0

    async def restore_user_memory(
        self, memory_id: int, matrix_id: str
    ) -> bool:
        """Restore a previously soft-deleted user memory."""
        now = datetime.now(timezone.utc).timestamp()
        async with self._conn.execute(
            """UPDATE user_memories
               SET is_deleted = 0, updated_ts = ?
               WHERE id = ? AND matrix_id = ? AND is_deleted = 1""",
            (now, memory_id, matrix_id),
        ) as cur:
            changed = cur.rowcount
        await self._conn.commit()
        return changed > 0

    # ------------------------------------------------------------------
    # Dream Memory — Dream Cycle Status (read-only from UI)
    # ------------------------------------------------------------------

    async def get_dream_cycles(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the most recent Dream Engine cycle records."""
        async with self._conn.execute(
            """SELECT * FROM dream_cycles
               ORDER BY run_ts DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_last_dream_cycle(self) -> Optional[Dict[str, Any]]:
        """Return the most recent completed Dream cycle."""
        async with self._conn.execute(
            """SELECT * FROM dream_cycles
               WHERE status = 'completed'
               ORDER BY run_ts DESC LIMIT 1""",
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
