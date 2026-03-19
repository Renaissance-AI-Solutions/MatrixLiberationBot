"""
bot/release.py
==============
Phase 5: Decryption & Emergency Data Release

Called ONLY after the group consensus threshold has been verified by the
ConsensusManager. This module:
  1. Retrieves the encrypted vault entry from the database.
  2. Decrypts the Emergency Data using the master key.
  3. Transmits the decrypted data to the designated Matrix group room.
  4. Marks the vault entry as released in the database.
  5. Clears the plaintext from memory immediately after transmission.

Security guarantee: This function is the ONLY code path that calls
decrypt_emergency_data(). It is invoked exclusively via the consensus
callback — never directly from any command handler.
"""

import logging
from typing import Dict, Any, Callable, Awaitable

from db.database import Database
from security.encryption import decrypt_emergency_data, unpack_vault_blob

logger = logging.getLogger(__name__)

RELEASE_HEADER_TEMPLATE = (
    "🔓 **EMERGENCY DATA RELEASE — CONSENSUS ACTIVATED**\n\n"
    "The group has reached consensus to activate the Dead Man's Switch for "
    "**{display_name}** (`{matrix_id}`).\n\n"
    "The following emergency data has been decrypted and is being released "
    "to this group as requested by {display_name} during registration.\n\n"
    "---\n"
    "**HANDLE THIS INFORMATION WITH CARE AND DISCRETION.**\n"
    "---\n\n"
)

RELEASE_FOOTER = (
    "\n\n---\n"
    "*End of emergency data release. This message was generated automatically "
    "by the Matrix Wellness Monitor upon group consensus. "
    "The vault entry has been marked as released.*"
)

RELEASE_ERROR_MESSAGE = (
    "⚠️ **CRITICAL ERROR during emergency data release for `{matrix_id}`.**\n\n"
    "The decryption process failed. This may indicate data corruption or a "
    "configuration issue. Please contact the bot administrator immediately.\n\n"
    "Error detail: {error}"
)

NO_VAULT_MESSAGE = (
    "⚠️ No emergency data vault entry found for `{matrix_id}`. "
    "The user may not have completed their registration, or the data was "
    "previously released or deleted."
)


class ReleaseManager:
    """
    Handles the final decryption and release of emergency data upon consensus.
    """

    def __init__(
        self,
        db: Database,
        master_key_hex: str,
        send_group_message: Callable[[str], Awaitable[None]],
    ):
        """
        Args:
            db: Database instance.
            master_key_hex: The AES-256 master key (hex string) from config.
            send_group_message: Async callable to post a message to the group room.
        """
        self.db = db
        self.master_key_hex = master_key_hex
        self.send_group_message = send_group_message

    async def release(self, user: Dict[str, Any]):
        """
        Decrypt and release the emergency data for the given user.

        This method is the sole authorised entry point for decryption.
        It must only be called from the consensus callback.
        """
        matrix_id = user["matrix_id"]
        display_name = user.get("display_name", matrix_id)

        logger.critical(
            "EMERGENCY DATA RELEASE INITIATED for %s (%s)",
            matrix_id,
            display_name,
        )

        # 1. Retrieve vault entry
        vault = await self.db.get_emergency_data(matrix_id)
        if not vault:
            logger.error("No vault entry found for %s", matrix_id)
            await self.send_group_message(
                NO_VAULT_MESSAGE.format(matrix_id=matrix_id)
            )
            return

        # 2. Unpack the stored blob into ciphertext + salt
        blob: bytes = vault["encrypted_data"]
        iv: bytes = vault["iv"]

        try:
            ciphertext, salt = unpack_vault_blob(blob)
        except Exception as exc:
            logger.error("Failed to unpack vault blob for %s: %s", matrix_id, exc)
            await self.send_group_message(
                RELEASE_ERROR_MESSAGE.format(matrix_id=matrix_id, error=str(exc))
            )
            return

        # 3. Decrypt
        plaintext: str = ""
        try:
            plaintext = decrypt_emergency_data(
                ciphertext=ciphertext,
                iv=iv,
                salt=salt,
                master_key_hex=self.master_key_hex,
            )
        except Exception as exc:
            logger.error(
                "Decryption failed for %s: %s", matrix_id, exc, exc_info=True
            )
            await self.send_group_message(
                RELEASE_ERROR_MESSAGE.format(matrix_id=matrix_id, error=str(exc))
            )
            return

        # 4. Transmit to group
        header = RELEASE_HEADER_TEMPLATE.format(
            display_name=display_name,
            matrix_id=matrix_id,
        )
        full_message = header + plaintext + RELEASE_FOOTER

        try:
            await self.send_group_message(full_message)
            logger.critical(
                "Emergency data successfully released to group for %s", matrix_id
            )
        except Exception as exc:
            logger.error(
                "Failed to send release message to group for %s: %s",
                matrix_id,
                exc,
                exc_info=True,
            )
            # Do not re-raise; mark as released anyway to prevent double-release
        finally:
            # 5. Zero out plaintext from memory
            del plaintext
            del ciphertext

        # 6. Mark vault as released in the database
        await self.db.mark_vault_released(matrix_id)
        await self.db.set_user_status(matrix_id, "RELEASED")
        await self.db.log_event(
            event_type="EMERGENCY_DATA_RELEASED",
            target_matrix_id=matrix_id,
        )

        logger.info(
            "Vault entry marked as released for %s. Release pipeline complete.",
            matrix_id,
        )
