"""
bot/video_session.py
====================
Video Planning Session — Liberation Bot

Holds the state of a single video planning session.  All prompt-building,
question-asking, and decision-making is handled by the VideoDialogueAgent
(agent/video_dialogue.py).  This module is a pure data container and
lifecycle manager — it contains no LLM logic.

Session lifecycle:
  BRAINSTORMING  →  CONFIRMING  →  GENERATING  →  DONE / FAILED

  BRAINSTORMING : The LLM is conducting a dialogue with the group.
                  All messages are recorded and fed to the agent.
  CONFIRMING    : The LLM has drafted both prompts and posted a preview.
                  Waiting for any user to type !video_confirm.
  GENERATING    : notebooklm-py is running in the background.
  DONE / FAILED : Terminal states.

Commands still available to users:
  !video_start          — Open a new session (triggers the dialogue)
  !video_styles         — List available styles + saved favourites
  !video_save_style     — Save a style as a named favourite
  !video_preview        — Re-show the current prompt preview
  !video_confirm        — Confirm and generate (any user, any time in CONFIRMING)
  !video_cancel         — Cancel the current session
  !video_history        — Show recent completed videos
  !video_revise <notes> — Ask the bot to revise the prompts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SessionState(Enum):
    BRAINSTORMING = auto()   # LLM is asking questions / building prompts
    CONFIRMING    = auto()   # LLM has posted a preview; waiting for !video_confirm
    GENERATING    = auto()   # notebooklm-py is running
    DONE          = auto()   # Video downloaded successfully
    FAILED        = auto()   # Generation failed


# ---------------------------------------------------------------------------
# Style catalogue (single source of truth)
# ---------------------------------------------------------------------------

# Mapping from user-friendly key → notebooklm VideoStyle enum name
VIDEO_STYLE_NAMES: dict[str, str] = {
    "auto":       "AUTO_SELECT",
    "classic":    "CLASSIC",
    "whiteboard": "WHITEBOARD",
    "kawaii":     "KAWAII",
    "anime":      "ANIME",
    "watercolor": "WATERCOLOR",
    "retro":      "RETRO_PRINT",
    "heritage":   "HERITAGE",
    "papercraft": "PAPER_CRAFT",
    "cinematic":  "CINEMATIC",   # Veo 3 — requires NotebookLM Ultra
}

VIDEO_STYLE_DESCRIPTIONS: dict[str, str] = {
    "auto":       "NotebookLM auto-selects the best style for the content",
    "classic":    "Clean slide-deck animation with professional typography",
    "whiteboard": "Hand-drawn whiteboard animation style",
    "kawaii":     "Cute, colourful Japanese kawaii illustration style",
    "anime":      "Japanese anime / manga-inspired animation",
    "watercolor": "Soft watercolour painting aesthetic",
    "retro":      "Vintage retro-print / risograph style",
    "heritage":   "Classic documentary / heritage film look",
    "papercraft": "Paper cut-out / collage animation style",
    "cinematic":  "AI-generated documentary footage via Veo 3 (~30–40 min, requires Ultra)",
}

# Closing-slide call-to-action — appended to every custom prompt
NPWA_CTA_SUFFIX = (
    "\n\n---\n"
    "CLOSING SLIDE — MANDATORY CALL TO ACTION:\n"
    "The final slide of this video MUST include:\n"
    "  • The name: NeuroPsychological Warfare Alliance (NPWA)\n"
    "  • Our website: neuropsychwarfare.org\n"
    "  • The statement: 'This video was produced using the Liberation Archives — "
    "a compilation of declassified military and intelligence documents combined "
    "with whistleblower testimony.'\n"
    "  • A call to action encouraging viewers to join the alliance and support "
    "victims of Neurowarfare, Havana Syndrome, and Anomalous Health Incidents (AHIs)."
)


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

@dataclass
class VideoSession:
    """
    Holds the mutable state of one video planning session.

    The LLM dialogue agent reads and writes `title`, `style_key`,
    `custom_prompt`, and `dialogue_history`.  The room handler reads
    `state` to decide how to route incoming messages.
    """

    room_id:     str
    started_by:  str                        # Matrix user ID of session creator
    state:       SessionState = SessionState.BRAINSTORMING

    # --- Prompt fields (set by the LLM, not by user commands) ---
    title:         Optional[str] = None     # Human-readable video title
    style_key:     Optional[str] = None     # Key from VIDEO_STYLE_NAMES
    custom_prompt: Optional[str] = None     # Content / instructions prompt

    # --- Dialogue history for the LLM context window ---
    # Each entry: {"role": "user"|"assistant", "sender": str, "content": str}
    dialogue_history: list[dict] = field(default_factory=list)

    # --- Generation tracking ---
    task_id:    Optional[str] = None
    video_path: Optional[str] = None        # Local path after download
    error_note: Optional[str] = None

    # --- Confirmation tracking ---
    confirmed_by: Optional[str] = None

    # --- DB record ID (set by VideoRoomHandler after DB insert) ---
    _db_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def style_display(self) -> str:
        if not self.style_key:
            return "_not chosen yet_"
        desc = VIDEO_STYLE_DESCRIPTIONS.get(self.style_key, "")
        return f"**{self.style_key}** — {desc}"

    @property
    def full_custom_prompt(self) -> str:
        """Return the content prompt with the NPWA CTA suffix appended."""
        base = self.custom_prompt or ""
        return base + NPWA_CTA_SUFFIX

    @property
    def is_complete(self) -> bool:
        """True when the LLM has filled in all three required fields."""
        return bool(self.title and self.style_key and self.custom_prompt)

    # ------------------------------------------------------------------
    # Dialogue history helpers
    # ------------------------------------------------------------------

    def add_user_message(self, sender: str, content: str):
        """Record a user message in the dialogue history."""
        self.dialogue_history.append({
            "role": "user",
            "sender": sender,
            "content": content,
        })

    def add_assistant_message(self, content: str):
        """Record a bot reply in the dialogue history."""
        self.dialogue_history.append({
            "role": "assistant",
            "sender": "liberation-bot",
            "content": content,
        })

    def dialogue_as_openai_messages(self) -> list[dict]:
        """
        Convert dialogue_history to the OpenAI messages format.
        User messages are prefixed with the sender's display name so the
        LLM can distinguish between multiple participants.
        """
        messages = []
        for entry in self.dialogue_history:
            if entry["role"] == "user":
                messages.append({
                    "role": "user",
                    "content": f"[{entry['sender']}]: {entry['content']}",
                })
            else:
                messages.append({
                    "role": "assistant",
                    "content": entry["content"],
                })
        return messages

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview_text(self) -> str:
        """Generate the confirmation preview message shown before generation."""
        lines = [
            "## 🎬 Video Generation Preview",
            "",
            f"**Title:** {self.title or '_not set_'}",
            "",
            f"**Visual Style:** {self.style_display}",
            "",
            "**Content Prompt:**",
            "```",
            self.custom_prompt or "_not set_",
            "```",
            "",
            "**Closing Slide (auto-appended to every video):**",
            "```",
            NPWA_CTA_SUFFIX.strip(),
            "```",
            "",
        ]
        if self.is_complete:
            lines += [
                "✅ **Ready to generate!**",
                "",
                "Type `!video_confirm` to start generation, or `!video_revise <notes>` "
                "to ask me to adjust the prompts.",
            ]
        else:
            missing = []
            if not self.title:        missing.append("title")
            if not self.style_key:    missing.append("visual style")
            if not self.custom_prompt: missing.append("content prompt")
            lines += [
                f"⚠️ Still working on: {', '.join(missing)}.",
                "Continue the conversation and I'll fill these in.",
            ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class VideoSessionManager:
    """
    Manages one active VideoSession per room.
    One room → one active session at a time.
    """

    def __init__(self):
        self._sessions: dict[str, VideoSession] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_session(self, room_id: str, started_by: str) -> tuple[bool, VideoSession | str]:
        """
        Start a new session.
        Returns (True, session) on success, (False, error_message) if blocked.
        """
        existing = self._sessions.get(room_id)
        if existing and existing.state not in (SessionState.DONE, SessionState.FAILED):
            return (False,
                "⚠️ A video planning session is already active.\n"
                "Use `!video_cancel` to cancel it before starting a new one."
            )
        session = VideoSession(room_id=room_id, started_by=started_by)
        self._sessions[room_id] = session
        return (True, session)

    def cancel_session(self, room_id: str) -> str:
        session = self._sessions.pop(room_id, None)
        if not session:
            return "No active video planning session in this room."
        return (
            f"❌ Video planning session cancelled "
            f"(started by {session.started_by}).\n"
            "Use `!video_start` to begin a new one."
        )

    def get_session(self, room_id: str) -> Optional[VideoSession]:
        return self._sessions.get(room_id)

    def has_active_session(self, room_id: str) -> bool:
        s = self._sessions.get(room_id)
        return s is not None and s.state not in (SessionState.DONE, SessionState.FAILED)

    def mark_generating(self, room_id: str, confirmed_by: str) -> Optional[VideoSession]:
        s = self._sessions.get(room_id)
        if s and s.state == SessionState.CONFIRMING:
            s.state = SessionState.GENERATING
            s.confirmed_by = confirmed_by
            return s
        return None

    def mark_done(self, room_id: str, video_path: str):
        s = self._sessions.get(room_id)
        if s:
            s.state = SessionState.DONE
            s.video_path = video_path

    def mark_failed(self, room_id: str, error: str):
        s = self._sessions.get(room_id)
        if s:
            s.state = SessionState.FAILED
            s.error_note = error

    # ------------------------------------------------------------------
    # Style listing helper
    # ------------------------------------------------------------------

    @staticmethod
    def styles_help_text(saved_styles: list[dict]) -> str:
        lines = [
            "## 🎨 Available Video Styles",
            "",
            "The bot will recommend a style based on your video concept, "
            "but you can also request one directly during the conversation.",
            "",
            "| Name | Description |",
            "|---|---|",
        ]
        for key, desc in VIDEO_STYLE_DESCRIPTIONS.items():
            lines.append(f"| `{key}` | {desc} |")

        if saved_styles:
            lines += [
                "",
                "## ⭐ Saved Style Favourites",
                "",
                "| Name | Style | Notes | Used |",
                "|---|---|---|---|",
            ]
            for ss in saved_styles:
                lines.append(
                    f"| `{ss['name']}` | `{ss['style_key']}` "
                    f"| {ss.get('notes') or ''} | {ss.get('use_count', 0)}x |"
                )
            lines += [
                "",
                "Mention a saved style name during brainstorming and the bot will apply it.",
            ]
        return "\n".join(lines)
