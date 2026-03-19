"""
bot/onboarding.py
=================
Phase 1: Secure Onboarding & Data Vaulting

Handles the multi-step DM onboarding flow for new users registering their
Dead Man's Switch. All sensitive Emergency Data is encrypted immediately
upon receipt and the plaintext is never persisted.

Onboarding conversation state is held in-memory (per-session) only.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from db.database import Database
from security.encryption import encrypt_emergency_data, pack_vault_blob

logger = logging.getLogger(__name__)

# Onboarding steps in order
STEPS = [
    "AWAIT_DISPLAY_NAME",
    "AWAIT_LOCATION",
    "AWAIT_SOCIAL_HANDLES",
    "AWAIT_THRESHOLD",
    "AWAIT_EMERGENCY_DATA",
    "COMPLETE",
]

STEP_PROMPTS = {
    "AWAIT_DISPLAY_NAME": (
        "Welcome to the **Matrix Wellness Monitor** — your personal Dead Man's Switch.\n\n"
        "This service will monitor your activity and, if you go missing beyond your chosen "
        "threshold, will alert the group and facilitate secure release of your emergency data.\n\n"
        "**Step 1 of 5:** Please enter your **full display name** (as you wish to be known "
        "in any alert messages). This is the name that will appear in group alerts."
    ),
    "AWAIT_LOCATION": (
        "**Step 2 of 5:** Please enter your **general location** (e.g., city and country). "
        "This is used only for OSINT safety checks (e.g., searching local news) if you go "
        "missing. Example: `Chicago, USA`\n\n"
        "Type `skip` to omit this."
    ),
    "AWAIT_SOCIAL_HANDLES": (
        "**Step 3 of 5:** Please provide your **public social media handles** for safety "
        "verification. Format as a comma-separated list:\n\n"
        "`platform:handle, platform:handle`\n\n"
        "Supported platforms: `twitter`, `mastodon`, `bluesky`, `instagram`, `facebook`, "
        "`linkedin`, `github`, `reddit`\n\n"
        "Example: `twitter:@alice, mastodon:alice@fosstodon.org, bluesky:alice.bsky.social`\n\n"
        "Type `skip` to omit this."
    ),
    "AWAIT_THRESHOLD": (
        "**Step 4 of 5:** How many **hours of inactivity** should trigger the missing alert? "
        "This is your personal threshold — the bot will wait this long before starting "
        "verification checks.\n\n"
        "Common choices: `24` (1 day), `72` (3 days), `168` (1 week)\n\n"
        "Enter a number between 1 and 720 (30 days)."
    ),
    "AWAIT_EMERGENCY_DATA": (
        "**Step 5 of 5 — CRITICAL:** Please now send your **Emergency Data**.\n\n"
        "This may include: last wishes, private contact information, investigative leads, "
        "account credentials for trusted parties, or any other information you want "
        "released to the group if you go missing.\n\n"
        "**IMPORTANT SECURITY NOTICE:**\n"
        "- This message will be **encrypted immediately** using AES-256-GCM.\n"
        "- The plaintext will **never be stored** on disk or in bot memory.\n"
        "- It can only be decrypted when the group reaches consensus.\n"
        "- Send your emergency data as a single message now."
    ),
}

COMPLETE_MESSAGE = (
    "**Registration complete!** Your Dead Man's Switch is now active.\n\n"
    "Your emergency data has been encrypted and vaulted securely.\n\n"
    "**Your settings:**\n"
    "- Missing threshold: **{threshold_h} hours**\n"
    "- Social handles registered: **{handle_count}**\n"
    "- Location registered: **{location}**\n\n"
    "**Commands you can use at any time:**\n"
    "- `!checkin` — Reset your activity timer manually.\n"
    "- `!my_status` — View your current registration status.\n"
    "- `!update_emergency_data` — Replace your emergency data with new content.\n"
    "- `!deregister` — Remove your registration entirely.\n\n"
    "Stay safe. The group has your back."
)


class OnboardingSession:
    """Tracks the multi-step onboarding state for a single user."""

    def __init__(self, matrix_id: str):
        self.matrix_id = matrix_id
        self.step_index = 0
        self.data: Dict[str, Any] = {}

    @property
    def current_step(self) -> str:
        return STEPS[self.step_index]

    def advance(self):
        self.step_index = min(self.step_index + 1, len(STEPS) - 1)

    @property
    def is_complete(self) -> bool:
        return self.current_step == "COMPLETE"


class OnboardingManager:
    """
    Manages all active onboarding sessions and processes incoming DM messages
    to advance users through the registration flow.
    """

    def __init__(self, db: Database, master_key_hex: str, default_threshold_h: int = 72):
        self.db = db
        self.master_key_hex = master_key_hex
        self.default_threshold_h = default_threshold_h
        # In-memory sessions: {matrix_id: OnboardingSession}
        self._sessions: Dict[str, OnboardingSession] = {}

    def has_session(self, matrix_id: str) -> bool:
        return matrix_id in self._sessions

    def start_session(self, matrix_id: str) -> str:
        """Start a new onboarding session and return the first prompt."""
        self._sessions[matrix_id] = OnboardingSession(matrix_id)
        logger.info("Onboarding session started for %s", matrix_id)
        return STEP_PROMPTS["AWAIT_DISPLAY_NAME"]

    async def process_message(self, matrix_id: str, message: str) -> str:
        """
        Process an incoming DM message for a user in an active onboarding session.
        Returns the next prompt or a completion/error message.
        """
        session = self._sessions.get(matrix_id)
        if not session:
            return "No active registration session. Send `!register_switch` to begin."

        step = session.current_step
        message = message.strip()

        if step == "AWAIT_DISPLAY_NAME":
            if len(message) < 2:
                return "Please enter a valid display name (at least 2 characters)."
            session.data["display_name"] = message
            session.advance()
            return STEP_PROMPTS["AWAIT_LOCATION"]

        elif step == "AWAIT_LOCATION":
            session.data["location"] = None if message.lower() == "skip" else message
            session.advance()
            return STEP_PROMPTS["AWAIT_SOCIAL_HANDLES"]

        elif step == "AWAIT_SOCIAL_HANDLES":
            if message.lower() == "skip":
                session.data["social_handles"] = {}
            else:
                handles = self._parse_social_handles(message)
                if handles is None:
                    return (
                        "Could not parse social handles. Please use the format:\n"
                        "`platform:handle, platform:handle`\n\n"
                        "Or type `skip` to continue without social handles."
                    )
                session.data["social_handles"] = handles
            session.advance()
            return STEP_PROMPTS["AWAIT_THRESHOLD"]

        elif step == "AWAIT_THRESHOLD":
            try:
                hours = int(message)
                if not (1 <= hours <= 720):
                    raise ValueError
            except ValueError:
                return "Please enter a whole number between 1 and 720."
            session.data["threshold_h"] = hours
            session.advance()
            return STEP_PROMPTS["AWAIT_EMERGENCY_DATA"]

        elif step == "AWAIT_EMERGENCY_DATA":
            if len(message) < 10:
                return (
                    "Emergency data seems too short. Please provide meaningful content "
                    "(at least 10 characters). This data will be encrypted immediately."
                )
            # Encrypt immediately — plaintext never leaves this scope
            result = await self._vault_emergency_data(session, message)
            del message  # Explicitly remove plaintext from local scope
            if result:
                session.advance()
                del self._sessions[matrix_id]  # Clean up session
                return COMPLETE_MESSAGE.format(
                    threshold_h=session.data["threshold_h"],
                    handle_count=len(session.data.get("social_handles", {})),
                    location=session.data.get("location") or "not provided",
                )
            else:
                return (
                    "An error occurred while encrypting your emergency data. "
                    "Please try again or contact the bot administrator."
                )

        return "Unexpected state. Please restart with `!register_switch`."

    async def _vault_emergency_data(
        self, session: OnboardingSession, plaintext: str
    ) -> bool:
        """
        Encrypt the emergency data and persist all records to the database.
        Returns True on success.
        """
        try:
            # 1. Encrypt the emergency data
            ciphertext, iv, salt = encrypt_emergency_data(plaintext, self.master_key_hex)
            blob = pack_vault_blob(ciphertext, salt)

            # 2. Register the user
            await self.db.register_user(
                matrix_id=session.matrix_id,
                display_name=session.data["display_name"],
                missing_threshold_h=session.data["threshold_h"],
            )

            # 3. Store the user profile
            await self.db.upsert_profile(
                matrix_id=session.matrix_id,
                location=session.data.get("location") or "",
                social_handles=json.dumps(session.data.get("social_handles", {})),
            )

            # 4. Store the encrypted vault
            await self.db.store_emergency_data(
                matrix_id=session.matrix_id,
                encrypted_data=blob,
                iv=iv,
            )

            # 5. Audit log
            await self.db.log_event(
                event_type="USER_REGISTERED",
                actor_matrix_id=session.matrix_id,
                note=f"threshold={session.data['threshold_h']}h",
            )

            logger.info(
                "User %s successfully registered and emergency data vaulted.",
                session.matrix_id,
            )
            return True

        except Exception as exc:
            logger.error(
                "Failed to vault emergency data for %s: %s",
                session.matrix_id,
                exc,
                exc_info=True,
            )
            return False

    @staticmethod
    def _parse_social_handles(raw: str) -> Optional[Dict[str, str]]:
        """
        Parse a comma-separated 'platform:handle' string into a dict.
        Returns None if parsing fails.
        """
        handles = {}
        try:
            for part in raw.split(","):
                part = part.strip()
                if ":" not in part:
                    return None
                platform, handle = part.split(":", 1)
                platform = platform.strip().lower()
                handle = handle.strip()
                if platform and handle:
                    handles[platform] = handle
        except Exception:
            return None
        return handles if handles else None
