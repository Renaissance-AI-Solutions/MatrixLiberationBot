"""
agent/tools/video_generator.py
================================
NotebookLM Video Generation Tool — Liberation Bot Phase I

Wraps the notebooklm-py `generate_video()` / `generate_cinematic_video()` API
for use by the video planning workflow in bot/video_room.py.

This module is NOT exposed to the Kimi K2 agent as a callable tool — it is
called directly by the video room handler after group confirmation.  This
keeps the video generation pipeline outside the LLM tool-calling sandbox.

Environment variables consumed:
  LIBERATION_ARCHIVES_NOTEBOOK_ID  — The NotebookLM notebook ID
  NOTEBOOKLM_AUTH_JSON             — Inline JSON from storage_state.json
  NOTEBOOKLM_HOME                  — Alt: path to ~/.notebooklm directory
  VIDEO_OUTPUT_DIR                 — Where to save downloaded videos (default: ./data/videos)
  NOTEBOOKLM_TIMEOUT_SECS          — Generation timeout in seconds (default: 1800)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NOTEBOOK_ID = os.getenv("LIBERATION_ARCHIVES_NOTEBOOK_ID", "")
NOTEBOOKLM_AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON", "")
NOTEBOOKLM_HOME = os.getenv("NOTEBOOKLM_HOME", "")
VIDEO_OUTPUT_DIR = os.getenv("VIDEO_OUTPUT_DIR", "./data/videos")
# Standard videos take ~5–10 min; cinematic (Veo 3) takes ~30–40 min
GENERATION_TIMEOUT = int(os.getenv("NOTEBOOKLM_TIMEOUT_SECS", "1800"))

# Map from our style key → notebooklm VideoStyle enum name
_STYLE_KEY_TO_ENUM: dict[str, str] = {
    "auto":       "AUTO_SELECT",
    "classic":    "CLASSIC",
    "whiteboard": "WHITEBOARD",
    "kawaii":     "KAWAII",
    "anime":      "ANIME",
    "watercolor": "WATERCOLOR",
    "retro":      "RETRO_PRINT",
    "heritage":   "HERITAGE",
    "papercraft": "PAPER_CRAFT",
    "cinematic":  "CINEMATIC",   # Special: uses generate_cinematic_video()
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class VideoGenerationResult:
    success: bool
    video_path: Optional[str] = None
    task_id: Optional[str] = None
    error: Optional[str] = None
    duration_secs: float = 0.0


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

async def _get_client():
    """
    Create and return an authenticated NotebookLMClient.
    Tries NOTEBOOKLM_AUTH_JSON first, then NOTEBOOKLM_HOME.
    Raises RuntimeError if neither is configured.
    """
    try:
        from notebooklm import NotebookLMClient
    except ImportError as e:
        raise RuntimeError(
            "notebooklm-py is not installed. Run: pip install notebooklm-py"
        ) from e

    if NOTEBOOKLM_AUTH_JSON:
        # Write the inline JSON to a temp file and use from_storage()
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix="notebooklm_")
        state_path = Path(tmp_dir) / "storage_state.json"
        state_path.write_text(NOTEBOOKLM_AUTH_JSON)
        logger.debug("Using inline NOTEBOOKLM_AUTH_JSON for authentication.")
        return NotebookLMClient.from_storage(storage_dir=tmp_dir)

    if NOTEBOOKLM_HOME:
        logger.debug("Using NOTEBOOKLM_HOME=%s for authentication.", NOTEBOOKLM_HOME)
        return NotebookLMClient.from_storage(storage_dir=NOTEBOOKLM_HOME)

    # Try the default ~/.notebooklm location
    default_home = Path.home() / ".notebooklm"
    if (default_home / "storage_state.json").exists():
        logger.debug("Using default ~/.notebooklm for authentication.")
        return NotebookLMClient.from_storage(storage_dir=str(default_home))

    raise RuntimeError(
        "NotebookLM authentication not configured. Set NOTEBOOKLM_AUTH_JSON "
        "or NOTEBOOKLM_HOME in your .env file."
    )


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

async def generate_video(
    custom_prompt: str,
    style_key: str,
    title: str,
    notebook_id: str = None,
) -> VideoGenerationResult:
    """
    Generate a NotebookLM video using the Liberation Archives notebook.

    Args:
        custom_prompt:  The full content prompt (including the NPWA CTA suffix).
        style_key:      One of the keys from VIDEO_STYLE_NAMES (e.g. "classic").
        title:          Human-readable title used for the output filename.
        notebook_id:    Override the notebook ID (defaults to LIBERATION_ARCHIVES_NOTEBOOK_ID).

    Returns:
        VideoGenerationResult with success flag and local video path or error.
    """
    nb_id = notebook_id or NOTEBOOK_ID
    if not nb_id:
        return VideoGenerationResult(
            success=False,
            error=(
                "LIBERATION_ARCHIVES_NOTEBOOK_ID is not set. "
                "Add it to your .env file."
            ),
        )

    # Resolve the VideoStyle enum value
    enum_name = _STYLE_KEY_TO_ENUM.get(style_key, "CLASSIC")
    is_cinematic = (style_key == "cinematic")

    # Prepare output directory and filename
    output_dir = Path(VIDEO_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)[:60]
    timestamp = int(time.time())
    output_path = str(output_dir / f"{safe_title}_{timestamp}.mp4")

    start_ts = time.time()
    task_id: Optional[str] = None

    try:
        client_ctx = await _get_client()
        async with client_ctx as client:
            logger.info(
                "Starting video generation | style=%s | cinematic=%s | notebook=%s",
                style_key, is_cinematic, nb_id,
            )

            if is_cinematic:
                # Cinematic uses Veo 3 — no VideoStyle parameter
                status = await client.artifacts.generate_cinematic_video(
                    notebook_id=nb_id,
                    instructions=custom_prompt,
                )
            else:
                from notebooklm.rpc import VideoStyle, VideoFormat
                style_enum = VideoStyle[enum_name]
                status = await client.artifacts.generate_video(
                    notebook_id=nb_id,
                    instructions=custom_prompt,
                    video_style=style_enum,
                    video_format=VideoFormat.EXPLAINER,
                )

            task_id = status.task_id
            logger.info("Video generation task started: task_id=%s", task_id)

            # Poll until complete
            final_status = await client.artifacts.wait_for_completion(
                notebook_id=nb_id,
                task_id=task_id,
                initial_interval=10.0,
                max_interval=30.0,
                timeout=float(GENERATION_TIMEOUT),
            )

            if not final_status.is_complete:
                return VideoGenerationResult(
                    success=False,
                    task_id=task_id,
                    error=f"Generation failed or timed out. Status: {final_status}",
                    duration_secs=time.time() - start_ts,
                )

            # Download the video
            logger.info("Downloading video to %s", output_path)
            await client.artifacts.download_video(
                notebook_id=nb_id,
                output_path=output_path,
                artifact_id=task_id,
            )

            duration = time.time() - start_ts
            logger.info(
                "Video generation complete in %.1fs: %s", duration, output_path
            )
            return VideoGenerationResult(
                success=True,
                video_path=output_path,
                task_id=task_id,
                duration_secs=duration,
            )

    except TimeoutError:
        return VideoGenerationResult(
            success=False,
            task_id=task_id,
            error=(
                f"Video generation timed out after {GENERATION_TIMEOUT}s. "
                "The video may still be generating in NotebookLM — check your notebook."
            ),
            duration_secs=time.time() - start_ts,
        )
    except Exception as exc:
        logger.error("Video generation error: %s", exc, exc_info=True)
        return VideoGenerationResult(
            success=False,
            task_id=task_id,
            error=str(exc),
            duration_secs=time.time() - start_ts,
        )


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def video_generation_available() -> tuple[bool, str]:
    """
    Check whether video generation is configured.
    Returns (available: bool, reason: str).
    """
    if not NOTEBOOK_ID:
        return False, "LIBERATION_ARCHIVES_NOTEBOOK_ID not set"
    if not NOTEBOOKLM_AUTH_JSON and not NOTEBOOKLM_HOME:
        default_home = Path.home() / ".notebooklm" / "storage_state.json"
        if not default_home.exists():
            return False, "NotebookLM authentication not configured"
    try:
        from notebooklm import NotebookLMClient  # noqa: F401
    except ImportError:
        return False, "notebooklm-py not installed"
    return True, "ok"
