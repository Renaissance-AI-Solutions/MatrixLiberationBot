"""
db/database.py
==============
Async SQLite database layer for the Matrix Wellness Bot.
Handles all schema creation and CRUD operations.
All sensitive fields (emergency_data_ciphertext, iv) are stored as raw bytes.
No plaintext emergency data is ever written to the database.
"""

import aiosqlite
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# SQL schema definitions
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
"""


class Database:
    """Async SQLite database wrapper for the wellness bot."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Open the database connection and initialise the schema."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        logger.info("Database connected and schema initialised: %s", self.db_path)

    async def close(self):
        if self._conn:
            await self._conn.close()
            logger.info("Database connection closed.")

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
