"""
agent/tools/dms_tools.py
========================
Liberation Bot — Dead Man's Switch Status Tool

Provides a single read-only tool that the Kimi K2 agent can call to answer
member questions about their Dead Man's Switch (DMS) configuration and
heartbeat timer status.

Security contract (enforced in code, not just documentation):
  - sender_id is injected by AgentCore._execute_tool_call — the agent
    cannot query another user's DMS status even if it tries to pass a
    different matrix_id.
  - The underlying get_dms_status() DB method NEVER returns:
      vault_text, legal_name, date_of_birth, physical_address,
      emergency_contacts details, or OTP data.
  - The agent is explicitly instructed NOT to trigger DMS actions —
    it is a read-only informational tool only.
  - The agent cannot modify threshold, emergency contacts, or vault
    content via this tool. Those operations remain portal-only.

Typical agent use cases:
  - "What is my heartbeat threshold?" → get_dms_status
  - "How long until my DMS triggers?" → get_dms_status
  - "Is my dead man's switch configured?" → get_dms_status
  - "When did I last check in?" → get_dms_status
  - "Do I have emergency contacts set up?" → get_dms_status (count only)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.database import Database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool function
# ---------------------------------------------------------------------------

async def get_dms_status(
    db: "Database",
    sender_id: str,
) -> str:
    """
    Retrieve a safe summary of the current member's Dead Man's Switch status.

    sender_id is injected by AgentCore._execute_tool_call — the agent cannot
    query another user's status. This function returns a formatted plain-text
    summary suitable for display in a Matrix room.

    Args:
        db:         Database instance (injected, not from agent args).
        sender_id:  Matrix ID of the current user (injected, not from agent args).

    Returns:
        A formatted plain-text status summary, or an error/guidance message.
    """
    if not db:
        return (
            "[DMS Status Unavailable] The database is not connected. "
            "Please contact the NPWA administrator."
        )

    status_data = await db.get_dms_status(sender_id)

    if status_data is None:
        return (
            "⚠️ I encountered an error retrieving your DMS status. "
            "Please try again or use `!my_status` directly."
        )

    if not status_data.get("registered"):
        return (
            "You are not currently registered with the Dead Man's Switch system. "
            "To register, send `!register_switch` in a direct message to the bot, "
            "or visit the member portal to complete your profile."
        )

    # Build the status summary
    s = status_data
    status_label = s["status"]
    elapsed_h = s["elapsed_h"]
    threshold_h = s["threshold_h"]
    time_remaining_h = s["time_remaining_h"]
    last_active_str = s["last_active_str"]
    vault_configured = s["vault_configured"]
    contacts_count = s["emergency_contacts_count"]
    has_release_actions = s["has_release_actions"]

    # Status indicator
    if status_label == "ACTIVE":
        status_icon = "🟢"
    elif status_label == "MISSING":
        status_icon = "🔴"
    elif status_label == "ESCALATED":
        status_icon = "🚨"
    elif status_label == "RELEASED":
        status_icon = "📤"
    else:
        status_icon = "⚪"

    # Timer urgency indicator
    if time_remaining_h <= 0:
        timer_line = f"⚠️ **OVERDUE** — your threshold was exceeded {abs(elapsed_h - threshold_h):.1f} hours ago"
    elif time_remaining_h < 12:
        timer_line = f"⚠️ **{time_remaining_h:.1f} hours remaining** before alert triggers — consider checking in soon"
    elif time_remaining_h < 24:
        timer_line = f"🟡 **{time_remaining_h:.1f} hours remaining** before alert triggers"
    else:
        timer_line = f"✅ **{time_remaining_h:.1f} hours remaining** before alert triggers"

    # Configuration completeness
    config_lines = []
    if vault_configured:
        config_lines.append("✅ Emergency vault message configured")
    else:
        config_lines.append("⚠️ No emergency vault message — visit the portal to add one")

    if contacts_count > 0:
        config_lines.append(f"✅ {contacts_count} emergency contact{'s' if contacts_count != 1 else ''} configured")
    else:
        config_lines.append("⚠️ No emergency contacts — visit the portal to add contacts")

    if has_release_actions:
        config_lines.append("✅ Release actions configured")
    else:
        config_lines.append("⚠️ No release actions configured — visit the portal to set them up")

    config_block = "\n".join(f"  {line}" for line in config_lines)

    summary = (
        f"**Your Dead Man's Switch Status**\n\n"
        f"{status_icon} **Status:** {status_label}\n"
        f"🕐 **Last activity:** {last_active_str}\n"
        f"⏱️ **Elapsed:** {elapsed_h:.1f} hours\n"
        f"🎯 **Your threshold:** {threshold_h} hours\n"
        f"{timer_line}\n\n"
        f"**Configuration:**\n{config_block}\n\n"
        f"Use `!checkin` to reset your timer. "
        f"Visit the member portal to update your vault message, "
        f"emergency contacts, or release actions."
    )

    return summary


# ---------------------------------------------------------------------------
# OpenAI Tool Schema
# ---------------------------------------------------------------------------

GET_DMS_STATUS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_dms_status",
        "description": (
            "Retrieve the current member's Dead Man's Switch (DMS) status, "
            "including their heartbeat timer, threshold, last check-in time, "
            "and configuration completeness (vault, emergency contacts, release actions). "
            "Use this when a member asks about their DMS, heartbeat timer, "
            "how long until their switch triggers, when they last checked in, "
            "or whether their emergency setup is complete. "
            "This tool is READ-ONLY — it cannot modify any DMS settings. "
            "To update settings, the member must use the portal."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}
