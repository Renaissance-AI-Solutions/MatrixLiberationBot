"""
bot/foia_session.py
===================
Liberation Bot — FOIA Request Session State Manager

Holds the mutable state of a single FOIA drafting session.  All
question-asking, jurisdiction selection, and letter generation is handled
by the FOIADialogueAgent (agent/foia_dialogue.py).  This module is a pure
data container and lifecycle manager — it contains no LLM logic.

Session lifecycle:
  INTAKE       →  DRAFTING  →  REVIEW  →  FINALIZED / CANCELLED

  INTAKE     : Bot is gathering jurisdiction, agency, and subject info.
  DRAFTING   : LLM is building the request letter through dialogue.
  REVIEW     : Bot has posted the draft letter; waiting for !foia_confirm
               or !foia_revise <notes>.
  FINALIZED  : User confirmed the letter; submission instructions delivered.
  CANCELLED  : Session was cancelled by the user.

DM Commands available to users:
  !foia_start                — Begin a new FOIA drafting session.
  !foia_jurisdictions        — List all supported jurisdictions.
  !foia_agencies             — List recommended federal agencies for AHI requests.
  !foia_preview              — Re-show the current draft letter at any time.
  !foia_revise <notes>       — Ask the bot to revise the draft based on feedback.
  !foia_confirm              — Accept the draft and receive submission instructions.
  !foia_cancel               — Cancel the current session.
  !foia_history              — Show your past finalized FOIA requests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional

from agent.tools.foia_jurisdictions import (
    get_jurisdiction,
    format_jurisdiction_summary,
    FEDERAL_AHI_AGENCIES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class FOIASessionState(Enum):
    INTAKE     = auto()   # Gathering jurisdiction / agency / subject
    DRAFTING   = auto()   # LLM is building the letter through dialogue
    REVIEW     = auto()   # Draft posted; waiting for confirm or revise
    FINALIZED  = auto()   # User confirmed; submission instructions sent
    CANCELLED  = auto()   # Session cancelled


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

@dataclass
class FOIASession:
    """
    Holds the mutable state of one FOIA drafting session.

    The LLM dialogue agent reads and writes `jurisdiction_code`,
    `target_agency`, `subject_summary`, `date_range`, `keywords`,
    `requester_name`, `requester_contact`, `fee_waiver_requested`,
    `fee_waiver_justification`, `expedited_requested`, `draft_letter`,
    and `dialogue_history`.

    The room/DM handler reads `state` to decide how to route messages.
    """

    room_id:    str
    sender_id:  str                         # Matrix user ID of session creator
    state:      FOIASessionState = FOIASessionState.INTAKE

    # --- Jurisdiction & agency (set during INTAKE) ---
    jurisdiction_code: Optional[str] = None   # e.g. "FEDERAL", "CA", "NY"
    target_agency:     Optional[str] = None   # e.g. "CIA", "California DOJ"

    # --- Request content (set by the LLM during DRAFTING) ---
    subject_summary:            Optional[str] = None
    date_range:                 Optional[str] = None   # e.g. "January 2016 – present"
    keywords:                   Optional[str] = None   # comma-separated search terms
    requester_name:             Optional[str] = None
    requester_contact:          Optional[str] = None   # email or mailing address
    fee_waiver_requested:       bool = False
    fee_waiver_justification:   Optional[str] = None
    expedited_requested:        bool = False
    expedited_justification:    Optional[str] = None

    # --- Generated letter ---
    draft_letter: Optional[str] = None

    # --- Dialogue history for the LLM context window ---
    # Each entry: {"role": "user"|"assistant", "sender": str, "content": str}
    dialogue_history: list[dict] = field(default_factory=list)

    # --- Confirmation tracking ---
    confirmed_at: Optional[datetime] = None

    # --- DB record ID (set by bot.py after DB insert) ---
    _db_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def jurisdiction_info(self) -> Optional[dict]:
        """Return the jurisdiction metadata dict, or None if not yet set."""
        if not self.jurisdiction_code:
            return None
        return get_jurisdiction(self.jurisdiction_code)

    @property
    def is_federal(self) -> bool:
        return self.jurisdiction_code == "FEDERAL"

    @property
    def is_complete(self) -> bool:
        """True when the LLM has filled in all required fields for a valid letter."""
        return bool(
            self.jurisdiction_code
            and self.target_agency
            and self.subject_summary
            and self.requester_name
            and self.requester_contact
            and self.draft_letter
        )

    @property
    def missing_fields(self) -> list[str]:
        """Return a list of field names that are still unset."""
        missing = []
        if not self.jurisdiction_code:   missing.append("jurisdiction")
        if not self.target_agency:       missing.append("target agency")
        if not self.subject_summary:     missing.append("subject / records description")
        if not self.requester_name:      missing.append("your full name")
        if not self.requester_contact:   missing.append("your contact information (email or address)")
        if not self.draft_letter:        missing.append("draft letter")
        return missing

    # ------------------------------------------------------------------
    # Dialogue history helpers
    # ------------------------------------------------------------------

    def add_user_message(self, sender: str, content: str) -> None:
        """Record a user message in the dialogue history."""
        self.dialogue_history.append({
            "role": "user",
            "sender": sender,
            "content": content,
        })

    def add_assistant_message(self, content: str) -> None:
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
        LLM can distinguish between multiple participants in a group room.
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
    # Preview helpers
    # ------------------------------------------------------------------

    def preview_text(self) -> str:
        """Generate the review-stage preview shown before confirmation."""
        j = self.jurisdiction_info
        law_name = j["law_name"] if j else "Unknown"
        citation  = j["citation"] if j else "Unknown"

        lines = [
            "## 📄 FOIA Request Draft — Preview",
            "",
            f"**Jurisdiction:** {self.jurisdiction_code or '_not set_'} — {law_name}",
            f"**Citation:** `{citation}`",
            f"**Target Agency:** {self.target_agency or '_not set_'}",
            f"**Subject:** {self.subject_summary or '_not set_'}",
            f"**Date Range:** {self.date_range or '_not specified_'}",
            f"**Keywords:** {self.keywords or '_not specified_'}",
            f"**Requester Name:** {self.requester_name or '_not set_'}",
            f"**Contact:** {self.requester_contact or '_not set_'}",
            f"**Fee Waiver Requested:** {'Yes' if self.fee_waiver_requested else 'No'}",
            f"**Expedited Processing Requested:** {'Yes' if self.expedited_requested else 'No'}",
            "",
        ]

        if self.draft_letter:
            lines += [
                "**Draft Letter:**",
                "```",
                self.draft_letter,
                "```",
                "",
            ]

        if self.is_complete:
            lines += [
                "✅ **Draft is ready for review.**",
                "",
                "Type `!foia_confirm` to accept this draft and receive submission instructions.",
                "Type `!foia_revise <your notes>` to ask the bot to make changes.",
            ]
        else:
            lines += [
                f"⚠️ **Still working on:** {', '.join(self.missing_fields)}",
                "Continue the conversation and the bot will fill these in.",
            ]

        return "\n".join(lines)

    def submission_instructions(self) -> str:
        """
        Return submission instructions for the finalized request, including
        the agency contact, expected response timeline, and next steps.
        """
        j = self.jurisdiction_info
        if not j:
            return "Submission instructions unavailable — jurisdiction not set."

        response_note = j.get("response_note", "See applicable law for deadlines.")
        appeal_body   = j.get("appeal_body", "See applicable law for appeal procedures.")

        # For federal requests, find the specific agency's contact info
        agency_contact_block = ""
        if self.is_federal:
            for agency in FEDERAL_AHI_AGENCIES:
                if (
                    agency["abbreviation"].upper() in (self.target_agency or "").upper()
                    or agency["name"].lower() in (self.target_agency or "").lower()
                ):
                    agency_contact_block = (
                        f"\n**FOIA Email:** `{agency['foia_email']}`"
                        f"\n**Online Portal:** {agency['foia_portal']}"
                        f"\n**Notes:** {agency['notes']}"
                    )
                    break

        lines = [
            "## ✅ FOIA Request Finalized — Submission Instructions",
            "",
            f"**Jurisdiction:** {j['name']} — {j['law_name']}",
            f"**Citation:** `{j['citation']}`",
            f"**Expected Response:** {response_note}",
            agency_contact_block,
            "",
            "**Next Steps:**",
            "1. Copy the letter above and submit it via the agency's preferred method.",
            "2. Keep a copy of your request and note the submission date.",
            "3. If the agency does not respond within the legal deadline, you may file an appeal.",
            f"4. **Appeal Body:** {appeal_body}",
            "",
            "**Tips for Success:**",
            "- Follow up with the agency's FOIA office after 2–3 weeks if you receive no acknowledgment.",
            "- If denied, always appeal — many initial denials are reversed on appeal.",
            "- For national security agencies (CIA, NSA, DOD), expect delays of months to years.",
            "- Consider filing with MuckRock (muckrock.com) to track your request publicly.",
            "",
            "Use `!foia_history` to view all your past FOIA requests.",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class FOIASessionManager:
    """
    Manages one active FOIASession per user (DM context).
    One user → one active session at a time.
    """

    def __init__(self):
        # Maps sender_id -> FOIASession
        self._sessions: dict[str, FOIASession] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self, room_id: str, sender_id: str
    ) -> tuple[bool, "FOIASession | str"]:
        """
        Start a new FOIA drafting session for the user.
        Returns (True, session) on success.
        Returns (False, error_message) if a session is already active.
        """
        existing = self._sessions.get(sender_id)
        if existing and existing.state not in (
            FOIASessionState.FINALIZED, FOIASessionState.CANCELLED
        ):
            return (
                False,
                "⚠️ You already have an active FOIA drafting session.\n"
                "Use `!foia_cancel` to cancel it before starting a new one, "
                "or `!foia_preview` to see your current draft.",
            )
        session = FOIASession(room_id=room_id, sender_id=sender_id)
        self._sessions[sender_id] = session
        logger.info("FOIA session started for %s in room %s", sender_id, room_id)
        return (True, session)

    def cancel_session(self, sender_id: str) -> str:
        """Cancel the active session for a user."""
        session = self._sessions.pop(sender_id, None)
        if not session:
            return "No active FOIA drafting session. Use `!foia_start` to begin one."
        session.state = FOIASessionState.CANCELLED
        logger.info("FOIA session cancelled for %s", sender_id)
        return (
            "❌ FOIA drafting session cancelled.\n"
            "Use `!foia_start` to begin a new one at any time."
        )

    def get_session(self, sender_id: str) -> Optional[FOIASession]:
        """Return the active session for a user, or None."""
        return self._sessions.get(sender_id)

    def has_active_session(self, sender_id: str) -> bool:
        """Return True if the user has a non-terminal session."""
        s = self._sessions.get(sender_id)
        return s is not None and s.state not in (
            FOIASessionState.FINALIZED, FOIASessionState.CANCELLED
        )

    def finalize_session(self, sender_id: str) -> Optional[FOIASession]:
        """
        Transition the session to FINALIZED state.
        Returns the session on success, None if no active session exists.
        """
        s = self._sessions.get(sender_id)
        if s and s.state == FOIASessionState.REVIEW:
            s.state = FOIASessionState.FINALIZED
            s.confirmed_at = datetime.now(timezone.utc)
            logger.info("FOIA session finalized for %s", sender_id)
            return s
        return None

    def transition_to_drafting(self, sender_id: str) -> bool:
        """Move session from INTAKE to DRAFTING. Returns True on success."""
        s = self._sessions.get(sender_id)
        if s and s.state == FOIASessionState.INTAKE:
            s.state = FOIASessionState.DRAFTING
            return True
        return False

    def transition_to_review(self, sender_id: str) -> bool:
        """Move session from DRAFTING to REVIEW. Returns True on success."""
        s = self._sessions.get(sender_id)
        if s and s.state == FOIASessionState.DRAFTING:
            s.state = FOIASessionState.REVIEW
            return True
        return False

    def reopen_for_revision(self, sender_id: str) -> bool:
        """Move session from REVIEW back to DRAFTING for revision. Returns True on success."""
        s = self._sessions.get(sender_id)
        if s and s.state == FOIASessionState.REVIEW:
            s.state = FOIASessionState.DRAFTING
            return True
        return False
