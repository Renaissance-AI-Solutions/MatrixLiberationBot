"""
dms-ui/backend/db.py
====================
Async SQLite layer for the DMS Web UI.

This module opens the SAME database file as Liberation Bot (read/write).
It reuses the existing bot tables:
  - registered_users   (matrix_id, display_name, missing_threshold_h, last_active_ts, status)
  - user_profiles      (matrix_id, location, social_handles JSON)
  - emergency_vault    (matrix_id, encrypted_data, iv, created_ts, released_ts)
  - audit_log          (id, event_ts, event_type, actor_matrix_id, target_matrix_id, note)

And adds two UI-specific tables:
  - dms_otp_challenges (matrix_id, otp_hash, expires_ts, used)
  - dms_ui_profiles    (matrix_id, emergency_contacts JSON, social_media JSON,
                        legal_name, date_of_birth, physical_address,
                        vault_text, release_actions JSON, updated_ts)

The vault_text in dms_ui_profiles stores the user's PLAINTEXT final message.
It is stored server-side (same security model as the bot's encrypted vault —
the server holds the master key anyway). If you want to encrypt it, use the
bot's existing AES-GCM module; the UI backend has access to BOT_MASTER_KEY.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

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
"""


class DMSDB:
    """Async SQLite wrapper for the DMS UI backend."""

    def __init__(self, path: str):
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

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
    # OTP Challenges
    # ------------------------------------------------------------------

    async def create_otp(self, matrix_id: str, otp_hash: str, expires_ts: float):
        # Invalidate any previous unused OTPs for this user
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
    # Bot tables — read/write alongside the bot
    # ------------------------------------------------------------------

    async def get_registered_user(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM registered_users WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_last_active(self, matrix_id: str):
        """Record a manual check-in — same semantics as the bot's !checkin command."""
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            "UPDATE registered_users SET last_active_ts = ?, status = 'ACTIVE' WHERE matrix_id = ?",
            (now, matrix_id),
        )
        await self._conn.commit()

    async def update_threshold(self, matrix_id: str, threshold_h: int):
        await self._conn.execute(
            "UPDATE registered_users SET missing_threshold_h = ? WHERE matrix_id = ?",
            (threshold_h, matrix_id),
        )
        await self._conn.commit()

    async def get_bot_profile(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM user_profiles WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_bot_profile(self, matrix_id: str, location: str, social_handles: str):
        """Update the bot's user_profiles table (location + social_handles JSON)."""
        await self._conn.execute(
            """INSERT INTO user_profiles (matrix_id, location, social_handles)
               VALUES (?, ?, ?)
               ON CONFLICT(matrix_id) DO UPDATE SET
                   location       = excluded.location,
                   social_handles = excluded.social_handles""",
            (matrix_id, location, social_handles),
        )
        await self._conn.commit()

    async def get_vault_meta(self, matrix_id: str) -> Optional[Dict[str, Any]]:
        """Return vault metadata (created_ts, released_ts) without the encrypted blob."""
        async with self._conn.execute(
            "SELECT matrix_id, created_ts, released_ts FROM emergency_vault WHERE matrix_id = ?",
            (matrix_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_audit_log(self, matrix_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        async with self._conn.execute(
            """SELECT * FROM audit_log
               WHERE actor_matrix_id = ? OR target_matrix_id = ?
               ORDER BY event_ts DESC LIMIT ?""",
            (matrix_id, matrix_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def log_event(
        self,
        event_type: str,
        actor_matrix_id: str = None,
        target_matrix_id: str = None,
        note: str = None,
    ):
        now = datetime.now(timezone.utc).timestamp()
        await self._conn.execute(
            """INSERT INTO audit_log (event_ts, event_type, actor_matrix_id, target_matrix_id, note)
               VALUES (?, ?, ?, ?, ?)""",
            (now, event_type, actor_matrix_id, target_matrix_id, note),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # UI-specific extended profile
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
