"""
bot/consensus.py
================
Phase 4: Escalation & Group Consensus

When OSINT verification fails to explain a user's absence, this module:
  1. Posts a formal ALERT to the Matrix group room.
  2. Presents the group with a consensus voting prompt.
  3. Tracks `!activate_switch <username>` votes from group members.
  4. Triggers the decryption/release pipeline once the consensus threshold is met.

Security constraints:
  - The bot itself cannot unilaterally activate the switch.
  - Each group member may vote only once per target user.
  - The target user cannot vote for themselves.
  - Votes are persisted in the database to survive bot restarts.
"""

import logging
from typing import Dict, Any, Callable, Awaitable, Set

from db.database import Database

logger = logging.getLogger(__name__)

ALERT_MESSAGE_TEMPLATE = (
    "🚨 **WELLNESS ALERT — ACTION REQUIRED** 🚨\n\n"
    "**{display_name}** (`{matrix_id}`) has exceeded their missing threshold "
    "of **{threshold_h} hours** and has not been heard from.\n\n"
    "**Automated Safety Checks Result:** {osint_summary}\n\n"
    "No legitimate reason for their absence has been found through automated checks.\n\n"
    "---\n"
    "**Group Consensus Required**\n\n"
    "To activate {display_name}'s Dead Man's Switch and release their emergency data, "
    "**{threshold_votes} group members** must type:\n\n"
    "```\n!activate_switch {matrix_id}\n```\n\n"
    "Current votes: **0 / {threshold_votes}**\n\n"
    "⚠️ This action is irreversible. Only activate if you genuinely believe "
    "{display_name} may be in danger or unreachable."
)

VOTE_RECORDED_TEMPLATE = (
    "Vote recorded from **{voter_display}**. "
    "Current consensus for `{target_id}`: **{current_votes} / {threshold_votes}**."
)

ALREADY_VOTED_MESSAGE = (
    "You have already cast your vote for this activation. "
    "Each member may vote only once."
)

SELF_VOTE_MESSAGE = (
    "You cannot vote to activate your own switch."
)

NOT_ESCALATED_MESSAGE = (
    "User `{target_id}` is not currently in an escalated missing state. "
    "No vote is needed at this time."
)

CONSENSUS_REACHED_TEMPLATE = (
    "✅ **CONSENSUS REACHED** — {current_votes}/{threshold_votes} members have voted.\n\n"
    "Activating the Dead Man's Switch for **{display_name}** (`{target_id}`).\n"
    "Decrypting and releasing emergency data now..."
)


