"""
bot/bot.py
==========
Liberation Bot — Matrix Orchestrator (Agentic Phase I)

This module wires together all subsystems:
  - Matrix client (simplematrixbotlib / matrix-nio) for E2EE messaging
  - Database layer (chat history + agent queries + DMS tables)
  - Onboarding manager (Dead Man's Switch Phase 1)
  - Heartbeat monitor (Dead Man's Switch Phase 2)
  - OSINT verification pipeline (Dead Man's Switch Phase 3)
  - Consensus manager (Dead Man's Switch Phase 4)
  - Release manager (Dead Man's Switch Phase 5)
  - AgentCore (Kimi K2 via NVIDIA NIM — Phase I Agentic)
  - APScheduler for periodic heartbeat checks

Dead Man's Switch Commands (unchanged):
  DM Commands:
    !register_switch         — Begin the onboarding flow
    !checkin                 — Reset your activity timer
    !my_status               — View your current status
    !update_emergency_data   — Replace your emergency data
    !deregister              — Remove your registration

  Group Room Commands:
    !activate_switch <@user:server>  — Cast a consensus vote
    !cancel_alert <@user:server>     — (Admin) Cancel an active alert
    !help                            — Show command reference

Agentic Commands (NEW — Phase I):
  Group Room or DM:
    @bot <question>  — Ask Liberation Bot about Neurowarfare, Havana Syndrome,
                       AHIs, directed energy weapons, legal options, etc.
                       Queries the Liberation Archives (NotebookLM).
    !archives        — Show Liberation Archives topic overview.
"""

import asyncio
import json
import logging
import os
import re
import time
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
from agent import AgentCore
from agent.tools import list_liberation_archives_topics
from bot.video_room import VideoRoomHandler

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
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/liberation_bot.db")
BOT_MASTER_KEY = os.getenv("BOT_MASTER_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
CONSENSUS_THRESHOLD = int(os.getenv("CONSENSUS_THRESHOLD", "3"))
DEFAULT_THRESHOLD_H = int(os.getenv("DEFAULT_MISSING_THRESHOLD_HOURS", "72"))
HEARTBEAT_INTERVAL_MIN = int(os.getenv("HEARTBEAT_CHECK_INTERVAL_MINUTES", "60"))
VIDEO_ROOM_ID = os.getenv("MATRIX_VIDEO_ROOM_ID", "")

# Regex to detect @bot mentions (case-insensitive)
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "liberation-bot")
_BOT_MENTION_PATTERN = re.compile(
    r"@(?:bot|liberation[-_]?bot|" + re.escape(BOT_DISPLAY_NAME) + r")\b",
    re.IGNORECASE,
)

HELP_TEXT = """
**Liberation Bot — Command Reference**

**Dead Man's Switch (DM Commands):**
- `!register_switch` — Begin the Dead Man's Switch registration flow.
- `!checkin` — Reset your activity timer. Use this to confirm you are safe.
- `!my_status` — View your current registration status and timer.
- `!update_emergency_data` — Replace your stored emergency data with new content.
- `!deregister` — Remove your registration and delete all stored data.

**Dead Man's Switch (Group Room Commands):**
- `!activate_switch @user:server` — Cast a consensus vote to activate a switch.
- `!cancel_alert @user:server` — (Admin) Cancel an active missing alert.

**Agentic AI (Group Room or DM):**
- `@bot <question>` — Ask Liberation Bot about Neurowarfare, Havana Syndrome,
  AHIs, directed energy weapons, legal options, resources, and more.
  Queries the **Liberation Archives** knowledge base for grounded answers.
- `!archives` — Show Liberation Archives topic overview.
- `!help` — Show this help message.

**Examples:**
- `@bot What are the symptoms of Havana Syndrome?`
- `@bot What legal options do AHI victims have in the US?`
- `@bot Can you summarize the latest research on directed energy weapons?`

**Video Planning Room (Video Planning and Generation room only):**
- `!video_start` — Begin a new video planning session. Liberation Bot will lead a dialogue with the group, ask questions, and build the prompts automatically.
- `!video_styles` — List all available visual styles and saved favourites.
- `!video_save_style <name> [notes]` — Save the current session's style as a reusable named favourite.
- `!video_preview` — Show the current prompt preview at any time.
- `!video_revise <notes>` — Ask the bot to revise the prompts based on your feedback.
- `!video_confirm` — Confirm the prompts and start video generation (any group member).
- `!video_cancel` — Cancel the current session.
- `!video_history` — Show recent completed videos.
"""


