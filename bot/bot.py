"""
bot/bot.py
==========
Main Bot Orchestrator — Matrix Ecosystem Security and Wellness Monitor

This module wires together all subsystems:
  - Matrix client (simplematrixbotlib / matrix-nio) for E2EE messaging
  - Database layer
  - Onboarding manager (Phase 1)
  - Heartbeat monitor (Phase 2)
  - OSINT verification pipeline (Phase 3)
  - Consensus manager (Phase 4)
  - Release manager (Phase 5)
  - APScheduler for periodic heartbeat checks

Command Reference:
  DM Commands (sent directly to the bot):
    !register_switch         — Begin the onboarding flow
    !checkin                 — Reset your activity timer
    !my_status               — View your current status
    !update_emergency_data   — Replace your emergency data
    !deregister              — Remove your registration

  Group Room Commands:
    !activate_switch <@user:server>  — Cast a consensus vote
    !cancel_alert <@user:server>     — (Admin) Cancel an active alert
    !help                            — Show command reference
"""

import asyncio
import logging
import os
from typing import Optional

import simplematrixbotlib as botlib
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from db.database import Database
from bot.onboarding import OnboardingManager
from bot.heartbeat import HeartbeatMonitor
from bot.verification import VerificationPipeline
from bot.consensus import ConsensusManager
from bot.release import ReleaseManager
from osint.scanner import OSINTScanner

