"""
tests/test_database.py
======================
Unit tests for the Database layer.
Uses an in-memory SQLite database for isolation.
"""

import asyncio
import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


@pytest.mark.asyncio
class TestDatabase:

    async def test_register_user(self, db):
        result = await db.register_user("@alice:matrix.org", "Alice", 72)
        assert result is True
        user = await db.get_user("@alice:matrix.org")
        assert user is not None
        assert user["display_name"] == "Alice"
        assert user["missing_threshold_h"] == 72
        assert user["status"] == "ACTIVE"

    async def test_get_nonexistent_user(self, db):
        user = await db.get_user("@nobody:matrix.org")
        assert user is None

    async def test_update_last_active(self, db):
        await db.register_user("@bob:matrix.org", "Bob", 48)
        user_before = await db.get_user("@bob:matrix.org")
        ts_before = user_before["last_active_ts"]

        await asyncio.sleep(0.01)
        await db.update_last_active("@bob:matrix.org")
        user_after = await db.get_user("@bob:matrix.org")
        assert user_after["last_active_ts"] >= ts_before

    async def test_set_user_status(self, db):
        await db.register_user("@carol:matrix.org", "Carol", 24)
        await db.set_user_status("@carol:matrix.org", "MISSING", "threshold exceeded")
        user = await db.get_user("@carol:matrix.org")
        assert user["status"] == "MISSING"
        assert user["osint_result_note"] == "threshold exceeded"

    async def test_upsert_profile(self, db):
        await db.register_user("@dave:matrix.org", "Dave", 72)
        handles = json.dumps({"twitter": "@dave", "github": "dave42"})
        await db.upsert_profile("@dave:matrix.org", "London, UK", handles)
        profile = await db.get_profile("@dave:matrix.org")
        assert profile["location"] == "London, UK"
        assert "@dave" in profile["social_handles"]

    async def test_store_and_retrieve_emergency_data(self, db):
        await db.register_user("@eve:matrix.org", "Eve", 72)
        fake_blob = b"\x00\x01\x02" * 16
        fake_iv = b"\xFF" * 12
        await db.store_emergency_data("@eve:matrix.org", fake_blob, fake_iv)
        vault = await db.get_emergency_data("@eve:matrix.org")
        assert vault is not None
        assert vault["encrypted_data"] == fake_blob
        assert vault["iv"] == fake_iv
        assert vault["released_ts"] is None

    async def test_mark_vault_released(self, db):
        await db.register_user("@frank:matrix.org", "Frank", 72)
        await db.store_emergency_data("@frank:matrix.org", b"\x00" * 32, b"\x00" * 12)
        await db.mark_vault_released("@frank:matrix.org")
        vault = await db.get_emergency_data("@frank:matrix.org")
        assert vault["released_ts"] is not None

    async def test_consensus_votes(self, db):
        await db.register_user("@grace:matrix.org", "Grace", 72)
        # First vote
        result1 = await db.add_vote("@grace:matrix.org", "@voter1:matrix.org")
        assert result1 is True
        # Duplicate vote
        result2 = await db.add_vote("@grace:matrix.org", "@voter1:matrix.org")
        assert result2 is False
        # Second unique vote
        result3 = await db.add_vote("@grace:matrix.org", "@voter2:matrix.org")
        assert result3 is True
        count = await db.count_votes("@grace:matrix.org")
        assert count == 2

    async def test_clear_votes(self, db):
        await db.register_user("@henry:matrix.org", "Henry", 72)
        await db.add_vote("@henry:matrix.org", "@v1:matrix.org")
        await db.add_vote("@henry:matrix.org", "@v2:matrix.org")
        await db.clear_votes("@henry:matrix.org")
        count = await db.count_votes("@henry:matrix.org")
        assert count == 0

    async def test_get_all_active_users(self, db):
        await db.register_user("@u1:matrix.org", "User1", 72)
        await db.register_user("@u2:matrix.org", "User2", 48)
        await db.register_user("@u3:matrix.org", "User3", 24)
        await db.set_user_status("@u3:matrix.org", "RELEASED")
        users = await db.get_all_active_users()
        ids = [u["matrix_id"] for u in users]
        assert "@u1:matrix.org" in ids
        assert "@u2:matrix.org" in ids
        assert "@u3:matrix.org" not in ids

    async def test_audit_log(self, db):
        await db.log_event(
            event_type="TEST_EVENT",
            actor_matrix_id="@actor:matrix.org",
            target_matrix_id="@target:matrix.org",
            note="test note",
        )
        async with db._conn.execute(
            "SELECT * FROM audit_log WHERE event_type = 'TEST_EVENT'"
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row["actor_matrix_id"] == "@actor:matrix.org"
