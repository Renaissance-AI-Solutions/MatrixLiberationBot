"""
bot/video_room.py
=================
Video Planning Room Handler — Liberation Bot

Handles all messages and commands in the "Video Planning and Generation"
Matrix room.  After !video_start, Liberation Bot drives a natural dialogue
with the group, asks clarifying questions, and builds the video prompts
autonomously using the VideoDialogueAgent (Kimi K2).

Users never need to manually set titles or prompts — the bot does that
from the conversation context.

Command reference:
  !video_start              — Open a new session; bot begins the dialogue
  !video_styles             — List all styles + saved favourites
  !video_save_style <name>  — Save the current session's style as a favourite
  !video_preview            — Re-show the current prompt preview
  !video_confirm            — Confirm and generate (any user, during CONFIRMING)
  !video_revise <notes>     — Ask the bot to revise the prompts
  !video_cancel             — Cancel the current session
  !video_history            — Show recent completed videos
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Optional

from bot.video_session import VideoSession, VideoSessionManager, SessionState
from agent.video_dialogue import VideoDialogueAgent
from agent.tools.video_generator import generate_video, video_generation_available

if TYPE_CHECKING:
    from db.database import Database

logger = logging.getLogger(__name__)

VIDEO_ROOM_ID = os.getenv("MATRIX_VIDEO_ROOM_ID", "")


class VideoRoomHandler:
    """
    Routes messages in the Video Planning room.

    After !video_start:
      - Every non-command message is fed to VideoDialogueAgent.
      - The agent replies conversationally and eventually calls
        submit_video_prompts, which moves the session to CONFIRMING.
      - Any user can type !video_confirm to trigger generation.
      - !video_revise <notes> sends revision notes back to the agent.
    """

    def __init__(self, db: "Database", bot_api):
        self.db = db
        self.bot_api = bot_api
        self.sessions = VideoSessionManager()
        self.agent = VideoDialogueAgent()
        # room_id → asyncio.Task (for in-progress generation)
        self._generation_tasks: dict[str, asyncio.Task] = {}
        # room_id → asyncio.Lock (prevents concurrent agent calls per room)
        self._agent_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def handle_message(self, room, message):
        """Route a message from the video room."""
        sender = message.sender
        body   = (message.body if hasattr(message, "body") else "").strip()
        if not body:
            return

        # --- Commands (always handled regardless of session state) ---
        if body == "!video_start":
            await self._cmd_start(room, sender)

        elif body == "!video_cancel":
            await self._cmd_cancel(room, sender)

        elif body == "!video_styles":
            await self._cmd_list_styles(room)

        elif body.startswith("!video_save_style"):
            arg = body[len("!video_save_style"):].strip()
            await self._cmd_save_style(room, sender, arg)

        elif body == "!video_preview":
            await self._cmd_preview(room)

        elif body == "!video_confirm":
            await self._cmd_confirm(room, sender)

        elif body.startswith("!video_revise"):
            notes = body[len("!video_revise"):].strip()
            await self._cmd_revise(room, sender, notes)

        elif body == "!video_history":
            await self._cmd_history(room)

        elif body.startswith("!"):
            # Unknown command — ignore silently (other bot commands may exist)
            return

        else:
            # --- Free-form message: route to dialogue agent ---
            await self._handle_dialogue_message(room, sender, body)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, room, sender: str):
        ok, result = self.sessions.start_session(room.room_id, sender)
        if not ok:
            await self._send(room.room_id, result)   # result is an error string
            return

        session: VideoSession = result
        # Create DB record
        session._db_id = await self.db.create_video_session(room.room_id, sender)
        logger.info(
            "Video session started in %s by %s (db_id=%s)",
            room.room_id, sender, session._db_id,
        )

        # Get the opening message from the dialogue agent
        await self._send(room.room_id, "🎬 Starting a new video planning session...")
        dialogue_result = await self.agent.get_opening_message(session)
        await self._send(room.room_id, dialogue_result.reply)

    async def _cmd_cancel(self, room, sender: str):
        session = self.sessions.get_session(room.room_id)
        if session and session._db_id:
            await self.db.update_video_session(
                session._db_id,
                status="CANCELLED",
                brainstorm_notes_json=json.dumps(session.dialogue_history),
            )
        # Cancel any running generation task
        task = self._generation_tasks.pop(room.room_id, None)
        if task and not task.done():
            task.cancel()
        reply = self.sessions.cancel_session(room.room_id)
        await self._send(room.room_id, reply)

    async def _cmd_list_styles(self, room):
        saved = await self.db.list_styles()
        await self._send(room.room_id, VideoSessionManager.styles_help_text(saved))

    async def _cmd_save_style(self, room, sender: str, arg: str):
        session = self.sessions.get_session(room.room_id)
        if not session:
            await self._send(room.room_id, "No active session. Use `!video_start` first.")
            return
        if not session.style_key:
            await self._send(
                room.room_id,
                "No style has been chosen yet. "
                "Continue the conversation and I'll recommend one, or tell me which style you'd like."
            )
            return
        parts     = arg.split(None, 1)
        save_name = parts[0].lower() if parts else ""
        notes     = parts[1] if len(parts) > 1 else None
        if not save_name:
            await self._send(room.room_id, "Usage: `!video_save_style <name> [optional notes]`")
            return
        ok = await self.db.save_style(
            name=save_name,
            style_key=session.style_key,
            created_by=sender,
            notes=notes,
        )
        if ok:
            await self._send(
                room.room_id,
                f"⭐ Style **{session.style_key}** saved as `{save_name}`"
                + (f" — _{notes}_" if notes else "")
                + ".\nYou can reference this name in future sessions.",
            )
        else:
            await self._send(room.room_id, "❌ Failed to save style. Please try again.")

    async def _cmd_preview(self, room):
        session = self.sessions.get_session(room.room_id)
        if not session:
            await self._send(room.room_id, "No active video planning session. Use `!video_start` to begin.")
            return
        await self._send(room.room_id, session.preview_text())

    async def _cmd_confirm(self, room, sender: str):
        session = self.sessions.get_session(room.room_id)
        if not session:
            await self._send(room.room_id, "No active video planning session. Use `!video_start` to begin.")
            return
        if session.state == SessionState.GENERATING:
            await self._send(room.room_id, "⏳ Video generation is already in progress. Please wait.")
            return
        if session.state == SessionState.BRAINSTORMING:
            await self._send(
                room.room_id,
                "⚠️ I'm still gathering information for the prompts. "
                "Continue the conversation and I'll let you know when they're ready to confirm.\n"
                "You can also type `!video_preview` to see what I have so far."
            )
            return
        if session.state != SessionState.CONFIRMING:
            await self._send(room.room_id, "⚠️ Nothing to confirm right now.")
            return
        if not session.is_complete:
            await self._send(room.room_id, session.preview_text())
            return

        # Move to GENERATING
        self.sessions.mark_generating(room.room_id, sender)

        # Check availability
        available, reason = video_generation_available()
        if not available:
            await self._send(
                room.room_id,
                f"❌ Video generation is not available: {reason}\n"
                "Please check the bot configuration and try again.",
            )
            self.sessions.mark_failed(room.room_id, reason)
            return

        # Persist to DB
        if session._db_id:
            await self.db.update_video_session(
                session._db_id,
                title=session.title,
                style_key=session.style_key,
                custom_prompt=session.custom_prompt,
                full_prompt=session.full_custom_prompt,
                brainstorm_notes_json=json.dumps(session.dialogue_history),
                status="IN_PROGRESS",
            )

        await self._send(
            room.room_id,
            f"✅ Confirmed by {sender}. Starting video generation now...\n"
            "⏳ This may take several minutes. I'll post the result when it's ready.",
        )

        # Launch background generation
        task = asyncio.create_task(self._run_generation(room.room_id, session))
        self._generation_tasks[room.room_id] = task

    async def _cmd_revise(self, room, sender: str, notes: str):
        session = self.sessions.get_session(room.room_id)
        if not session:
            await self._send(room.room_id, "No active video planning session. Use `!video_start` to begin.")
            return
        if session.state == SessionState.GENERATING:
            await self._send(room.room_id, "⏳ Generation is already in progress — revisions are not possible now.")
            return
        if not notes:
            await self._send(
                room.room_id,
                "Usage: `!video_revise <your revision notes>`\n"
                "Example: `!video_revise Make the tone more urgent and add more focus on the legal angle`"
            )
            return

        # Move back to BRAINSTORMING so the agent can revise
        session.state = SessionState.BRAINSTORMING
        await self._send(room.room_id, "📝 Got it — let me revise the prompts based on your feedback...")
        await self._run_agent(room.room_id, session, sender=sender, content=None, revision_notes=notes)

    async def _cmd_history(self, room):
        from datetime import datetime, timezone
        sessions = await self.db.get_recent_video_sessions(room_id=room.room_id, limit=10)
        if not sessions:
            await self._send(room.room_id, "No video sessions recorded for this room yet.")
            return
        lines = ["## 🎬 Recent Video Sessions", ""]
        for s in sessions:
            ts = datetime.fromtimestamp(s["created_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            emoji = {"COMPLETED": "✅", "FAILED": "❌", "CANCELLED": "🚫", "IN_PROGRESS": "⏳"}.get(s["status"], "❓")
            lines.append(
                f"{emoji} **{s['title'] or 'Untitled'}** "
                f"| `{s['style_key'] or 'n/a'}` "
                f"| {ts} | by {s['started_by']}"
            )
            if s["status"] == "COMPLETED" and s.get("video_download_path"):
                lines.append(f"   📁 `{s['video_download_path']}`")
        await self._send(room.room_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Dialogue routing
    # ------------------------------------------------------------------

    async def _handle_dialogue_message(self, room, sender: str, content: str):
        """Route a free-form message to the dialogue agent."""
        session = self.sessions.get_session(room.room_id)
        if not session:
            # No active session — silently ignore (don't spam the room)
            return
        if session.state not in (SessionState.BRAINSTORMING, SessionState.CONFIRMING):
            # Session is generating or done — ignore free-form messages
            return
        await self._run_agent(room.room_id, session, sender=sender, content=content)

    async def _run_agent(
        self,
        room_id: str,
        session: VideoSession,
        sender: str,
        content: Optional[str],
        revision_notes: Optional[str] = None,
    ):
        """
        Call the VideoDialogueAgent and post its reply.
        Uses a per-room lock to prevent concurrent agent calls.
        """
        lock = self._agent_locks.setdefault(room_id, asyncio.Lock())
        if lock.locked():
            # Agent is already thinking — queue the message but don't double-call
            logger.debug("Agent lock held for %s — message will be included in next call", room_id)
            if content:
                session.add_user_message(sender, content)
            return

        async with lock:
            try:
                if revision_notes is not None:
                    result = await self.agent.process_revision(session, sender, revision_notes)
                elif content is not None:
                    result = await self.agent.process_message(session, sender, content)
                else:
                    return
            except Exception as exc:
                logger.error("VideoDialogueAgent error: %s", exc, exc_info=True)
                await self._send(
                    room_id,
                    "⚠️ I encountered an error. Please try again in a moment.",
                )
                return

            # Post the reply
            await self._send(room_id, result.reply)

            # Sync session to DB after each agent turn
            if session._db_id:
                await self.db.update_video_session(
                    session._db_id,
                    title=session.title,
                    style_key=session.style_key,
                    custom_prompt=session.custom_prompt,
                    brainstorm_notes_json=json.dumps(session.dialogue_history),
                    status="CONFIRMING" if session.state == SessionState.CONFIRMING else "BRAINSTORMING",
                )

    # ------------------------------------------------------------------
    # Background generation
    # ------------------------------------------------------------------

    async def _run_generation(self, room_id: str, session: VideoSession):
        logger.info(
            "Starting video generation for room %s | title=%s | style=%s",
            room_id, session.title, session.style_key,
        )
        try:
            result = await generate_video(
                custom_prompt=session.full_custom_prompt,
                style_key=session.style_key,
                title=session.title or "liberation-video",
            )
        except Exception as exc:
            logger.error("Unexpected error in video generation: %s", exc, exc_info=True)
            result_success  = False
            result_error    = str(exc)
            result_path     = None
            result_task_id  = None
            result_duration = 0.0
        else:
            result_success  = result.success
            result_error    = result.error
            result_path     = result.video_path
            result_task_id  = result.task_id
            result_duration = result.duration_secs

        if result_success:
            self.sessions.mark_done(room_id, result_path)
            if session._db_id:
                await self.db.update_video_session(
                    session._db_id,
                    status="COMPLETED",
                    notebooklm_task_id=result_task_id,
                    video_download_path=result_path,
                )
            mins = int(result_duration // 60)
            secs = int(result_duration % 60)
            await self._send(
                room_id,
                f"## ✅ Video Generation Complete!\n\n"
                f"**Title:** {session.title}\n"
                f"**Style:** `{session.style_key}`\n"
                f"**Generation time:** {mins}m {secs}s\n\n"
                f"📁 **Saved to:** `{result_path}`\n\n"
                f"The closing slide includes the NPWA call to action and "
                f"Liberation Archives attribution.\n\n"
                f"Use `!video_history` to see all completed videos, or "
                f"`!video_start` to plan another one.",
            )
        else:
            self.sessions.mark_failed(room_id, result_error or "unknown error")
            if session._db_id:
                await self.db.update_video_session(
                    session._db_id,
                    status="FAILED",
                    notebooklm_task_id=result_task_id,
                    error_note=result_error,
                )
            await self._send(
                room_id,
                f"## ❌ Video Generation Failed\n\n"
                f"**Error:** {result_error}\n\n"
                "You can try again with `!video_start`, or use `!video_revise` "
                "to adjust the prompts before retrying.",
            )

        self._generation_tasks.pop(room_id, None)

    # ------------------------------------------------------------------
    # Send helper
    # ------------------------------------------------------------------

    async def _send(self, room_id: str, text: str):
        try:
            await self.bot_api.send_markdown_message(room_id, text)
        except Exception as exc:
            logger.error("Failed to send message to %s: %s", room_id, exc)