# Load environment variables from .env file
load_dotenv()

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "./data/bot.log")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "")
BOT_USER_ID = os.getenv("MATRIX_BOT_USER_ID", "")
BOT_PASSWORD = os.getenv("MATRIX_BOT_PASSWORD", "")
GROUP_ROOM_ID = os.getenv("MATRIX_GROUP_ROOM_ID", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/wellness_bot.db")
BOT_MASTER_KEY = os.getenv("BOT_MASTER_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
CONSENSUS_THRESHOLD = int(os.getenv("CONSENSUS_THRESHOLD", "3"))
DEFAULT_THRESHOLD_H = int(os.getenv("DEFAULT_MISSING_THRESHOLD_HOURS", "72"))
HEARTBEAT_INTERVAL_MIN = int(os.getenv("HEARTBEAT_CHECK_INTERVAL_MINUTES", "60"))

HELP_TEXT = """
**Matrix Wellness Monitor — Command Reference**

**DM Commands** (send these directly to the bot in a private message):
- `!register_switch` — Begin the Dead Man's Switch registration flow.
- `!checkin` — Reset your activity timer. Use this to confirm you are safe.
- `!my_status` — View your current registration status and timer.
- `!update_emergency_data` — Replace your stored emergency data with new content.
- `!deregister` — Remove your registration and delete all stored data.

**Group Room Commands** (send these in the monitored group room):
- `!activate_switch @user:server` — Cast a consensus vote to activate a switch.
- `!cancel_alert @user:server` — (Admin) Cancel an active missing alert.
- `!help` — Show this help message.

**How it works:**
1. Register via DM. Your emergency data is encrypted immediately.
2. The bot monitors your activity. Any message you send resets your timer.
3. If you exceed your threshold, automated safety checks run.
4. If checks find nothing, the group is alerted and can vote to release your data.
5. Upon consensus, your emergency data is decrypted and posted to the group.
"""


class WellnessBot:
    """
    Top-level orchestrator for the Matrix Wellness Monitor bot.
    """

    def __init__(self):
        self._validate_config()

        # --- Database ---
        self.db = Database(DATABASE_PATH)

        # --- Matrix client ---
        self.creds = botlib.Creds(
            homeserver=HOMESERVER_URL,
            username=BOT_USER_ID,
            password=BOT_PASSWORD,
        )
        self.config = botlib.Config()
        self.config.encryption_enabled = True
        self.config.ignore_unverified_devices = True  # Allow E2EE with unverified devices
        self.bot = botlib.Bot(self.creds, self.config)

        # --- OSINT Scanner ---
        self.osint_scanner = OSINTScanner(serpapi_key=SERPAPI_KEY or None)

        # --- Sub-modules (initialised after DB is ready) ---
        self.onboarding: Optional[OnboardingManager] = None
        self.heartbeat: Optional[HeartbeatMonitor] = None
        self.verification: Optional[VerificationPipeline] = None
        self.consensus: Optional[ConsensusManager] = None
        self.release_mgr: Optional[ReleaseManager] = None

        # --- Scheduler ---
        self.scheduler = AsyncIOScheduler()

    def _validate_config(self):
        required = {
            "MATRIX_HOMESERVER_URL": HOMESERVER_URL,
            "MATRIX_BOT_USER_ID": BOT_USER_ID,
            "MATRIX_BOT_PASSWORD": BOT_PASSWORD,
            "MATRIX_GROUP_ROOM_ID": GROUP_ROOM_ID,
            "BOT_MASTER_KEY": BOT_MASTER_KEY,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Please copy .env.example to .env and fill in all required values."
            )
        if len(BOT_MASTER_KEY) < 64:
            raise EnvironmentError(
                "BOT_MASTER_KEY must be at least 64 hex characters (256 bits). "
                "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )

    # ------------------------------------------------------------------
    # Async send helpers
    # ------------------------------------------------------------------

    async def _send_group_message(self, text: str):
        """Post a message to the monitored group room."""
        try:
            await self.bot.api.send_markdown_message(GROUP_ROOM_ID, text)
        except Exception as exc:
            logger.error("Failed to send group message: %s", exc)

    async def _send_dm(self, matrix_id: str, text: str):
        """Send a direct message to a specific user."""
        try:
            # Create or retrieve a DM room with the user
            room_id = await self.bot.api.async_client.room_create(
                is_direct=True,
                invite=[matrix_id],
            )
            if hasattr(room_id, "room_id"):
                room_id = room_id.room_id
            await self.bot.api.send_markdown_message(room_id, text)
        except Exception as exc:
            logger.error("Failed to send DM to %s: %s", matrix_id, exc)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def _init_modules(self):
        """Initialise all sub-modules after the database is connected."""
        self.release_mgr = ReleaseManager(
            db=self.db,
            master_key_hex=BOT_MASTER_KEY,
            send_group_message=self._send_group_message,
        )

        self.consensus = ConsensusManager(
            db=self.db,
            consensus_threshold=CONSENSUS_THRESHOLD,
            on_consensus_reached=self.release_mgr.release,
            send_group_message=self._send_group_message,
        )

        self.verification = VerificationPipeline(
            db=self.db,
            osint_scanner=self.osint_scanner,
            on_verification_passed=self._on_verification_passed,
            on_verification_failed=self._on_verification_failed,
            send_dm=self._send_dm,
        )

        self.heartbeat = HeartbeatMonitor(
            db=self.db,
            on_user_missing=self.verification.run,
        )

        self.onboarding = OnboardingManager(
            db=self.db,
            master_key_hex=BOT_MASTER_KEY,
            default_threshold_h=DEFAULT_THRESHOLD_H,
        )

        logger.info("All sub-modules initialised.")

    async def _on_verification_passed(self, user, summary):
        logger.info("Verification passed for %s: %s", user["matrix_id"], summary)

    async def _on_verification_failed(self, user, summary):
        logger.warning(
            "Verification failed for %s — posting group alert.", user["matrix_id"]
        )
        await self.consensus.post_alert(user, summary)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _register_handlers(self):
        """Register all bot message listeners."""

        # ---- Group room: activity tracking ----
        @self.bot.listener.on_message_event
        async def on_any_message(room, message):
            """Track all messages for heartbeat monitoring."""
            sender = message.sender
            if sender == BOT_USER_ID:
                return
            if room.room_id == GROUP_ROOM_ID:
                await self.heartbeat.record_activity(sender)

        # ---- Group room: !help ----
        @self.bot.listener.on_message_event
        async def on_help(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if match.is_not_from_this_bot() and match.prefix() and match.command("help"):
                await self.bot.api.send_markdown_message(room.room_id, HELP_TEXT)

        # ---- Group room: !activate_switch ----
        @self.bot.listener.on_message_event
        async def on_activate_switch(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id == GROUP_ROOM_ID
                and match.prefix()
                and match.command("activate_switch")
            ):
                args = match.args()
                if not args:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Usage: `!activate_switch @user:server`",
                    )
                    return
                target_id = args[0].strip()
                voter_id = message.sender
                voter_display = room.user_name(voter_id) or voter_id

                response = await self.consensus.handle_activate_vote(
                    voter_matrix_id=voter_id,
                    voter_display_name=voter_display,
                    target_matrix_id=target_id,
                )
                if response:
                    await self.bot.api.send_markdown_message(room.room_id, response)

        # ---- Group room: !cancel_alert ----
        @self.bot.listener.on_message_event
        async def on_cancel_alert(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id == GROUP_ROOM_ID
                and match.prefix()
                and match.command("cancel_alert")
            ):
                args = match.args()
                if not args:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Usage: `!cancel_alert @user:server`",
                    )
                    return
                target_id = args[0].strip()
                admin_id = message.sender
                response = await self.consensus.handle_cancel_alert(admin_id, target_id)
                await self.bot.api.send_markdown_message(room.room_id, response)

        # ---- Group room: !checkin (also works in group) ----
        @self.bot.listener.on_message_event
        async def on_group_checkin(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id == GROUP_ROOM_ID
                and match.prefix()
                and match.command("checkin")
            ):
                response = await self.heartbeat.handle_checkin(message.sender)
                await self.bot.api.send_markdown_message(room.room_id, response)

        # ---- DM: !register_switch ----
        @self.bot.listener.on_message_event
        async def on_register_switch(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and match.prefix()
                and match.command("register_switch")
            ):
                sender = message.sender
                prompt = self.onboarding.start_session(sender)
                await self.bot.api.send_markdown_message(room.room_id, prompt)

        # ---- DM: !checkin ----
        @self.bot.listener.on_message_event
        async def on_dm_checkin(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and match.prefix()
                and match.command("checkin")
            ):
                response = await self.heartbeat.handle_checkin(message.sender)
                await self.bot.api.send_markdown_message(room.room_id, response)

        # ---- DM: !my_status ----
        @self.bot.listener.on_message_event
        async def on_my_status(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and match.prefix()
                and match.command("my_status")
            ):
                response = await self.heartbeat.handle_my_status(message.sender)
                await self.bot.api.send_markdown_message(room.room_id, response)

        # ---- DM: !update_emergency_data ----
        @self.bot.listener.on_message_event
        async def on_update_emergency_data(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and match.prefix()
                and match.command("update_emergency_data")
            ):
                sender = message.sender
                # Re-use onboarding but jump straight to the emergency data step
                session = self.onboarding.start_session(sender)
                # Fast-forward to emergency data step by pre-filling from DB
                user = await self.db.get_user(sender)
                profile = await self.db.get_profile(sender)
                if user and profile:
                    import json
                    onb_session = self.onboarding._sessions[sender]
                    onb_session.data["display_name"] = user.get("display_name", sender)
                    onb_session.data["location"] = profile.get("location", "")
                    onb_session.data["social_handles"] = json.loads(
                        profile.get("social_handles") or "{}"
                    )
                    onb_session.data["threshold_h"] = user.get("missing_threshold_h", 72)
                    # Advance to AWAIT_EMERGENCY_DATA step
                    from bot.onboarding import STEPS
                    onb_session.step_index = STEPS.index("AWAIT_EMERGENCY_DATA")
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Please send your new emergency data. "
                        "It will be encrypted immediately and replace your previous entry.\n\n"
                        "⚠️ Your previous emergency data will be permanently overwritten.",
                    )
                else:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "You are not registered. Please use `!register_switch` first.",
                    )

        # ---- DM: !deregister ----
        @self.bot.listener.on_message_event
        async def on_deregister(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and match.prefix()
                and match.command("deregister")
            ):
                sender = message.sender
                user = await self.db.get_user(sender)
                if not user:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "You are not registered with the Wellness Monitor.",
                    )
                    return
                # Delete all user data
                await self.db._conn.execute(
                    "DELETE FROM registered_users WHERE matrix_id = ?", (sender,)
                )
                await self.db._conn.commit()
                await self.db.log_event(
                    event_type="USER_DEREGISTERED",
                    actor_matrix_id=sender,
                )
                await self.bot.api.send_markdown_message(
                    room.room_id,
                    "Your registration and all associated data (including your encrypted "
                    "emergency data) have been permanently deleted.",
                )

        # ---- DM: Onboarding conversation (catch-all for active sessions) ----
        @self.bot.listener.on_message_event
        async def on_dm_conversation(room, message):
            if room.room_id == GROUP_ROOM_ID:
                return
            sender = message.sender
            if sender == BOT_USER_ID:
                return
            # Only handle if there's an active onboarding session
            if self.onboarding.has_session(sender):
                body = message.body
                # Skip if it's a command (already handled above)
                if body.startswith("!"):
                    return
                response = await self.onboarding.process_message(sender, body)
                await self.bot.api.send_markdown_message(room.room_id, response)

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    def _start_scheduler(self):
        """Start the APScheduler for periodic heartbeat checks."""
        self.scheduler.add_job(
            self.heartbeat.run_check,
            "interval",
            minutes=HEARTBEAT_INTERVAL_MIN,
            id="heartbeat_check",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(
            "Heartbeat scheduler started (interval: %d minutes).",
            HEARTBEAT_INTERVAL_MIN,
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self):
        """Connect to the database, initialise modules, and start the bot."""
        logger.info("=== Matrix Wellness Monitor starting up ===")

        # Connect database
        await self.db.connect()

        # Initialise sub-modules
        await self._init_modules()

        # Register message handlers
        self._register_handlers()

        # Start the periodic heartbeat scheduler
        self._start_scheduler()

        logger.info(
            "Bot configured. Connecting to %s as %s ...",
            HOMESERVER_URL,
            BOT_USER_ID,
        )

        # Start the bot (blocking)
        await self.bot.main()


def main():
    """Entry point for running the bot."""
    bot = WellnessBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as exc:
        logger.critical("Bot crashed: %s", exc, exc_info=True)
        raise


if __name__ == "__main__":
    main()