class LiberationBot:
    """
    Top-level orchestrator for Liberation Bot (Agentic Phase I).
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

        # --- Agentic Core (Phase I) ---
        self.agent = AgentCore()

        # --- Sub-modules (initialised after DB is ready) ---
        self.onboarding: Optional[OnboardingManager] = None
        self.heartbeat: Optional[HeartbeatMonitor] = None
        self.verification: Optional[VerificationPipeline] = None
        self.consensus: Optional[ConsensusManager] = None
        self.release_mgr: Optional[ReleaseManager] = None
        self.video_handler: Optional[VideoRoomHandler] = None

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

        # --- Video Planning Room Handler ---
        if VIDEO_ROOM_ID:
            self.video_handler = VideoRoomHandler(
                db=self.db,
                bot_api=self.bot.api,
            )
            logger.info("VideoRoomHandler initialised for room: %s", VIDEO_ROOM_ID)
        else:
            logger.warning(
                "MATRIX_VIDEO_ROOM_ID not set — video planning room disabled. "
                "Add it to .env to enable the video workflow."
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
    # Agentic helpers
    # ------------------------------------------------------------------

    def _is_bot_mention(self, content: str) -> bool:
        """Return True if the message content mentions the bot."""
        return bool(_BOT_MENTION_PATTERN.search(content))

    def _extract_query(self, content: str) -> str:
        """Strip the @bot mention prefix and return the clean query."""
        query = _BOT_MENTION_PATTERN.sub("", content).strip()
        query = re.sub(r"^[,:\s]+", "", query).strip()
        return query

    async def _send_room_message(self, room_id: str, text: str):
        """Send a message to any room (group or DM)."""
        try:
            await self.bot.api.send_markdown_message(room_id, text)
        except Exception as exc:
            logger.error("Failed to send message to room %s: %s", room_id, exc)

    async def _handle_agent_query(self, room, message):
        """
        Handle a natural language query directed at the bot.
        Saves the message to chat history, calls the agent, and responds.
        """
        sender = message.sender
        room_id = room.room_id
        content = message.body if hasattr(message, "body") else str(message)
        user_query = self._extract_query(content)

        if not user_query:
            await self._send_room_message(
                room_id,
                "Hi! I'm Liberation Bot. Ask me anything about Neurowarfare, "
                "Havana Syndrome, or AHIs. For example: "
                "`@bot What are the symptoms of Havana Syndrome?`",
            )
            return

        logger.info(
            "Agent query from %s in %s: %s", sender, room_id, user_query[:100]
        )

        # Acknowledge the query
        await self._send_room_message(room_id, "🔍 Searching the Liberation Archives...")

        # Fetch recent chat history for context
        recent_messages = await self.db.get_recent_messages(room_id, limit=20)

        # Generate the agent response
        result = await self.agent.generate_response(
            user_query=user_query,
            room_id=room_id,
            sender_id=sender,
            recent_messages=recent_messages,
        )

        # Send the response
        response_text = result["response"]
        if result.get("notebooklm_response"):
            response_text += "\n\n*— Sourced from the Liberation Archives*"

        await self._send_room_message(room_id, response_text)

        # Log the interaction to the knowledge base
        await self.db.log_agent_query(
            room_id=room_id,
            user_matrix_id=sender,
            user_query=user_query,
            agent_response=result["response"],
            notebooklm_query=result.get("notebooklm_query"),
            notebooklm_response=result.get("notebooklm_response"),
            tool_calls_made=json.dumps(result.get("tool_calls_made", [])),
            latency_ms=result.get("latency_ms"),
        )
        await self.db.log_event(
            event_type="AGENT_QUERY",
            actor_matrix_id=sender,
            note=f"Query: {user_query[:100]} | Tools: {result.get('tool_calls_made', [])}",
        )

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _register_handlers(self):
        """Register all bot message listeners."""

        # ---- ALL messages: save to chat history + heartbeat tracking ----
        @self.bot.listener.on_message_event
        async def on_any_message(room, message):
            """
            Track ALL messages for:
              1. Chat history memory (agent context window)
              2. Heartbeat monitoring (Dead Man's Switch)
            """
            sender = message.sender
            if sender == BOT_USER_ID:
                return

            # Extract message content
            content = ""
            if hasattr(message, "body"):
                content = message.body

            # Save to chat history (all rooms)
            event_id = getattr(message, "event_id", None) or str(time.time())
            timestamp_ts = getattr(message, "server_timestamp", None)
            timestamp_ts = (timestamp_ts / 1000.0) if timestamp_ts else time.time()

            await self.db.save_message(
                event_id=event_id,
                room_id=room.room_id,
                sender_id=sender,
                content=content,
                timestamp_ts=timestamp_ts,
                sender_display_name=(
                    room.user_name(sender) if hasattr(room, "user_name") else None
                ),
            )

            # Heartbeat tracking for registered users in the group room
            if room.room_id == GROUP_ROOM_ID:
                await self.heartbeat.record_activity(sender)

        # ---- Group room: !help ----
        @self.bot.listener.on_message_event
        async def on_help(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if match.is_not_from_this_bot() and match.prefix() and match.command("help"):
                await self.bot.api.send_markdown_message(room.room_id, HELP_TEXT)

        # ---- Any room: !archives — Liberation Archives overview ----
        @self.bot.listener.on_message_event
        async def on_archives(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if match.is_not_from_this_bot() and match.prefix() and match.command("archives"):
                await self._send_room_message(room.room_id, "🔍 Fetching Liberation Archives overview...")
                overview = await list_liberation_archives_topics()
                await self._send_room_message(room.room_id, overview)

        # ---- Any room: @bot <query> — agentic AI response ----
        @self.bot.listener.on_message_event
        async def on_agent_mention(room, message):
            if message.sender == BOT_USER_ID:
                return
            content = message.body if hasattr(message, "body") else ""
            if self._is_bot_mention(content):
                await self._handle_agent_query(room, message)

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

        # ---- Video Planning Room: all !video_* commands + brainstorm messages ----
        @self.bot.listener.on_message_event
        async def on_video_room_message(room, message):
            if not VIDEO_ROOM_ID:
                return
            if room.room_id != VIDEO_ROOM_ID:
                return
            if message.sender == BOT_USER_ID:
                return
            if self.video_handler:
                await self.video_handler.handle_message(room, message)

        # ---- DM: Onboarding conversation (catch-all for active sessions) ----
        @self.bot.listener.on_message_event
        async def on_dm_conversation(room, message):
            if room.room_id == GROUP_ROOM_ID:
                return
            if room.room_id == VIDEO_ROOM_ID:
                return  # Video room handled above
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
        logger.info("=== Liberation Bot (Agentic Phase I) starting up ===")

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
    bot = LiberationBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as exc:
        logger.critical("Bot crashed: %s", exc, exc_info=True)
        raise


if __name__ == "__main__":
    main()
