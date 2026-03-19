"""
bot/heartbeat.py
================
Phase 2: Activity Monitoring — The "Heartbeat"

Runs as a periodic background task (via APScheduler) to:
  1. Check all registered users' last-active timestamps.
  2. Identify users who have exceeded their personal missing threshold.
  3. Transition those users to MISSING status and hand off to the OSINT module.

Also handles manual `!checkin` commands from users.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, List, Dict, Any

from db.database import Database

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """
    Monitors registered users for inactivity and triggers the verification
    pipeline when a user's missing threshold is exceeded.
    """

    def __init__(
        self,
        db: Database,
        on_user_missing: Callable[[Dict[str, Any]], Awaitable[None]],
    ):
        """
        Args:
            db: The database instance.
            on_user_missing: An async callback invoked when a user transitions
                             to MISSING status. Receives the user dict.
        """
        self.db = db
        self.on_user_missing = on_user_missing

    async def record_activity(self, matrix_id: str):
        """
        Record that a user was active right now.
        Called whenever the bot observes any message from the user in the group.
        """
        user = await self.db.get_user(matrix_id)
        if user:
            await self.db.update_last_active(matrix_id)
            logger.debug("Activity recorded for %s", matrix_id)

    async def handle_checkin(self, matrix_id: str) -> str:
        """
        Handle a manual `!checkin` command from a registered user.
        Returns a confirmation message.
        """
        user = await self.db.get_user(matrix_id)
        if not user:
            return (
                "You are not registered with the Wellness Monitor. "
                "Send `!register_switch` in a DM to the bot to register."
            )

        await self.db.update_last_active(matrix_id)
        await self.db.log_event(
            event_type="MANUAL_CHECKIN",
            actor_matrix_id=matrix_id,
        )

        threshold_h = user["missing_threshold_h"]
        logger.info("Manual check-in received from %s", matrix_id)
        return (
            f"Check-in confirmed. Your activity timer has been reset.\n"
            f"Your next alert threshold is **{threshold_h} hours** from now."
        )

    async def run_check(self) -> List[str]:
        """
        Periodic check: scan all active users and identify those who have
        exceeded their missing threshold.

        Returns a list of matrix_ids that were transitioned to MISSING.
        """
        now_ts = datetime.now(timezone.utc).timestamp()
        users = await self.db.get_all_active_users()
        newly_missing: List[str] = []

        for user in users:
            matrix_id = user["matrix_id"]
            threshold_seconds = user["missing_threshold_h"] * 3600
            last_active = user["last_active_ts"]
            elapsed_seconds = now_ts - last_active
            status = user["status"]

            if elapsed_seconds > threshold_seconds and status == "ACTIVE":
                # Transition to MISSING
                await self.db.set_user_status(matrix_id, "MISSING")
                await self.db.log_event(
                    event_type="STATUS_TRANSITION_MISSING",
                    target_matrix_id=matrix_id,
                    note=f"elapsed={elapsed_seconds:.0f}s threshold={threshold_seconds}s",
                )
                logger.warning(
                    "User %s transitioned to MISSING (elapsed: %.1f h, threshold: %d h)",
                    matrix_id,
                    elapsed_seconds / 3600,
                    user["missing_threshold_h"],
                )
                newly_missing.append(matrix_id)

                # Trigger the OSINT verification pipeline
                try:
                    await self.on_user_missing(user)
                except Exception as exc:
                    logger.error(
                        "on_user_missing callback failed for %s: %s",
                        matrix_id,
                        exc,
                        exc_info=True,
                    )

        if newly_missing:
            logger.info(
                "Heartbeat check complete: %d user(s) newly marked MISSING: %s",
                len(newly_missing),
                newly_missing,
            )
        else:
            logger.debug("Heartbeat check complete: all users within threshold.")

        return newly_missing

    async def handle_my_status(self, matrix_id: str) -> str:
        """
        Handle a `!my_status` command. Returns a human-readable status summary.
        """
        user = await self.db.get_user(matrix_id)
        if not user:
            return (
                "You are not registered with the Wellness Monitor. "
                "Send `!register_switch` in a DM to register."
            )

        now_ts = datetime.now(timezone.utc).timestamp()
        last_active_ts = user["last_active_ts"]
        elapsed_h = (now_ts - last_active_ts) / 3600
        threshold_h = user["missing_threshold_h"]
        remaining_h = max(0.0, threshold_h - elapsed_h)
        status = user["status"]

        last_active_str = datetime.fromtimestamp(
            last_active_ts, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

        return (
            f"**Your Wellness Monitor Status**\n\n"
            f"- Status: **{status}**\n"
            f"- Last activity detected: **{last_active_str}**\n"
            f"- Time elapsed since last activity: **{elapsed_h:.1f} hours**\n"
            f"- Your missing threshold: **{threshold_h} hours**\n"
            f"- Time remaining before alert: **{remaining_h:.1f} hours**\n\n"
            f"Send `!checkin` to reset your timer."
        )