class ConsensusManager:
    """
    Manages the escalation alerts and group voting process.
    """

    def __init__(
        self,
        db: Database,
        consensus_threshold: int,
        on_consensus_reached: Callable[[Dict[str, Any]], Awaitable[None]],
        send_group_message: Callable[[str], Awaitable[None]],
    ):
        """
        Args:
            db: Database instance.
            consensus_threshold: Number of votes required to activate the switch.
            on_consensus_reached: Async callback invoked when threshold is met.
                                  Receives the target user dict.
            send_group_message: Async callable to post a message to the group room.
        """
        self.db = db
        self.consensus_threshold = consensus_threshold
        self.on_consensus_reached = on_consensus_reached
        self.send_group_message = send_group_message

    async def post_alert(self, user: Dict[str, Any], osint_summary: str):
        """
        Post a formal ALERT message to the group room for a missing user.
        """
        matrix_id = user["matrix_id"]
        display_name = user.get("display_name", matrix_id)
        threshold_h = user.get("missing_threshold_h", 72)

        alert_text = ALERT_MESSAGE_TEMPLATE.format(
            display_name=display_name,
            matrix_id=matrix_id,
            threshold_h=threshold_h,
            osint_summary=osint_summary,
            threshold_votes=self.consensus_threshold,
        )

        await self.send_group_message(alert_text)
        await self.db.log_event(
            event_type="GROUP_ALERT_POSTED",
            target_matrix_id=matrix_id,
            note=f"osint_summary={osint_summary[:200]}",
        )
        logger.info("Group alert posted for missing user %s", matrix_id)

    async def handle_activate_vote(
        self,
        voter_matrix_id: str,
        voter_display_name: str,
        target_matrix_id: str,
    ) -> str:
        """
        Process an `!activate_switch <target>` vote from a group member.

        Returns a message to send back to the group.
        """
        # Prevent self-voting
        if voter_matrix_id == target_matrix_id:
            return SELF_VOTE_MESSAGE

        # Verify target is in ESCALATED state
        target_user = await self.db.get_user(target_matrix_id)
        if not target_user or target_user.get("status") != "ESCALATED":
            return NOT_ESCALATED_MESSAGE.format(target_id=target_matrix_id)

        # Record the vote (returns False if duplicate)
        is_new_vote = await self.db.add_vote(
            target_matrix_id=target_matrix_id,
            voter_matrix_id=voter_matrix_id,
        )

        if not is_new_vote:
            return ALREADY_VOTED_MESSAGE

        # Count current votes
        current_votes = await self.db.count_votes(target_matrix_id)

        await self.db.log_event(
            event_type="CONSENSUS_VOTE_CAST",
            actor_matrix_id=voter_matrix_id,
            target_matrix_id=target_matrix_id,
            note=f"votes={current_votes}/{self.consensus_threshold}",
        )

        logger.info(
            "Vote cast by %s for %s: %d/%d",
            voter_matrix_id,
            target_matrix_id,
            current_votes,
            self.consensus_threshold,
        )

        if current_votes >= self.consensus_threshold:
            # Consensus reached — trigger release
            consensus_msg = CONSENSUS_REACHED_TEMPLATE.format(
                current_votes=current_votes,
                threshold_votes=self.consensus_threshold,
                display_name=target_user.get("display_name", target_matrix_id),
                target_id=target_matrix_id,
            )
            await self.send_group_message(consensus_msg)
            await self.db.log_event(
                event_type="CONSENSUS_REACHED",
                target_matrix_id=target_matrix_id,
                note=f"votes={current_votes}",
            )

            # Clear votes to prevent re-triggering
            await self.db.clear_votes(target_matrix_id)

            # Trigger the release pipeline
            try:
                await self.on_consensus_reached(target_user)
            except Exception as exc:
                logger.error(
                    "on_consensus_reached callback failed for %s: %s",
                    target_matrix_id,
                    exc,
                    exc_info=True,
                )
            return ""  # Consensus message already sent to group

        else:
            # Vote recorded, not yet at threshold
            return VOTE_RECORDED_TEMPLATE.format(
                voter_display=voter_display_name,
                target_id=target_matrix_id,
                current_votes=current_votes,
                threshold_votes=self.consensus_threshold,
            )

    async def handle_cancel_alert(
        self, admin_matrix_id: str, target_matrix_id: str
    ) -> str:
        """
        Allow an admin to cancel an active alert (e.g., user checked in via phone).
        Command: !cancel_alert <target_matrix_id>
        """
        target_user = await self.db.get_user(target_matrix_id)
        if not target_user:
            return f"User `{target_matrix_id}` is not registered."

        if target_user.get("status") not in ("MISSING", "ESCALATED"):
            return f"User `{target_matrix_id}` is not currently in a missing/escalated state."

        await self.db.update_last_active(target_matrix_id)
        await self.db.clear_votes(target_matrix_id)
        await self.db.log_event(
            event_type="ALERT_CANCELLED_BY_ADMIN",
            actor_matrix_id=admin_matrix_id,
            target_matrix_id=target_matrix_id,
        )

        display_name = target_user.get("display_name", target_matrix_id)
        logger.info(
            "Alert for %s cancelled by admin %s", target_matrix_id, admin_matrix_id
        )
        return (
            f"Alert for **{display_name}** (`{target_matrix_id}`) has been cancelled "
            f"by an administrator. Their timer has been reset."
        )
