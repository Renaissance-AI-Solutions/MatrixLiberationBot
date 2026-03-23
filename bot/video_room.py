"""
bot/video_room.py
=================
Video Planning Room Handler — Liberation Bot Phase I

Handles all commands and message routing for the "Video Planning and
Generation" Matrix room.  This module is instantiated once and registered
as a set of message listeners in bot/bot.py.

Command reference (all prefixed with !):
  !video_start                    — Open a new brainstorming session
  !video_title <title>            — Set the video title
  !video_style <name>             — Choose a visual style
  !video_styles                   — List all styles + saved favourites
  !video_save_style <name> [notes]— Save current style as a named favourite
  !video_prompt <text>            — Set the content prompt
  !video_preview                  — Preview both prompts
  !video_confirm                  — Confirm and trigger generation
  !video_cancel                   — Cancel the current session
  !video_history                  — Show recent completed videos
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

from bot.video_session import VideoSessionManager, SessionState
from agent.tools.video_generator import generate_video, video_generation_available

if TYPE_CHECKING:
    from db.database import Database

logger = logging.getLogger(__name__)

# The Matrix room ID for the Video Planning and Generation room
VIDEO_ROOM_ID = os.getenv("MATRIX_VIDEO_ROOM_ID", "")


class VideoRoomHandler:
    """
    Handles the video planning workflow for the Video Planning and Generation room.

    Lifecycle:
      1. User calls !video_start → session opens, brainstorming begins.
      2. Users chat freely — all messages are recorded as brainstorming notes.
      3. Commands set title, style, and prompt incrementally.
      4. !video_preview shows the full prompt for review.
      5. Any user calls !video_confirm → bot previews prompts and starts generation.
      6. Bot posts a download link (or error) when done.
    """

    def __init__(self, db: "Database", bot_api):
        self.db = db
        self.bot_api = bot_api
        self.sessions = VideoSessionManager()
        # Track in-progress generation tasks: room_id → asyncio.Task
        self._generation_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public entry point — called from bot.py for every message in the
    # video room.
    # ------------------------------------------------------------------

    async def handle_message(self, room, message):
        """Route a message from the video room to the appropriate handler."""
        sender = message.sender
        body = message.body if hasattr(message, "body") else ""

        if not body:
            return

        # Dispatch commands
        if body.startswith("!video_start"):
            await self._cmd_start(room, sender)

        elif body.startswith("!video_cancel"):
            await self._cmd_cancel(room, sender)

        elif body.startswith("!video_title "):
            title = body[len("!video_title "):].strip()
            await self._cmd_title(room, sender, title)

        elif body.startswith("!video_style ") or body == "!video_style":
            arg = body[len("!video_style"):].strip()
            await self._cmd_style(room, sender, arg)

        elif body.startswith("!video_styles"):
            await self._cmd_list_styles(room, sender)

        elif body.startswith("!video_save_style "):
            arg = body[len("!video_save_style "):].strip()
            await self._cmd_save_style(room, sender, arg)

        elif body.startswith("!video_prompt "):
            prompt_text = body[len("!video_prompt "):].strip()
            await self._cmd_prompt(room, sender, prompt_text)

        elif body.startswith("!video_preview"):
            await self._cmd_preview(room, sender)

        elif body.startswith("!video_confirm"):
            await self._cmd_confirm(room, sender)

        elif body.startswith("!video_history"):
            await self._cmd_history(room, sender)

        else:
            # Free-form brainstorming message — record as a note
            if self.sessions.has_active_session(room.room_id):
                self.sessions.record_brainstorm_message(room.room_id, sender, body)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, room, sender: str):
        reply = self.sessions.start_session(room.room_id, sender)
        await self._send(room.room_id, reply)
        # Create a DB record for this session
        session = self.sessions.get_session(room.room_id)
        if session:
            session._db_id = await self.db.create_video_session(room.room_id, sender)
            logger.info(
                "Video session started in %s by %s (db_id=%s)",
                room.room_id, sender, session._db_id,
            )

    async def _cmd_cancel(self, room, sender: str):
        session = self.sessions.get_session(room.room_id)
        if session and hasattr(session, "_db_id"):
            await self.db.update_video_session(
                session._db_id,
                status="CANCELLED",
                brainstorm_notes_json=json.dumps(session.brainstorm_notes),
            )
        # Cancel any running generation task
        task = self._generation_tasks.pop(room.room_id, None)
        if task and not task.done():
            task.cancel()
        reply = self.sessions.cancel_session(room.room_id)
        await self._send(room.room_id, reply)

    async def _cmd_title(self, room, sender: str, title: str):
        if not title:
            await self._send(room.room_id, "Usage: `!video_title <title>`")
            return
        reply = self.sessions.handle_title(room.room_id, title)
        await self._send(room.room_id, reply)
        await self._sync_session_to_db(room.room_id)

    async def _cmd_style(self, room, sender: str, arg: str):
        if not arg:
            await self._cmd_list_styles(room, sender)
            return
        # Check if it's a saved style name first
        saved = await self.db.get_style(arg.lower())
        if saved:
            # Apply the saved style's style_key
            reply = self.sessions.handle_style(room.room_id, saved["style_key"])
            if "✅" in reply:
                reply += f"\n\n_(Applied from saved favourite: **{arg}**)_"
            await self._send(room.room_id, reply)
            await self.db.increment_style_use_count(arg.lower())
        else:
            reply = self.sessions.handle_style(room.room_id, arg)
            await self._send(room.room_id, reply)
        await self._sync_session_to_db(room.room_id)

    async def _cmd_list_styles(self, room, sender: str):
        saved = await self.db.list_styles()
        text = VideoSessionManager.styles_help_text(saved)
        await self._send(room.room_id, text)

    async def _cmd_save_style(self, room, sender: str, arg: str):
        """Save the current session's style as a named favourite.
        Usage: !video_save_style <name> [optional notes]
        """
        session = self.sessions.get_session(room.room_id)
        if not session:
            await self._send(room.room_id, "No active session. Use `!video_start` first.")
            return
        if not session.style_key:
            await self._send(room.room_id, "No style set yet. Use `!video_style <name>` first.")
            return

        parts = arg.split(None, 1)
        save_name = parts[0].lower() if parts else ""
        notes = parts[1] if len(parts) > 1 else None

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
                f"⭐ Style saved as **{save_name}** "
                f"(style: `{session.style_key}`{f', notes: {notes}' if notes else ''}).\n"
                "You can reuse it with `!video_style {save_name}` in future sessions.",
            )
        else:
            await self._send(room.room_id, "❌ Failed to save style. Please try again.")

    async def _cmd_prompt(self, room, sender: str, prompt_text: str):
        if not prompt_text:
            await self._send(room.room_id, "Usage: `!video_prompt <your content prompt>`")
            return
        reply = self.sessions.handle_prompt(room.room_id, prompt_text)
        await self._send(room.room_id, reply)
        await self._sync_session_to_db(room.room_id)

    async def _cmd_preview(self, room, sender: str):
        reply = self.sessions.handle_preview(room.room_id)
        await self._send(room.room_id, reply)

    async def _cmd_confirm(self, room, sender: str):
        reply, session = self.sessions.handle_confirm(room.room_id, sender)
        await self._send(room.room_id, reply)

        if session is None:
            return  # Not ready or already generating

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

        # Persist the full prompt to DB before starting
        if hasattr(session, "_db_id"):
            await self.db.update_video_session(
                session._db_id,
                title=session.title,
                style_key=session.style_key,
                custom_prompt=session.custom_prompt,
                full_prompt=session.full_custom_prompt,
                brainstorm_notes_json=json.dumps(session.brainstorm_notes),
                status="IN_PROGRESS",
            )

        # Launch generation as a background task
        task = asyncio.create_task(
            self._run_generation(room.room_id, session)
        )
        self._generation_tasks[room.room_id] = task

    async def _cmd_history(self, room, sender: str):
        sessions = await self.db.get_recent_video_sessions(room_id=room.room_id, limit=10)
        if not sessions:
            await self._send(room.room_id, "No video sessions recorded for this room yet.")
            return
        from datetime import datetime, timezone
        lines = ["## 🎬 Recent Video Sessions", ""]
        for s in sessions:
            ts = datetime.fromtimestamp(s["created_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            status_emoji = {
                "COMPLETED": "✅", "FAILED": "❌",
                "CANCELLED": "🚫", "IN_PROGRESS": "⏳",
            }.get(s["status"], "❓")
            lines.append(
                f"{status_emoji} **{s['title'] or 'Untitled'}** "
                f"| style: `{s['style_key'] or 'n/a'}` "
                f"| {ts} "
                f"| by {s['started_by']}"
            )
            if s["status"] == "COMPLETED" and s["video_download_path"]:
                lines.append(f"   📁 `{s['video_download_path']}`")
        await self._send(room.room_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Background generation runner
    # ------------------------------------------------------------------

    async def _run_generation(self, room_id: str, session):
        """Run video generation in the background and post results to the room."""
        logger.info(
            "Starting background video generation for room %s | title=%s | style=%s",
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
            result_success = False
            result_error = str(exc)
            result_path = None
            result_task_id = None
            result_duration = 0.0
        else:
            result_success = result.success
            result_error = result.error
            result_path = result.video_path
            result_task_id = result.task_id
            result_duration = result.duration_secs

        if result_success:
            self.sessions.mark_done(room_id, result_path)
            if hasattr(session, "_db_id"):
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
                f"**Duration:** {mins}m {secs}s\n\n"
                f"📁 **Saved to:** `{result_path}`\n\n"
                f"The video includes the NPWA call to action and Liberation Archives attribution "
                f"on the closing slide.\n\n"
                f"Use `!video_history` to see all completed videos.",
            )
        else:
            self.sessions.mark_failed(room_id, result_error or "unknown error")
            if hasattr(session, "_db_id"):
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
                "You can try again with `!video_start` or adjust your prompts.\n"
                "If the issue persists, check that your NotebookLM credentials "
                "and notebook ID are correctly configured.",
            )

        # Clean up task reference
        self._generation_tasks.pop(room_id, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send(self, room_id: str, text: str):
        """Send a markdown message to the video room."""
        try:
            await self.bot_api.send_markdown_message(room_id, text)
        except Exception as exc:
            logger.error("Failed to send message to %s: %s", room_id, exc)

    async def _sync_session_to_db(self, room_id: str):
        """Persist the current session state to the database."""
        session = self.sessions.get_session(room_id)
        if session and hasattr(session, "_db_id"):
            await self.db.update_video_session(
                session._db_id,
                title=session.title,
                style_key=session.style_key,
                custom_prompt=session.custom_prompt,
                brainstorm_notes_json=json.dumps(session.brainstorm_notes),
            )
