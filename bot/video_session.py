"""
bot/video_session.py
====================
Video Planning Session Manager — Liberation Bot Phase I

Manages the lifecycle of a brainstorming session in the "Video Planning and
Generation" Matrix room.  A session moves through these states:

  BRAINSTORMING  →  AWAITING_STYLE  →  AWAITING_CUSTOM_PROMPT
                 →  CONFIRMING      →  GENERATING      →  DONE / FAILED

Commands handled here (all prefixed with !):
  !video_start              — Open a new brainstorming session
  !video_title <title>      — Set the video title
  !video_style <name>       — Choose a visual style (or !video_styles to list)
  !video_prompt <text>      — Set the custom content prompt
  !video_preview            — Preview both prompts before confirmation
  !video_confirm            — Confirm and trigger generation
  !video_cancel             — Cancel the current session
  !video_styles             — List all available styles and saved favourites
  !video_save_style <name>  — Save the current style prompt as a named favourite
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
    BRAINSTORMING = auto()
    CONFIRMING = auto()
    GENERATING = auto()
    DONE = auto()
    FAILED = auto()


# Mapping from user-friendly name → notebooklm VideoStyle enum name
# (resolved at generation time to avoid importing notebooklm at module load)
VIDEO_STYLE_NAMES: dict[str, str] = {
    "auto":        "AUTO_SELECT",
    "classic":     "CLASSIC",
    "whiteboard":  "WHITEBOARD",
    "kawaii":      "KAWAII",
    "anime":       "ANIME",
    "watercolor":  "WATERCOLOR",
    "retro":       "RETRO_PRINT",
    "heritage":    "HERITAGE",
    "papercraft":  "PAPER_CRAFT",
    "cinematic":   "CINEMATIC",   # Uses generate_cinematic_video() — Veo 3
}

# Human-readable descriptions for each style
VIDEO_STYLE_DESCRIPTIONS: dict[str, str] = {
    "auto":       "NotebookLM auto-selects the best style",
    "classic":    "Clean slide-deck animation with professional typography",
    "whiteboard": "Hand-drawn whiteboard animation style",
    "kawaii":     "Cute, colourful Japanese kawaii illustration style",
    "anime":      "Japanese anime / manga-inspired animation",
    "watercolor": "Soft watercolour painting aesthetic",
    "retro":      "Vintage retro-print / risograph style",
    "heritage":   "Classic documentary / heritage film look",
    "papercraft": "Paper cut-out / collage animation style",
    "cinematic":  "AI-generated documentary footage via Veo 3 (requires Ultra subscription, ~30–40 min)",
}

# Closing-slide call-to-action appended to every custom prompt
NPWA_CTA_SUFFIX = (
    "\n\n---\n"
    "CLOSING SLIDE — CALL TO ACTION:\n"
    "The final slide of this video MUST include a call to action for the "
    "NeuroPsychological Warfare Alliance (NPWA). It should:\n"
    "  • Display the NPWA name prominently.\n"
    "  • Direct viewers to our website: neuropsychwarfare.org\n"
    "  • State that this video was generated using the Liberation Archives — "
    "our compilation of unclassified military and intelligence documents "
    "combined with whistleblower testimony.\n"
    "  • Encourage viewers to join the alliance and support victims of "
    "Neurowarfare, Havana Syndrome, and Anomalous Health Incidents (AHIs)."
)


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

@dataclass
class VideoSession:
    """Holds the state of a single video planning session."""

    room_id: str
    started_by: str                        # Matrix user ID of session creator
    state: SessionState = SessionState.BRAINSTORMING

    title: Optional[str] = None            # Human-readable video title
    style_key: Optional[str] = None        # Key from VIDEO_STYLE_NAMES
    custom_prompt: Optional[str] = None    # Content / instructions prompt
    brainstorm_notes: list[str] = field(default_factory=list)

    # Set after generation starts
    task_id: Optional[str] = None
    artifact_id: Optional[str] = None
    video_path: Optional[str] = None       # Local path after download

    # Confirmation tracking — first !video_confirm triggers generation
    confirmed_by: Optional[str] = None

    @property
    def style_display(self) -> str:
        if not self.style_key:
            return "_not set_"
        desc = VIDEO_STYLE_DESCRIPTIONS.get(self.style_key, "")
        return f"**{self.style_key}** — {desc}"

    @property
    def full_custom_prompt(self) -> str:
        """Return the custom prompt with the NPWA CTA suffix appended."""
        base = self.custom_prompt or ""
        return base + NPWA_CTA_SUFFIX

    def is_ready_to_confirm(self) -> tuple[bool, list[str]]:
        """Check whether all required fields are set. Returns (ready, missing)."""
        missing = []
        if not self.title:
            missing.append("video title (`!video_title <title>`)")
        if not self.style_key:
            missing.append("visual style (`!video_style <name>`)")
        if not self.custom_prompt:
            missing.append("content prompt (`!video_prompt <text>`)")
        return len(missing) == 0, missing

    def preview_text(self) -> str:
        """Generate the confirmation preview message."""
        ready, missing = self.is_ready_to_confirm()
        lines = [
            "## 🎬 Video Generation Preview",
            "",
            f"**Title:** {self.title or '_not set_'}",
            "",
            f"**Visual Style:** {self.style_display}",
            "",
            "**Custom Content Prompt:**",
            f"```",
            self.custom_prompt or "_not set_",
            "```",
            "",
            "**Closing Slide (auto-appended):**",
            "```",
            NPWA_CTA_SUFFIX.strip(),
            "```",
        ]
        if missing:
            lines += [
                "",
                "⚠️ **Not ready yet — missing:**",
            ] + [f"  - {m}" for m in missing]
        else:
            lines += [
                "",
                "✅ **Ready to generate!**",
                "Reply `!video_confirm` to start generation, or `!video_cancel` to abort.",
            ]
        return "\n".join(lines)

    def add_brainstorm_note(self, sender: str, text: str):
        self.brainstorm_notes.append(f"[{sender}] {text}")


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class VideoSessionManager:
    """
    Manages one active VideoSession per room.

    A room can only have one active session at a time.  Starting a new session
    while one is active requires cancelling the existing one first.
    """

    def __init__(self):
        # room_id → VideoSession
        self._sessions: dict[str, VideoSession] = {}

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(self, room_id: str, started_by: str) -> str:
        """Start a new brainstorming session. Returns a welcome message."""
        if room_id in self._sessions:
            existing = self._sessions[room_id]
            if existing.state not in (SessionState.DONE, SessionState.FAILED):
                return (
                    "⚠️ A video planning session is already active in this room.\n"
                    "Use `!video_cancel` to cancel it before starting a new one."
                )
        self._sessions[room_id] = VideoSession(room_id=room_id, started_by=started_by)
        return (
            "## 🎬 Video Planning Session Started!\n\n"
            "Let's brainstorm a video for the NeuroPsychological Warfare Alliance.\n\n"
            "**Steps:**\n"
            "1. Discuss your video idea freely — all messages in this room are recorded as brainstorming notes.\n"
            "2. `!video_title <title>` — Set the video title.\n"
            "3. `!video_style <name>` — Choose a visual style (use `!video_styles` to see options).\n"
            "4. `!video_prompt <text>` — Set the content prompt (what the video should cover).\n"
            "5. `!video_preview` — Preview the full prompt before generating.\n"
            "6. `!video_confirm` — Confirm and generate the video.\n\n"
            "The closing slide will automatically include the NPWA call to action and Liberation Archives attribution.\n\n"
            "Type `!video_cancel` at any time to cancel."
        )

    def cancel_session(self, room_id: str) -> str:
        """Cancel the active session."""
        session = self._sessions.pop(room_id, None)
        if not session:
            return "No active video planning session in this room."
        return f"❌ Video planning session cancelled (was started by {session.started_by})."

    def get_session(self, room_id: str) -> Optional[VideoSession]:
        return self._sessions.get(room_id)

    def has_active_session(self, room_id: str) -> bool:
        s = self._sessions.get(room_id)
        return s is not None and s.state not in (SessionState.DONE, SessionState.FAILED)

    def mark_done(self, room_id: str, video_path: str):
        s = self._sessions.get(room_id)
        if s:
            s.state = SessionState.DONE
            s.video_path = video_path

    def mark_failed(self, room_id: str, error: str):
        s = self._sessions.get(room_id)
        if s:
            s.state = SessionState.FAILED

    # ------------------------------------------------------------------
    # Command handlers (return reply strings)
    # ------------------------------------------------------------------

    def handle_title(self, room_id: str, title: str) -> str:
        s = self.get_session(room_id)
        if not s or s.state not in (SessionState.BRAINSTORMING, SessionState.CONFIRMING):
            return "No active brainstorming session. Use `!video_start` to begin."
        s.title = title.strip()
        s.state = SessionState.BRAINSTORMING
        return f"✅ Video title set to: **{s.title}**"

    def handle_style(self, room_id: str, style_arg: str) -> str:
        s = self.get_session(room_id)
        if not s or s.state not in (SessionState.BRAINSTORMING, SessionState.CONFIRMING):
            return "No active brainstorming session. Use `!video_start` to begin."
        key = style_arg.strip().lower()
        if key not in VIDEO_STYLE_NAMES:
            options = ", ".join(f"`{k}`" for k in VIDEO_STYLE_NAMES)
            return f"❌ Unknown style `{key}`. Available styles: {options}"
        s.style_key = key
        s.state = SessionState.BRAINSTORMING
        return f"✅ Visual style set to: {s.style_display}"

    def handle_prompt(self, room_id: str, prompt_text: str) -> str:
        s = self.get_session(room_id)
        if not s or s.state not in (SessionState.BRAINSTORMING, SessionState.CONFIRMING):
            return "No active brainstorming session. Use `!video_start` to begin."
        s.custom_prompt = prompt_text.strip()
        s.state = SessionState.BRAINSTORMING
        return (
            f"✅ Content prompt set ({len(s.custom_prompt)} chars).\n\n"
            "Use `!video_preview` to review the full prompt, or `!video_confirm` when ready."
        )

    def handle_preview(self, room_id: str) -> str:
        s = self.get_session(room_id)
        if not s:
            return "No active video planning session. Use `!video_start` to begin."
        return s.preview_text()

    def handle_confirm(self, room_id: str, confirmed_by: str) -> tuple[str, Optional[VideoSession]]:
        """
        Handle !video_confirm.
        Returns (reply_message, session_if_ready_to_generate).
        If the session is not ready, returns a message and None.
        """
        s = self.get_session(room_id)
        if not s:
            return ("No active video planning session. Use `!video_start` to begin.", None)
        if s.state == SessionState.GENERATING:
            return ("⏳ Video generation is already in progress. Please wait.", None)
        ready, missing = s.is_ready_to_confirm()
        if not ready:
            items = "\n".join(f"  - {m}" for m in missing)
            return (f"⚠️ Cannot generate yet — please set:\n{items}", None)
        s.state = SessionState.CONFIRMING
        s.confirmed_by = confirmed_by
        s.state = SessionState.GENERATING
        return (
            f"✅ Confirmed by {confirmed_by}. Starting video generation now...\n"
            "⏳ This may take several minutes. I'll post the download link when it's ready.",
            s,
        )

    def record_brainstorm_message(self, room_id: str, sender: str, text: str):
        """Record a free-form message as a brainstorming note."""
        s = self.get_session(room_id)
        if s and s.state == SessionState.BRAINSTORMING:
            s.add_brainstorm_note(sender, text)

    # ------------------------------------------------------------------
    # Style listing
    # ------------------------------------------------------------------

    @staticmethod
    def styles_help_text(saved_styles: list[dict]) -> str:
        """Return a formatted list of all styles plus saved favourites."""
        lines = [
            "## 🎨 Available Video Styles",
            "",
            "Use `!video_style <name>` to select a style.",
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
                "| Name | Style | Notes |",
                "|---|---|---|",
            ]
            for ss in saved_styles:
                lines.append(
                    f"| `{ss['name']}` | `{ss['style_key']}` | {ss.get('notes') or ''} |"
                )
            lines += [
                "",
                "Use `!video_style <saved_name>` to apply a saved favourite.",
            ]
        return "\n".join(lines)
