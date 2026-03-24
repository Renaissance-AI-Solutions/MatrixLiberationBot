"""
dms-ui/backend/matrix_otp.py
=============================
Delivers OTP codes to users via Matrix DM using the bot's credentials.

The bot's access token is used directly (same account, same homeserver).
If MATRIX_BOT_ACCESS_TOKEN is not set, it attempts to obtain one using
MATRIX_BOT_USER_ID + MATRIX_BOT_PASSWORD on startup.

In development mode (no credentials configured), the OTP is logged to
the console so developers can test without a live Matrix homeserver.
"""

import logging
import os
import secrets

import httpx

logger = logging.getLogger(__name__)

HOMESERVER = os.getenv("MATRIX_HOMESERVER_URL", "")
BOT_USER_ID = os.getenv("MATRIX_BOT_USER_ID", "")
BOT_PASSWORD = os.getenv("MATRIX_BOT_PASSWORD", "")
_access_token: str = os.getenv("MATRIX_BOT_ACCESS_TOKEN", "")

OTP_MESSAGE = """\
🔐 **Liberation Bot — Dead Man's Switch Portal Login**

Your one-time login code is:

# {otp}

This code is valid for **10 minutes** and can only be used once.

If you did not request this, please ignore this message.
"""


async def _ensure_token() -> str:
    """Return the bot access token, obtaining one via password login if needed."""
    global _access_token
    if _access_token:
        return _access_token
    if not HOMESERVER or not BOT_USER_ID or not BOT_PASSWORD:
        return ""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{HOMESERVER}/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": BOT_USER_ID},
                "password": BOT_PASSWORD,
            },
        )
        if resp.status_code == 200:
            _access_token = resp.json().get("access_token", "")
            logger.info("Obtained Matrix access token via password login.")
        else:
            logger.error("Matrix login failed: %s %s", resp.status_code, resp.text)
    return _access_token


async def send_otp_dm(matrix_id: str, otp: str) -> bool:
    """
    Send the OTP to the user via Matrix DM.
    Returns True on success, False on failure.
    Falls back to console logging in dev mode.
    """
    token = await _ensure_token()

    if not token:
        # Dev-mode fallback: log to console
        logger.warning(
            "DEV MODE — No Matrix credentials configured. "
            "OTP for %s: %s",
            matrix_id,
            otp,
        )
        return True  # Treat as success so the UI flow continues in dev

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    message = OTP_MESSAGE.format(otp=otp)

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Create or retrieve a DM room
        create_resp = await client.post(
            f"{HOMESERVER}/_matrix/client/v3/createRoom",
            headers=headers,
            json={
                "invite": [matrix_id],
                "is_direct": True,
                "preset": "trusted_private_chat",
            },
        )
        if create_resp.status_code not in (200, 201):
            logger.error(
                "createRoom failed for %s: %s %s",
                matrix_id,
                create_resp.status_code,
                create_resp.text,
            )
            return False

        room_id = create_resp.json().get("room_id")
        if not room_id:
            logger.error("No room_id in createRoom response for %s", matrix_id)
            return False

        # Send the OTP message
        import uuid as _uuid
        txn_id = _uuid.uuid4().hex
        send_resp = await client.put(
            f"{HOMESERVER}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}",
            headers=headers,
            json={"msgtype": "m.text", "body": message},
        )
        if send_resp.status_code == 200:
            logger.info("OTP sent to %s via Matrix DM.", matrix_id)
            return True
        else:
            logger.error(
                "Failed to send OTP message to %s: %s %s",
                matrix_id,
                send_resp.status_code,
                send_resp.text,
            )
            return False


def generate_otp() -> str:
    """Generate a cryptographically secure 6-digit OTP."""
    return str(secrets.randbelow(900000) + 100000)
