"""
dms-ui/backend/main.py
=======================
Liberation Bot — Dead Man's Switch Web UI Backend

FastAPI application providing:
  POST /api/auth/request   — Request OTP (sends via Matrix DM)
  POST /api/auth/verify    — Verify OTP, receive JWT session
  GET  /api/profile        — Fetch full combined profile
  PUT  /api/profile        — Update profile (structured fields + vault text)
  POST /api/checkin        — Manual check-in (resets the bot's timer)
  GET  /api/audit          — Audit log for this user
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, field_validator

from db import DMSDB
from matrix_otp import generate_otp, send_otp_dm

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_PATH = os.getenv("DATABASE_PATH", "../../data/liberation_bot.db")
JWT_SECRET = os.getenv("DMS_JWT_SECRET", "change-me")
JWT_ALGORITHM = "HS256"
SESSION_HOURS = int(os.getenv("DMS_SESSION_HOURS", "8"))
OTP_EXPIRE_MINUTES = 10
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Liberation Bot — DMS Web UI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = DMSDB(DATABASE_PATH)
pwd_ctx = CryptContext(schemes=["argon2"], deprecated="auto")
bearer = HTTPBearer()


@app.on_event("startup")
async def startup():
    await db.connect()
    logger.info("DMS UI backend started.")


@app.on_event("shutdown")
async def shutdown():
    await db.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _create_token(matrix_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    return jwt.encode(
        {"sub": matrix_id, "type": "dms_ui", "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


async def _get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "dms_ui":
            raise HTTPException(status_code=401, detail="Invalid token type")
        matrix_id: str = payload.get("sub")
        if not matrix_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return matrix_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expired or invalid")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class OTPRequest(BaseModel):
    matrix_id: str

    @field_validator("matrix_id")
    @classmethod
    def validate_matrix_id(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.startswith("@") or ":" not in v:
            raise ValueError("Must be a valid Matrix ID, e.g. @alice:matrix.org")
        return v


class OTPVerify(BaseModel):
    matrix_id: str
    otp: str

    @field_validator("matrix_id")
    @classmethod
    def validate_matrix_id(cls, v: str) -> str:
        return v.strip().lower()


class EmergencyContact(BaseModel):
    name: str = ""
    relationship: str = ""
    phone: str = ""
    matrix_id: str = ""
    email: str = ""


class SocialMediaEntry(BaseModel):
    platform: str = ""
    url: str = ""


class ReleaseAction(BaseModel):
    type: str = "matrix_dm"   # matrix_dm | matrix_room | webhook
    target: str = ""


class ProfileUpdate(BaseModel):
    legal_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    physical_address: Optional[str] = None
    location: Optional[str] = None          # synced to bot's user_profiles.location
    emergency_contacts: Optional[List[EmergencyContact]] = None
    social_media: Optional[List[SocialMediaEntry]] = None
    vault_text: Optional[str] = None
    missing_threshold_h: Optional[int] = None
    release_actions: Optional[List[ReleaseAction]] = None


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth/request")
async def request_otp(body: OTPRequest, request: Request):
    """
    Generate a 6-digit OTP and deliver it to the user via Matrix DM.
    The user must already be registered with the bot (have a row in registered_users).
    """
    user = await db.get_registered_user(body.matrix_id)
    if not user:
        # Return a generic 200 to avoid leaking whether an account exists
        logger.info("OTP requested for unknown user %s — silently ignored.", body.matrix_id)
        return {"status": "sent"}

    otp = generate_otp()
    otp_hash = pwd_ctx.hash(otp)
    expires_ts = (datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MINUTES)).timestamp()

    await db.create_otp(body.matrix_id, otp_hash, expires_ts)
    await db.log_event("UI_OTP_REQUESTED", actor_matrix_id=body.matrix_id,
                       note=f"ip={request.client.host}")

    success = await send_otp_dm(body.matrix_id, otp)
    if not success:
        raise HTTPException(
            status_code=503,
            detail="Could not deliver OTP via Matrix. Is the bot running?",
        )

    return {"status": "sent"}


@app.post("/api/auth/verify")
async def verify_otp(body: OTPVerify, request: Request):
    """Verify the OTP and return a JWT session token."""
    challenge = await db.get_valid_otp(body.matrix_id)
    if not challenge:
        raise HTTPException(status_code=401, detail="No valid OTP found. Please request a new code.")

    if not pwd_ctx.verify(body.otp, challenge["otp_hash"]):
        await db.log_event("UI_OTP_FAILED", actor_matrix_id=body.matrix_id,
                           note=f"ip={request.client.host}")
        raise HTTPException(status_code=401, detail="Incorrect OTP code.")

    await db.consume_otp(challenge["id"])
    await db.log_event("UI_LOGIN", actor_matrix_id=body.matrix_id,
                       note=f"ip={request.client.host}")

    token = _create_token(body.matrix_id)
    return {"access_token": token, "token_type": "bearer", "matrix_id": body.matrix_id}


# ---------------------------------------------------------------------------
# Routes — Profile
# ---------------------------------------------------------------------------

@app.get("/api/profile")
async def get_profile(matrix_id: str = Depends(_get_current_user)):
    """Return the full combined profile for the authenticated user."""
    user = await db.get_registered_user(matrix_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found in bot database.")

    bot_profile = await db.get_bot_profile(matrix_id) or {}
    ui_profile = await db.get_ui_profile(matrix_id) or {}
    vault_meta = await db.get_vault_meta(matrix_id) or {}

    # Parse social_handles from bot profile (legacy format: {"twitter": "@handle"})
    legacy_handles: Dict[str, str] = {}
    if bot_profile.get("social_handles"):
        try:
            legacy_handles = json.loads(bot_profile["social_handles"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Parse structured social_media from UI profile
    social_media = []
    if ui_profile.get("social_media"):
        try:
            social_media = json.loads(ui_profile["social_media"])
        except (json.JSONDecodeError, TypeError):
            pass

    # If no UI social_media yet, seed from legacy bot handles
    if not social_media and legacy_handles:
        social_media = [{"platform": p, "url": h} for p, h in legacy_handles.items()]

    return {
        "matrix_id": matrix_id,
        "display_name": user.get("display_name"),
        "status": user.get("status"),
        "missing_threshold_h": user.get("missing_threshold_h"),
        "last_active_ts": user.get("last_active_ts"),
        "registration_ts": user.get("registration_ts"),
        # Bot profile fields
        "location": bot_profile.get("location", ""),
        # UI-extended fields
        "legal_name": ui_profile.get("legal_name"),
        "date_of_birth": ui_profile.get("date_of_birth"),
        "physical_address": ui_profile.get("physical_address"),
        "emergency_contacts": _parse_json_field(ui_profile.get("emergency_contacts"), []),
        "social_media": social_media,
        "vault_text": ui_profile.get("vault_text"),
        "release_actions": _parse_json_field(ui_profile.get("release_actions"), []),
        # Vault metadata
        "vault_created_ts": vault_meta.get("created_ts"),
        "vault_released_ts": vault_meta.get("released_ts"),
    }


@app.put("/api/profile")
async def update_profile(
    body: ProfileUpdate,
    matrix_id: str = Depends(_get_current_user),
):
    """Update the user's profile. All fields are optional (partial update)."""
    user = await db.get_registered_user(matrix_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found in bot database.")

    # Fetch current state to merge
    ui_profile = await db.get_ui_profile(matrix_id) or {}
    bot_profile = await db.get_bot_profile(matrix_id) or {}

    # Update missing_threshold_h in registered_users if provided
    if body.missing_threshold_h is not None:
        if not (1 <= body.missing_threshold_h <= 720):
            raise HTTPException(status_code=422, detail="missing_threshold_h must be 1–720.")
        await db.update_threshold(matrix_id, body.missing_threshold_h)

    # Update bot's user_profiles (location + social_handles for OSINT compatibility)
    new_location = body.location if body.location is not None else bot_profile.get("location", "")
    # Build legacy social_handles dict from the structured social_media list for OSINT scanner
    if body.social_media is not None:
        legacy = {
            e.platform.lower().split("/")[0].strip(): e.url
            for e in body.social_media
            if e.platform and e.url
        }
        await db.upsert_bot_profile(matrix_id, new_location, json.dumps(legacy))
    elif body.location is not None:
        current_handles = bot_profile.get("social_handles", "{}")
        await db.upsert_bot_profile(matrix_id, new_location, current_handles)

    # Update UI-extended profile
    await db.upsert_ui_profile(
        matrix_id=matrix_id,
        legal_name=body.legal_name if body.legal_name is not None else ui_profile.get("legal_name"),
        date_of_birth=body.date_of_birth if body.date_of_birth is not None else ui_profile.get("date_of_birth"),
        physical_address=body.physical_address if body.physical_address is not None else ui_profile.get("physical_address"),
        emergency_contacts=[c.model_dump() for c in body.emergency_contacts]
            if body.emergency_contacts is not None
            else _parse_json_field(ui_profile.get("emergency_contacts"), []),
        social_media=[s.model_dump() for s in body.social_media]
            if body.social_media is not None
            else _parse_json_field(ui_profile.get("social_media"), []),
        vault_text=body.vault_text if body.vault_text is not None else ui_profile.get("vault_text"),
        release_actions=[r.model_dump() for r in body.release_actions]
            if body.release_actions is not None
            else _parse_json_field(ui_profile.get("release_actions"), []),
    )

    await db.log_event("UI_PROFILE_UPDATED", actor_matrix_id=matrix_id)
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Routes — Check-in
# ---------------------------------------------------------------------------

@app.post("/api/checkin")
async def checkin(matrix_id: str = Depends(_get_current_user)):
    """
    Record a manual check-in. Updates last_active_ts in registered_users
    and resets status to ACTIVE — identical to the bot's !checkin command.
    """
    user = await db.get_registered_user(matrix_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found in bot database.")
    await db.update_last_active(matrix_id)
    await db.log_event("UI_CHECKIN", actor_matrix_id=matrix_id)
    return {"status": "checked_in", "ts": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Routes — Audit Log
# ---------------------------------------------------------------------------

@app.get("/api/audit")
async def get_audit(matrix_id: str = Depends(_get_current_user)):
    """Return the last 50 audit log entries for the authenticated user."""
    entries = await db.get_audit_log(matrix_id)
    return entries


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_field(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8001"))
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
