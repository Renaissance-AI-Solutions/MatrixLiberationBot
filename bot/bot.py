"""
bot/bot.py
==========
Liberation Bot — Matrix Orchestrator (Agentic Phase I)

This module wires together all subsystems:
  - Matrix client (simplematrixbotlib / matrix-nio) for E2EE messaging
  - Database layer (chat history + agent queries + DMS tables + dream memory)
  - Onboarding manager (Dead Man's Switch Phase 1)
  - Heartbeat monitor (Dead Man's Switch Phase 2)
  - OSINT verification pipeline (Dead Man's Switch Phase 3)
  - Consensus manager (Dead Man's Switch Phase 4)
  - Release manager (Dead Man's Switch Phase 5)
  - AgentCore (Kimi K2 via NVIDIA NIM — Phase I Agentic)
  - DreamEngine (nightly memory consolidation — fires at 03:00 UTC)
  - APScheduler for periodic heartbeat checks and Dream cycles

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

Agentic Commands (Phase I):
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
from agent.dreamer import DreamEngine
from agent.tools import list_liberation_archives_topics
from bot.video_room import VideoRoomHandler
from bot.foia_session import FOIASessionManager, FOIASessionState
from bot.foia_deadline_monitor import FOIADeadlineMonitor
from agent.foia_dialogue import FOIADialogueAgent
from agent.foia_appeal_agent import FOIAAppealAgent
from agent.tools.foia_jurisdictions import (
    format_jurisdiction_summary,
    format_federal_agencies_summary,
    list_jurisdiction_codes,
)
from agent.tools.web_search import run_watched_topic_scan

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

# Dream cycle schedule: hour and minute (UTC) when the Dream Engine runs
DREAM_HOUR_UTC = int(os.getenv("DREAM_HOUR_UTC", "3"))    # 03:00 UTC default
DREAM_MINUTE_UTC = int(os.getenv("DREAM_MINUTE_UTC", "0"))

# FOIA deadline reminder check interval (minutes)
FOIA_DEADLINE_CHECK_INTERVAL_MIN = int(os.getenv("FOIA_DEADLINE_CHECK_INTERVAL_MINUTES", "360"))  # 6 hours

# Watched topic scan interval (minutes) — 0 to disable
WATCHED_TOPIC_SCAN_INTERVAL_MIN = int(os.getenv("WATCHED_TOPIC_SCAN_INTERVAL_MINUTES", "720"))  # 12 hours

# ---------------------------------------------------------------------------
# Rate limiting — Layer 2: per-user cooldown
# ---------------------------------------------------------------------------
# Minimum number of seconds a user must wait between @bot queries.
# Queries arriving within this window are rejected with a polite message
# rather than consuming an API slot. This prevents a single user from
# monopolising the global concurrency semaphore and starving others.
# Set to 0 to disable per-user cooldown (not recommended on free-tier keys).
AGENT_USER_COOLDOWN_S = float(os.getenv("AGENT_USER_COOLDOWN_S", "30"))

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

**FOIA Request Generator (DM Commands):**
- `!foia_start` — Begin a new FOIA/public records request drafting session.
  Liberation Bot will guide you through the process step by step.
- `!foia_jurisdictions` — List all supported jurisdictions (Federal + all 50 states).
- `!foia_agencies` — List recommended federal agencies for AHI/Neurowarfare requests.
- `!foia_preview` — Re-show your current draft letter at any time.
- `!foia_revise <notes>` — Ask the bot to revise the draft based on your feedback.
- `!foia_confirm` — Accept the draft and receive submission instructions.
- `!foia_cancel` — Cancel the current drafting session.
- `!foia_history` — Show your past finalized FOIA requests.
- `!foia_submit <id>` — Mark a finalized request as physically submitted and start deadline tracking.
- `!foia_status <id> <status>` — Update a request status (`RESPONDED`, `APPEALED`, `CLOSED`).
- `!foia_deadlines` — View all submitted requests with their statutory response deadlines.
- `!foia_appeal <id>` — Draft an appeal letter for a denied or overdue request.

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


# ---------------------------------------------------------------------------
# FOIA helper — applies LLM-generated draft fields to a session object
# ---------------------------------------------------------------------------

def _apply_draft_to_session(session, draft: dict) -> None:
    """
    Write all fields from a submit_foia_draft tool call result into the
    FOIASession dataclass. Called from both the confirm and revise handlers.
    """
    session.jurisdiction_code       = draft.get("jurisdiction_code") or session.jurisdiction_code
    session.target_agency           = draft.get("target_agency") or session.target_agency
    session.subject_summary         = draft.get("subject_summary") or session.subject_summary
    session.date_range              = draft.get("date_range") or session.date_range
    session.keywords                = draft.get("keywords") or session.keywords
    session.requester_name          = draft.get("requester_name") or session.requester_name
    session.requester_contact       = draft.get("requester_contact") or session.requester_contact
    session.fee_waiver_requested    = bool(draft.get("fee_waiver_requested", session.fee_waiver_requested))
    session.fee_waiver_justification = (
        draft.get("fee_waiver_justification") or session.fee_waiver_justification
    )
    session.expedited_requested     = bool(draft.get("expedited_requested", session.expedited_requested))
    session.expedited_justification = (
        draft.get("expedited_justification") or session.expedited_justification
    )
    session.draft_letter            = draft.get("draft_letter") or session.draft_letter


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
        self.agent = AgentCore(db=self.db)

        # --- Dream Engine (nightly memory consolidation) ---
        # Initialised after DB is connected (in _init_modules)
        self.dream_engine: Optional[DreamEngine] = None

        # --- Sub-modules (initialised after DB is ready) ---
        self.onboarding: Optional[OnboardingManager] = None
        self.heartbeat: Optional[HeartbeatMonitor] = None
        self.verification: Optional[VerificationPipeline] = None
        self.consensus: Optional[ConsensusManager] = None
        self.release_mgr: Optional[ReleaseManager] = None
        self.video_handler: Optional[VideoRoomHandler] = None

        # --- Per-user cooldown tracking (in-memory, no DB needed) ---
        # Maps matrix_id -> monotonic timestamp of their last successful query.
        # Cleared on bot restart (intentional — cooldowns don't need to survive restarts).
        self._user_last_query: dict[str, float] = {}

        # --- FOIA subsystem (initialised in _init_modules after DB is ready) ---
        self.foia_manager: Optional[FOIASessionManager] = None
        self.foia_agent: Optional[FOIADialogueAgent] = None
        self.foia_appeal_agent: Optional[FOIAAppealAgent] = None
        self.foia_deadline_monitor: Optional[FOIADeadlineMonitor] = None

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

        # --- Dream Engine ---
        self.dream_engine = DreamEngine(db=self.db)
        logger.info(
            "DreamEngine initialised. Scheduled for %02d:%02d UTC daily.",
            DREAM_HOUR_UTC, DREAM_MINUTE_UTC,
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

        # --- FOIA Session Manager, Dialogue Agent, Appeal Agent, and Deadline Monitor ---
        self.foia_manager = FOIASessionManager()
        self.foia_agent = FOIADialogueAgent()
        self.foia_appeal_agent = FOIAAppealAgent()
        self.foia_deadline_monitor = FOIADeadlineMonitor(
            db=self.db,
            on_reminder=self._send_dm,
        )
        logger.info("FOIA session manager, dialogue agent, appeal agent, and deadline monitor initialised.")

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

        Rate limiting (three-layer defence):
          Layer 2 — Per-user cooldown: reject queries arriving within
            AGENT_USER_COOLDOWN_S seconds of the user's last successful query.
            This prevents a single user from monopolising the global semaphore.
          Layer 1 — Global concurrency semaphore: enforced inside AgentCore.
            At most AGENT_MAX_CONCURRENT_CALLS LLM requests in-flight at once.
          Layer 3 — 429 retry-with-backoff: enforced inside AgentCore.
            Transient NVIDIA throttle responses are retried automatically.

        Enriches the agent context with:
          1. Long-term user memories (from Dream consolidation)
          2. Operational memories relevant to this room
          3. Last 30 messages of room chat history (with compaction)

        Then calls the agent and logs the interaction.
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

        # ------------------------------------------------------------------
        # Layer 2: Per-user cooldown check
        # ------------------------------------------------------------------
        if AGENT_USER_COOLDOWN_S > 0:
            now = time.monotonic()
            last_query_ts = self._user_last_query.get(sender, 0.0)
            elapsed = now - last_query_ts
            if elapsed < AGENT_USER_COOLDOWN_S:
                remaining = int(AGENT_USER_COOLDOWN_S - elapsed) + 1
                logger.info(
                    "Agent query from %s rejected: cooldown active (%.1fs remaining).",
                    sender, AGENT_USER_COOLDOWN_S - elapsed,
                )
                await self._send_room_message(
                    room_id,
                    f"⏱️ Please wait **{remaining} seconds** before sending another query. "
                    f"This keeps the AI available for all members.",
                )
                return

        logger.info(
            "Agent query from %s in %s: %s", sender, room_id, user_query[:100]
        )

        # Acknowledge the query
        await self._send_room_message(room_id, "🔍 Searching the Liberation Archives...")

        # --- Fetch context: short-term chat history (last 30 messages) ---
        # Long-term memories are NO LONGER pre-fetched here. The agent calls
        # search_memories on demand via tool use, and writes new memories via
        # upsert_memory. Both tools are scoped to sender/room_id by AgentCore.
        from agent.core import CONTEXT_WINDOW_MESSAGES
        recent_messages = await self.db.get_recent_messages(
            room_id, limit=CONTEXT_WINDOW_MESSAGES
        )

        # Generate the agent response (memory tools available via self.agent.db)
        result = await self.agent.generate_response(
            user_query=user_query,
            room_id=room_id,
            sender_id=sender,
            recent_messages=recent_messages,
        )

        # ------------------------------------------------------------------
        # Record the cooldown timestamp only on a successful (non-error) call.
        # If the call failed due to rate limiting or an API error, don't
        # penalise the user — they should be free to retry immediately.
        # ------------------------------------------------------------------
        if not result.get("error"):
            self._user_last_query[sender] = time.monotonic()

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
            note=(
                f"Query: {user_query[:100]} | "
                f"Tools: {result.get('tool_calls_made', [])} | "
                f"RateLimited: {result.get('rate_limited', False)}"
            ),
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
              1. Chat history memory (agent context window + Dream Engine)
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
                    import json as _json
                    onb_session = self.onboarding._sessions[sender]
                    onb_session.data["display_name"] = user.get("display_name", sender)
                    onb_session.data["location"] = profile.get("location", "")
                    onb_session.data["social_handles"] = _json.loads(
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

        # ====================================================================
        # FOIA Request Generator — DM Commands
        # ====================================================================

        # ---- DM: !foia_start ----
        @self.bot.listener.on_message_event
        async def on_foia_start(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_start")
            ):
                sender = message.sender
                ok, result = self.foia_manager.start_session(room.room_id, sender)
                if not ok:
                    await self.bot.api.send_markdown_message(room.room_id, result)
                    return
                session = result
                # Record session start time for audit log
                session._started_ts = time.time()
                # Get the opening message from the LLM agent
                dialogue_result = await self.foia_agent.get_opening_message(session)
                await self.bot.api.send_markdown_message(room.room_id, dialogue_result.reply)
                await self.db.log_event(
                    event_type="FOIA_SESSION_STARTED",
                    actor_matrix_id=sender,
                )

        # ---- DM: !foia_jurisdictions ----
        @self.bot.listener.on_message_event
        async def on_foia_jurisdictions(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_jurisdictions")
            ):
                codes = list_jurisdiction_codes()
                lines = [
                    "## Supported Jurisdictions",
                    "",
                    "Use these codes when asked for your jurisdiction during a `!foia_start` session.",
                    "",
                    "| Code | Jurisdiction |",
                    "|---|---|",
                ]
                for code in codes:
                    from agent.tools.foia_jurisdictions import JURISDICTIONS
                    name = JURISDICTIONS[code]["name"]
                    lines.append(f"| `{code}` | {name} |")
                lines += [
                    "",
                    "Use `!foia_agencies` to see recommended federal agencies for AHI requests.",
                ]
                await self.bot.api.send_markdown_message(room.room_id, "\n".join(lines))

        # ---- DM: !foia_agencies ----
        @self.bot.listener.on_message_event
        async def on_foia_agencies(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_agencies")
            ):
                summary = format_federal_agencies_summary()
                await self.bot.api.send_markdown_message(room.room_id, summary)

        # ---- DM: !foia_preview ----
        @self.bot.listener.on_message_event
        async def on_foia_preview(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_preview")
            ):
                sender = message.sender
                session = self.foia_manager.get_session(sender)
                if not session:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "No active FOIA drafting session. Use `!foia_start` to begin one.",
                    )
                    return
                await self.bot.api.send_markdown_message(
                    room.room_id, session.preview_text()
                )

        # ---- DM: !foia_revise <notes> ----
        @self.bot.listener.on_message_event
        async def on_foia_revise(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_revise")
            ):
                sender = message.sender
                session = self.foia_manager.get_session(sender)
                if not session:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "No active FOIA drafting session. Use `!foia_start` to begin one.",
                    )
                    return
                if session.state not in (
                    FOIASessionState.REVIEW, FOIASessionState.DRAFTING
                ):
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "⚠️ Your session is not in a state that can be revised. "
                        "Use `!foia_preview` to check the current status.",
                    )
                    return
                args = match.args()
                revision_notes = " ".join(args).strip() if args else ""
                if not revision_notes:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Please provide revision notes. Example: `!foia_revise Please add a fee waiver request`",
                    )
                    return
                # Reopen for revision if in REVIEW state
                self.foia_manager.reopen_for_revision(sender)
                await self.bot.api.send_markdown_message(
                    room.room_id, "✏️ Revising your draft..."
                )
                dialogue_result = await self.foia_agent.process_revision(
                    session, sender, revision_notes
                )
                # If a new draft was submitted, update the session
                if dialogue_result.draft:
                    _apply_draft_to_session(session, dialogue_result.draft)
                    self.foia_manager.transition_to_review(sender)
                await self.bot.api.send_markdown_message(
                    room.room_id, dialogue_result.reply
                )
                if dialogue_result.draft:
                    await self.bot.api.send_markdown_message(
                        room.room_id, session.preview_text()
                    )

        # ---- DM: !foia_confirm ----
        @self.bot.listener.on_message_event
        async def on_foia_confirm(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_confirm")
            ):
                sender = message.sender
                session = self.foia_manager.get_session(sender)
                if not session:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "No active FOIA drafting session. Use `!foia_start` to begin one.",
                    )
                    return
                if session.state != FOIASessionState.REVIEW:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "⚠️ Your draft is not ready for confirmation yet. "
                        "Continue the conversation until the bot presents the draft for review.",
                    )
                    return
                if not session.is_complete:
                    missing = ", ".join(session.missing_fields)
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        f"⚠️ The draft is still missing: **{missing}**. "
                        "Please continue the conversation to fill in these details.",
                    )
                    return
                # Finalize the session
                finalized = self.foia_manager.finalize_session(sender)
                if not finalized:
                    await self.bot.api.send_markdown_message(
                        room.room_id, "⚠️ Could not finalize the session. Please try again."
                    )
                    return
                # Save to DB
                request_id = await self.db.save_foia_request(
                    sender_id=sender,
                    room_id=room.room_id,
                    jurisdiction_code=session.jurisdiction_code,
                    target_agency=session.target_agency,
                    subject_summary=session.subject_summary,
                    requester_name=session.requester_name,
                    requester_contact=session.requester_contact,
                    draft_letter=session.draft_letter,
                    date_range=session.date_range,
                    keywords=session.keywords,
                    fee_waiver_requested=session.fee_waiver_requested,
                    fee_waiver_justification=session.fee_waiver_justification,
                    expedited_requested=session.expedited_requested,
                    expedited_justification=session.expedited_justification,
                    confirmed_ts=(
                        session.confirmed_at.timestamp()
                        if session.confirmed_at else None
                    ),
                )
                session._db_id = request_id
                # Log the session audit record
                await self.db.log_foia_session(
                    sender_id=sender,
                    room_id=room.room_id,
                    started_ts=getattr(session, "_started_ts", time.time()),
                    final_state="FINALIZED",
                    foia_request_id=request_id,
                )
                await self.db.log_event(
                    event_type="FOIA_REQUEST_FINALIZED",
                    actor_matrix_id=sender,
                    note=f"Agency: {session.target_agency} | Jurisdiction: {session.jurisdiction_code}",
                )
                # Send the finalized letter and submission instructions
                await self.bot.api.send_markdown_message(
                    room.room_id,
                    f"```\n{session.draft_letter}\n```",
                )
                await self.bot.api.send_markdown_message(
                    room.room_id, session.submission_instructions()
                )

        # ---- DM: !foia_cancel ----
        @self.bot.listener.on_message_event
        async def on_foia_cancel(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_cancel")
            ):
                sender = message.sender
                session = self.foia_manager.get_session(sender)
                response = self.foia_manager.cancel_session(sender)
                if session:
                    await self.db.log_foia_session(
                        sender_id=sender,
                        room_id=room.room_id,
                        started_ts=getattr(session, "_started_ts", time.time()),
                        final_state="CANCELLED",
                    )
                    await self.db.log_event(
                        event_type="FOIA_SESSION_CANCELLED",
                        actor_matrix_id=sender,
                    )
                await self.bot.api.send_markdown_message(room.room_id, response)

        # ---- DM: !foia_history ----
        @self.bot.listener.on_message_event
        async def on_foia_history(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_history")
            ):
                sender = message.sender
                requests = await self.db.get_foia_requests_for_user(sender, limit=10)
                if not requests:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "You have no finalized FOIA requests yet. "
                        "Use `!foia_start` to draft your first one.",
                    )
                    return
                lines = [
                    "## Your FOIA Request History",
                    "",
                    "| # | Date | Jurisdiction | Agency | Subject | Status |",
                    "|---|---|---|---|---|---|",
                ]
                from datetime import datetime, timezone as _tz
                for req in requests:
                    date_str = datetime.fromtimestamp(
                        req["created_ts"], tz=_tz.utc
                    ).strftime("%Y-%m-%d")
                    subject_short = (req["subject_summary"] or "")[:40]
                    if len(req["subject_summary"] or "") > 40:
                        subject_short += "..."
                    lines.append(
                        f"| {req['id']} | {date_str} | `{req['jurisdiction_code']}` "
                        f"| {req['target_agency']} | {subject_short} | {req['status']} |"
                    )
                lines += [
                    "",
                    "Use `@bot` to ask Liberation Bot about the status of your requests "
                    "or next steps for any of the above.",
                ]
                await self.bot.api.send_markdown_message(room.room_id, "\n".join(lines))

        # ---- DM: !foia_submit <request_id> ----
        @self.bot.listener.on_message_event
        async def on_foia_submit(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_submit")
            ):
                sender = message.sender
                args = match.args()
                if not args or not args[0].isdigit():
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Usage: `!foia_submit <request_id>`\n"
                        "Example: `!foia_submit 3`\n\n"
                        "Use `!foia_history` to find your request IDs.",
                    )
                    return
                request_id = int(args[0])
                req = await self.db.get_foia_request_by_id(request_id, sender)
                if not req:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        f"Request #{request_id} not found or does not belong to you.",
                    )
                    return
                if req["status"] not in ("FINALIZED", "DRAFT"):
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        f"Request #{request_id} has status **{req['status']}** and cannot "
                        f"be marked as submitted again.",
                    )
                    return
                # Calculate the expected response deadline from jurisdiction data
                from agent.tools.foia_jurisdictions import calculate_foia_deadline
                from datetime import datetime, timezone as _tz
                submitted_ts = time.time()
                expected_dt = calculate_foia_deadline(
                    req["jurisdiction_code"], submitted_ts
                )
                expected_ts = expected_dt.timestamp()
                ok = await self.db.mark_foia_submitted(
                    request_id=request_id,
                    sender_id=sender,
                    submitted_ts=submitted_ts,
                    expected_response_date=expected_ts,
                )
                if not ok:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Failed to update request status. Please try again.",
                    )
                    return
                deadline_str = expected_dt.strftime("%Y-%m-%d")
                await self.db.log_event(
                    event_type="FOIA_REQUEST_SUBMITTED",
                    actor_matrix_id=sender,
                    note=f"Request #{request_id} | Agency: {req['target_agency']} | Deadline: {deadline_str}",
                )
                await self.bot.api.send_markdown_message(
                    room.room_id,
                    f"**Request #{request_id} marked as submitted.**\n\n"
                    f"Agency: **{req['target_agency']}** ({req['jurisdiction_code']})\n"
                    f"Statutory response deadline: **{deadline_str}**\n\n"
                    f"Liberation Bot will send you a reminder if the deadline passes "
                    f"without a response. Use `!foia_status {request_id}` to update "
                    f"the status when you receive a response.",
                )

        # ---- DM: !foia_status <request_id> <new_status> ----
        @self.bot.listener.on_message_event
        async def on_foia_status(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_status")
            ):
                sender = message.sender
                args = match.args()
                VALID_STATUSES = ["SUBMITTED", "RESPONDED", "APPEALED", "CLOSED"]
                if len(args) < 2 or not args[0].isdigit() or args[1].upper() not in VALID_STATUSES:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Usage: `!foia_status <request_id> <status>`\n"
                        f"Valid statuses: {', '.join(f'`{s}`' for s in VALID_STATUSES)}\n\n"
                        "Example: `!foia_status 3 RESPONDED`",
                    )
                    return
                request_id = int(args[0])
                new_status = args[1].upper()
                req = await self.db.get_foia_request_by_id(request_id, sender)
                if not req:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        f"Request #{request_id} not found or does not belong to you.",
                    )
                    return
                ok = await self.db.update_foia_request_status(
                    request_id=request_id,
                    sender_id=sender,
                    status=new_status,
                )
                if not ok:
                    await self.bot.api.send_markdown_message(
                        room.room_id, "Failed to update status. Please try again."
                    )
                    return
                await self.db.log_event(
                    event_type="FOIA_STATUS_UPDATED",
                    actor_matrix_id=sender,
                    note=f"Request #{request_id} -> {new_status}",
                )
                status_notes = {
                    "RESPONDED": "Great news! Log the response details for your records. "
                                 "If the response was a denial, use `!foia_appeal {request_id}` "
                                 "to draft an appeal letter.",
                    "APPEALED":  "Your appeal has been logged. Liberation Bot will track the "
                                 "appeal timeline.",
                    "CLOSED":    "This request has been closed and archived.",
                    "SUBMITTED": "Status reset to SUBMITTED.",
                }
                note = status_notes.get(new_status, "")
                await self.bot.api.send_markdown_message(
                    room.room_id,
                    f"**Request #{request_id} status updated to `{new_status}`.**\n\n{note}",
                )

        # ---- DM: !foia_deadlines ----
        @self.bot.listener.on_message_event
        async def on_foia_deadlines(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_deadlines")
            ):
                sender = message.sender
                requests = await self.db.get_foia_requests_with_deadlines(sender)
                if not requests:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "You have no submitted FOIA requests with tracked deadlines.\n\n"
                        "Use `!foia_submit <id>` after submitting a request to start "
                        "deadline tracking.",
                    )
                    return
                from datetime import datetime, timezone as _tz
                now_ts = time.time()
                lines = [
                    "## FOIA Deadline Tracker",
                    "",
                    "| # | Agency | Jurisdiction | Deadline | Days Remaining |",
                    "|---|---|---|---|---|",
                ]
                for req in requests:
                    deadline_str = datetime.fromtimestamp(
                        req["expected_response_date"], tz=_tz.utc
                    ).strftime("%Y-%m-%d")
                    days_left = (req["expected_response_date"] - now_ts) / 86400
                    if days_left < 0:
                        days_label = f"**OVERDUE ({abs(int(days_left))}d)**"
                    elif days_left < 2:
                        days_label = f"**{days_left:.1f}d (URGENT)**"
                    else:
                        days_label = f"{int(days_left)}d"
                    lines.append(
                        f"| {req['id']} | {req['target_agency']} "
                        f"| `{req['jurisdiction_code']}` | {deadline_str} | {days_label} |"
                    )
                lines += [
                    "",
                    "Use `!foia_status <id> RESPONDED` when you receive a response, "
                    "or `!foia_appeal <id>` to draft an appeal for overdue requests.",
                ]
                await self.bot.api.send_markdown_message(room.room_id, "\n".join(lines))

        # ---- DM: !foia_appeal <request_id> [denial_reason] ----
        @self.bot.listener.on_message_event
        async def on_foia_appeal(room, message):
            match = botlib.MessageMatch(room, message, self.bot, prefix="!")
            if (
                match.is_not_from_this_bot()
                and room.room_id != GROUP_ROOM_ID
                and room.room_id != VIDEO_ROOM_ID
                and match.prefix()
                and match.command("foia_appeal")
            ):
                sender = message.sender
                args = match.args()
                if not args or not args[0].isdigit():
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        "Usage: `!foia_appeal <request_id> [denial reason]`\n\n"
                        "**Examples:**\n"
                        "- `!foia_appeal 3` — Appeal based on no response (constructive denial)\n"
                        "- `!foia_appeal 3 Exemption 5 deliberative process` — Appeal a specific denial\n\n"
                        "Use `!foia_history` to find your request IDs.",
                    )
                    return
                request_id = int(args[0])
                denial_reason = " ".join(args[1:]).strip() if len(args) > 1 else None

                req = await self.db.get_foia_request_by_id(request_id, sender)
                if not req:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        f"Request #{request_id} not found or does not belong to you.",
                    )
                    return
                if req["status"] not in ("SUBMITTED", "RESPONDED", "APPEALED", "FINALIZED"):
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        f"Request #{request_id} has status **{req['status']}**. "
                        f"You can only appeal requests that have been submitted or responded to.",
                    )
                    return

                await self.bot.api.send_markdown_message(
                    room.room_id,
                    f"Drafting appeal letter for Request #{request_id}...\n"
                    f"Agency: **{req['target_agency']}** | Jurisdiction: `{req['jurisdiction_code']}`",
                )

                result = await self.foia_appeal_agent.draft_appeal(
                    original_request=req,
                    denial_reason=denial_reason,
                )

                if not result.success:
                    await self.bot.api.send_markdown_message(
                        room.room_id,
                        f"Failed to draft appeal letter: {result.error}\n"
                        f"Please try again or contact support.",
                    )
                    return

                # Save the appeal letter to the DB
                await self.db.save_foia_appeal(
                    request_id=request_id,
                    sender_id=sender,
                    appeal_letter=result.appeal_letter,
                )
                await self.db.log_event(
                    event_type="FOIA_APPEAL_DRAFTED",
                    actor_matrix_id=sender,
                    note=f"Request #{request_id} | Agency: {req['target_agency']} | Basis: {result.legal_basis}",
                )

                # Send the appeal letter
                await self.bot.api.send_markdown_message(
                    room.room_id,
                    f"**Appeal Letter for Request #{request_id}**\n"
                    f"Legal basis: _{result.legal_basis}_\n"
                    f"Appeal authority: _{result.appeal_authority}_\n"
                    f"Estimated response deadline: {result.estimated_appeal_deadline_days} business days",
                )
                await self.bot.api.send_markdown_message(
                    room.room_id,
                    f"```\n{result.appeal_letter}\n```",
                )

                # Send key arguments as a follow-up
                if result.key_arguments:
                    arg_lines = ["**Key Arguments in This Appeal:**", ""]
                    for i, arg in enumerate(result.key_arguments, 1):
                        arg_lines.append(f"{i}. {arg}")
                    arg_lines += [
                        "",
                        f"This appeal has been saved to your FOIA history (Request #{request_id}, "
                        f"status: APPEALED). Use `!foia_status {request_id} CLOSED` to close "
                        f"this request once resolved.",
                    ]
                    await self.bot.api.send_markdown_message(
                        room.room_id, "\n".join(arg_lines)
                    )

        # ====================================================================
        # End FOIA Commands
        # ====================================================================

        # ---- DM: Onboarding + FOIA conversation (catch-all for active sessions) ----
        @self.bot.listener.on_message_event
        async def on_dm_conversation(room, message):
            if room.room_id == GROUP_ROOM_ID:
                return
            if room.room_id == VIDEO_ROOM_ID:
                return  # Video room handled above
            sender = message.sender
            if sender == BOT_USER_ID:
                return
            body = message.body if hasattr(message, "body") else ""
            # Skip commands — they are handled by dedicated handlers above
            if body.startswith("!"):
                return

            # --- FOIA session: route free-text messages to the dialogue agent ---
            if self.foia_manager and self.foia_manager.has_active_session(sender):
                session = self.foia_manager.get_session(sender)
                if session and session.state in (
                    FOIASessionState.INTAKE, FOIASessionState.DRAFTING
                ):
                    dialogue_result = await self.foia_agent.process_message(
                        session, sender, body
                    )
                    # If the LLM submitted a draft, apply it and transition to REVIEW
                    if dialogue_result.draft:
                        _apply_draft_to_session(session, dialogue_result.draft)
                        self.foia_manager.transition_to_review(sender)
                    await self.bot.api.send_markdown_message(
                        room.room_id, dialogue_result.reply
                    )
                    # If a draft was just submitted, also show the preview
                    if dialogue_result.draft:
                        await self.bot.api.send_markdown_message(
                            room.room_id, session.preview_text()
                        )
                    return  # Don't fall through to onboarding

            # --- Onboarding session: route free-text messages to onboarding ---
            if self.onboarding.has_session(sender):
                response = await self.onboarding.process_message(sender, body)
                await self.bot.api.send_markdown_message(room.room_id, response)

    # ------------------------------------------------------------------
    # Watched Topic Scanner
    # ------------------------------------------------------------------

    async def _run_watched_topic_scan(self) -> None:
        """
        Scheduled job: scan all watched topics via the tiered web search tool
        and post a digest to the group room when new results are found.
        """
        logger.info("Running watched topic scan...")

        async def _on_results(topic: str, response) -> None:
            lines = [
                f"**Watched Topic Alert** — _{topic}_",
                "",
                f"New web results found ({response.tier_used}):",
                "",
            ]
            for i, r in enumerate(response.results, 1):
                lines.append(f"{i}. **{r.title}**")
                lines.append(f"   {r.snippet[:200]}")
                lines.append(f"   {r.url}")
                lines.append("")
            lines.append(
                "_Use `@bot` to ask Liberation Bot to analyze any of these results._"
            )
            await self._send_group_message("\n".join(lines))

        try:
            await run_watched_topic_scan(on_results=_on_results, max_results_per_topic=3)
        except Exception as exc:
            logger.error("Watched topic scan failed: %s", exc)

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    def _start_scheduler(self):
        """Start the APScheduler for periodic heartbeat checks and Dream cycles."""

        # --- Heartbeat check (existing) ---
        self.scheduler.add_job(
            self.heartbeat.run_check,
            "interval",
            minutes=HEARTBEAT_INTERVAL_MIN,
            id="heartbeat_check",
            replace_existing=True,
        )

        # --- Dream Engine: nightly memory consolidation ---
        # Runs at DREAM_HOUR_UTC:DREAM_MINUTE_UTC UTC every day.
        # misfire_grace_time=3600 means: if the bot was offline at the scheduled
        # time, run the job as soon as the bot comes back online (up to 1 hour late).
        self.scheduler.add_job(
            self.dream_engine.run_dream_cycle,
            "cron",
            hour=DREAM_HOUR_UTC,
            minute=DREAM_MINUTE_UTC,
            id="dream_cycle",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # --- FOIA Deadline Monitor: proactive deadline reminders ---
        self.scheduler.add_job(
            self.foia_deadline_monitor.run_check,
            "interval",
            minutes=FOIA_DEADLINE_CHECK_INTERVAL_MIN,
            id="foia_deadline_check",
            replace_existing=True,
        )

        # --- Watched Topic Scanner: proactive web monitoring ---
        if WATCHED_TOPIC_SCAN_INTERVAL_MIN > 0:
            self.scheduler.add_job(
                self._run_watched_topic_scan,
                "interval",
                minutes=WATCHED_TOPIC_SCAN_INTERVAL_MIN,
                id="watched_topic_scan",
                replace_existing=True,
            )

        self.scheduler.start()
        logger.info(
            "Scheduler started. Heartbeat: every %d min | Dream cycle: daily at %02d:%02d UTC "
            "| FOIA deadline check: every %d min.",
            HEARTBEAT_INTERVAL_MIN,
            DREAM_HOUR_UTC,
            DREAM_MINUTE_UTC,
            FOIA_DEADLINE_CHECK_INTERVAL_MIN,
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self):
        """Connect to the database, initialise modules, and start the bot."""
        logger.info("=== Liberation Bot (Agentic Phase I) starting up ===")

        # Connect database
        await self.db.connect()

        # Initialise sub-modules (including DreamEngine)
        await self._init_modules()

        # Register message handlers
        self._register_handlers()

        # Start the periodic heartbeat + Dream schedulers
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
        logger.info("Liberation Bot stopped by user.")
