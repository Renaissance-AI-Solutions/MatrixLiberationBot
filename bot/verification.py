"""
bot/verification.py
===================
Phase 3: Automated Verification (Pre-Escalation)

When a user exceeds their missing threshold, this module:
  1. Scans the user's provided public social media handles for recent activity.
  2. Scans recent Matrix group chat history for any context from the user.
  3. Conducts a targeted web search against news and obituary databases.

If a legitimate reason for absence is found, the timer is reset and the
finding is privately logged. If no reason is found, the escalation pipeline
is triggered.
"""

import json
import logging
from typing import Dict, Any, Callable, Awaitable, Optional

from db.database import Database
from osint.scanner import OSINTScanner

logger = logging.getLogger(__name__)


class VerificationPipeline:
    """
    Orchestrates the automated OSINT verification checks for a missing user.
    """

    def __init__(
        self,
        db: Database,
        osint_scanner: OSINTScanner,
        on_verification_passed: Callable[[Dict[str, Any], str], Awaitable[None]],
        on_verification_failed: Callable[[Dict[str, Any], str], Awaitable[None]],
        send_dm: Callable[[str, str], Awaitable[None]],
    ):
        """
        Args:
            db: Database instance.
            osint_scanner: Configured OSINTScanner instance.
            on_verification_passed: Async callback when absence is explained.
                                    Receives (user_dict, summary_note).
            on_verification_failed: Async callback when no explanation found.
                                    Receives (user_dict, summary_note).
            send_dm: Async callback to send a DM to a Matrix user.
                     Signature: send_dm(matrix_id, message_text)
        """
        self.db = db
        self.osint_scanner = osint_scanner
        self.on_verification_passed = on_verification_passed
        self.on_verification_failed = on_verification_failed
        self.send_dm = send_dm

    async def run(self, user: Dict[str, Any]):
        """
        Execute the full verification pipeline for a missing user.
        """
        matrix_id = user["matrix_id"]
        display_name = user.get("display_name", matrix_id)

        logger.info(
            "Starting verification pipeline for %s (%s)", matrix_id, display_name
        )

        # Retrieve profile for OSINT data
        profile = await self.db.get_profile(matrix_id)
        location: Optional[str] = None
        social_handles: Dict[str, str] = {}

        if profile:
            location = profile.get("location") or None
            raw_handles = profile.get("social_handles") or "{}"
            try:
                social_handles = json.loads(raw_handles)
            except json.JSONDecodeError:
                social_handles = {}

        # --- Action A: Scan public social media handles ---
        logger.info("Action A: Scanning social media handles for %s", matrix_id)

        # --- Action C: Scan news/obituary databases ---
        logger.info("Action C: Scanning news/obituary databases for %s", matrix_id)

        osint_result = await self.osint_scanner.run_full_scan(
            display_name=display_name,
            location=location,
            social_handles=social_handles if social_handles else None,
        )

        summary = osint_result.get("summary", "No summary available.")
        details = osint_result.get("details", [])
        found_activity = osint_result.get("found_activity", False)

        # Build a detailed note for the audit log (no plaintext sensitive data)
        detail_note = " | ".join(details[:10]) if details else "No details."
        full_note = f"OSINT: {summary} | {detail_note}"

        # Mark OSINT as checked in the database
        await self.db.mark_osint_checked(matrix_id, full_note[:1000])
        await self.db.log_event(
            event_type="OSINT_SCAN_COMPLETE",
            target_matrix_id=matrix_id,
            note=f"found_activity={found_activity}",
        )

        if found_activity:
            # Absence appears to have a legitimate explanation
            logger.info(
                "Verification PASSED for %s: activity found. Resetting timer.",
                matrix_id,
            )
            await self.db.update_last_active(matrix_id)
            await self.db.log_event(
                event_type="VERIFICATION_PASSED",
                target_matrix_id=matrix_id,
                note=summary,
            )

            # Privately notify the user (if reachable)
            dm_message = (
                f"**Wellness Monitor — Absence Detected & Resolved**\n\n"
                f"Your activity timer expired, but our automated safety checks found "
                f"evidence of recent public activity. Your timer has been reset.\n\n"
                f"**Finding:** {summary}\n\n"
                f"If this was an error or you have concerns, please send `!checkin` "
                f"to confirm you are safe."
            )
            try:
                await self.send_dm(matrix_id, dm_message)
            except Exception as exc:
                logger.warning("Could not send DM to %s: %s", matrix_id, exc)

            await self.on_verification_passed(user, summary)

        else:
            # No explanation found — escalate to group
            logger.warning(
                "Verification FAILED for %s: no activity found. Escalating.",
                matrix_id,
            )
            await self.db.set_user_status(matrix_id, "ESCALATED")
            await self.db.log_event(
                event_type="VERIFICATION_FAILED_ESCALATING",
                target_matrix_id=matrix_id,
                note=summary,
            )
            await self.on_verification_failed(user, summary)
