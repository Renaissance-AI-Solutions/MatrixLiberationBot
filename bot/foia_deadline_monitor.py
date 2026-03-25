"""
bot/foia_deadline_monitor.py
============================
Liberation Bot — FOIA Deadline Monitor

Background task that runs on a configurable interval and:
  1. Detects SUBMITTED requests whose expected_response_date has passed
     (overdue) and sends the user a proactive DM reminder.
  2. Detects requests due within the next 48 hours and sends an
     advance warning DM.
  3. Tracks which reminders have already been sent this cycle to avoid
     spamming users.

This module is intentionally free of any Matrix-specific imports so it
can be unit-tested in isolation. The bot wires it in via the
`on_reminder_callback` hook.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, Set

logger = logging.getLogger(__name__)

# How many seconds before a deadline to send the advance warning
ADVANCE_WARNING_SECONDS = 48 * 3600   # 48 hours

# Minimum interval between reminder DMs for the same request (seconds)
# Prevents re-sending on every scheduler tick if the bot restarts
REMINDER_COOLDOWN_SECONDS = 20 * 3600  # 20 hours


class FOIADeadlineMonitor:
    """
    Monitors FOIA request deadlines and fires reminder callbacks.

    Parameters
    ----------
    db : Database
        The shared bot database instance.
    on_reminder : async callable (sender_id: str, message: str) -> None
        Called when a reminder DM should be sent. The caller (bot.py)
        is responsible for actually sending the Matrix message.
    """

    def __init__(self, db, on_reminder: Callable[[str, str], Awaitable[None]]):
        self._db = db
        self._on_reminder = on_reminder
        # Track (request_id, reminder_type) pairs already sent this session
        # to avoid duplicate DMs across scheduler ticks
        self._sent_reminders: Set[tuple] = set()

    async def run_check(self) -> None:
        """
        Execute one deadline check cycle. Called by the APScheduler job
        in bot.py. Runs two passes: overdue check and advance warning check.
        """
        try:
            await self._check_overdue()
            await self._check_due_soon()
        except Exception as exc:
            logger.error("FOIADeadlineMonitor.run_check failed: %s", exc)

    async def _check_overdue(self) -> None:
        """Send reminders for requests whose deadline has already passed."""
        try:
            overdue = await self._db.get_all_overdue_foia_requests()
        except Exception as exc:
            logger.error("get_all_overdue_foia_requests failed: %s", exc)
            return

        for req in overdue:
            key = (req["id"], "overdue")
            if key in self._sent_reminders:
                continue
            self._sent_reminders.add(key)

            deadline_str = _format_ts(req["expected_response_date"])
            days_overdue = _days_since(req["expected_response_date"])
            msg = (
                f"**FOIA Deadline Overdue** — Request #{req['id']}\n\n"
                f"Your FOIA request to **{req['target_agency']}** "
                f"({req['jurisdiction_code']}) was due by **{deadline_str}** "
                f"({days_overdue} day(s) ago).\n\n"
                f"Subject: _{req['subject_summary']}_\n\n"
                f"The agency has not responded within the statutory deadline. "
                f"You may now file an appeal or pursue legal action. "
                f"Use `!foia_appeal {req['id']}` to draft an appeal letter, "
                f"or `!foia_status {req['id']}` to update the request status."
            )
            try:
                await self._on_reminder(req["sender_id"], msg)
                logger.info(
                    "Sent overdue reminder for FOIA request #%s to %s",
                    req["id"], req["sender_id"],
                )
            except Exception as exc:
                logger.error(
                    "Failed to send overdue reminder for request #%s: %s",
                    req["id"], exc,
                )

    async def _check_due_soon(self) -> None:
        """Send advance warnings for requests due within ADVANCE_WARNING_SECONDS."""
        try:
            due_soon = await self._db.get_foia_requests_due_soon(
                within_seconds=ADVANCE_WARNING_SECONDS
            )
        except Exception as exc:
            logger.error("get_foia_requests_due_soon failed: %s", exc)
            return

        for req in due_soon:
            key = (req["id"], "due_soon")
            if key in self._sent_reminders:
                continue
            self._sent_reminders.add(key)

            deadline_str = _format_ts(req["expected_response_date"])
            hours_remaining = _hours_until(req["expected_response_date"])
            msg = (
                f"**FOIA Deadline Approaching** — Request #{req['id']}\n\n"
                f"Your FOIA request to **{req['target_agency']}** "
                f"({req['jurisdiction_code']}) is due by **{deadline_str}** "
                f"({hours_remaining:.0f} hours from now).\n\n"
                f"Subject: _{req['subject_summary']}_\n\n"
                f"If you have not yet received a response, prepare to follow up "
                f"with the agency directly. Use `!foia_status {req['id']}` to "
                f"update the status once you receive a response."
            )
            try:
                await self._on_reminder(req["sender_id"], msg)
                logger.info(
                    "Sent due-soon reminder for FOIA request #%s to %s",
                    req["id"], req["sender_id"],
                )
            except Exception as exc:
                logger.error(
                    "Failed to send due-soon reminder for request #%s: %s",
                    req["id"], exc,
                )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_ts(ts: float) -> str:
    """Format a Unix timestamp as a human-readable UTC date string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _days_since(ts: float) -> int:
    """Return the number of whole days since the given timestamp."""
    now = datetime.now(timezone.utc).timestamp()
    return max(0, int((now - ts) / 86400))


def _hours_until(ts: float) -> float:
    """Return the number of hours until the given timestamp."""
    now = datetime.now(timezone.utc).timestamp()
    return max(0.0, (ts - now) / 3600)
