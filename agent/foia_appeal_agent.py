"""
agent/foia_appeal_agent.py
==========================
Liberation Bot — FOIA Appeal Drafting Agent

Generates a complete, jurisdiction-appropriate FOIA appeal letter when a
request has been denied, partially fulfilled, or has gone unanswered past
the statutory deadline.

The agent uses Kimi K2 (via NVIDIA NIM) with a specialized system prompt
that understands:
  - The legal basis for administrative appeals under 5 U.S.C. § 552(a)(6)
    (federal) and equivalent state statutes.
  - Common denial grounds and how to rebut each one:
      * Exemption 1 (classified)
      * Exemption 3 (other statutes)
      * Exemption 5 (deliberative process / attorney-client)
      * Exemption 6 (personal privacy)
      * Exemption 7 (law enforcement)
      * Constructive denial (no response within statutory deadline)
  - AHI/Neurowarfare-specific arguments (public interest, health and safety,
    declassification trends, prior disclosures).
  - State-specific appeal procedures and deadlines.

The appeal letter is returned as a complete, ready-to-send document.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI, APIError, RateLimitError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"
KIMI_MODEL = "moonshotai/kimi-k2-instruct"

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5.0
RETRY_MAX_DELAY = 60.0

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
APPEAL_SYSTEM_PROMPT = """You are an expert FOIA appeal attorney specializing in government transparency litigation, with deep knowledge of:

1. **Federal FOIA Appeals** (5 U.S.C. § 552(a)(6)(A)(i)):
   - The 20-business-day deadline for initial responses and the 20-business-day appeal window.
   - All nine FOIA exemptions (1-9) and how to argue against improper application.
   - The "foreseeable harm" standard added by the FOIA Improvement Act of 2016.
   - Constructive denial doctrine when agencies fail to respond within the statutory deadline.
   - Segregability requirements — agencies must release non-exempt portions.

2. **State-Specific Appeal Procedures**:
   - Each state has its own public records law with different appeal mechanisms.
   - Some states appeal to an ombudsman or AG; others require direct court action.
   - You know the specific appeal procedures for all 50 states.

3. **AHI/Neurowarfare-Specific Arguments**:
   - The public interest in disclosing information about directed energy weapons and AHIs.
   - Prior disclosures (e.g., CIA AHI Task Force reports, State Department cables) that
     establish a precedent for releasing similar records.
   - Health and safety arguments that override privacy exemptions.
   - The declassification trend following the HAVANA Act (2021) and subsequent investigations.

## Your Task
Generate a complete, professional FOIA appeal letter based on the original request details and
the denial or non-response circumstances provided. The letter must:

1. Be addressed to the correct appeal authority (FOIA Appeals Officer or equivalent).
2. Reference the original request by date, tracking number (if known), and subject.
3. Clearly state the legal basis for the appeal.
4. Rebut each exemption cited in the denial (if applicable).
5. Argue for segregability of non-exempt portions.
6. Invoke the public interest standard where applicable.
7. Request a response within the statutory appeal deadline.
8. Include a professional closing with the requester's contact information.

## Output Format
Return a JSON object with exactly these fields:
{
  "appeal_letter": "<complete formatted appeal letter as a string>",
  "appeal_authority": "<name/title of the appeal authority>",
  "legal_basis": "<primary legal basis for the appeal, e.g., '5 U.S.C. § 552(a)(6)(A)(ii)'>",
  "key_arguments": ["<argument 1>", "<argument 2>", ...],
  "estimated_appeal_deadline_days": <integer number of business days for appeal response>
}

Do NOT include any text outside the JSON object.
"""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class AppealResult:
    """Result from the appeal drafting agent."""
    appeal_letter: str
    appeal_authority: str
    legal_basis: str
    key_arguments: list
    estimated_appeal_deadline_days: int
    error: Optional[str] = None
    latency_ms: int = 0

    @property
    def success(self) -> bool:
        return bool(self.appeal_letter) and not self.error


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class FOIAAppealAgent:
    """
    Kimi K2-powered FOIA appeal drafting agent.

    Generates a complete appeal letter for a denied or unanswered FOIA request.
    """

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=NVIDIA_API_KEY or "not-set",
            base_url=NVIDIA_API_BASE,
        )

    async def _call_llm(self, messages: list) -> str:
        """Call Kimi K2 with retry-backoff on 429."""
        last_exc = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self.client.chat.completions.create(
                    model=KIMI_MODEL,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=2048,
                )
                return resp.choices[0].message.content or ""
            except RateLimitError as exc:
                last_exc = exc
                if attempt >= MAX_RETRIES:
                    raise
                delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                logger.warning(
                    "FOIAAppealAgent: 429 RateLimitError (attempt %d/%d). Retrying in %.1fs.",
                    attempt + 1, MAX_RETRIES, delay,
                )
                import asyncio
                await asyncio.sleep(delay)
            except APIError:
                raise
        raise last_exc  # type: ignore

    async def draft_appeal(
        self,
        original_request: dict,
        denial_reason: Optional[str] = None,
        denial_date: Optional[str] = None,
        tracking_number: Optional[str] = None,
        additional_context: Optional[str] = None,
    ) -> AppealResult:
        """
        Draft a FOIA appeal letter for a denied or unanswered request.

        Parameters
        ----------
        original_request : dict
            The foia_requests DB row for the original request. Must contain:
            jurisdiction_code, target_agency, subject_summary, requester_name,
            requester_contact, draft_letter, created_ts, submitted_ts.
        denial_reason : str, optional
            The reason given by the agency for the denial (e.g., "Exemption 5").
            If None, the appeal is based on constructive denial (no response).
        denial_date : str, optional
            The date the denial was received (YYYY-MM-DD format).
        tracking_number : str, optional
            The agency-assigned tracking number for the original request.
        additional_context : str, optional
            Any additional context the user wants to include in the appeal.

        Returns
        -------
        AppealResult
            Contains the complete appeal letter and metadata.
        """
        start_ts = time.time()

        # Build the context for the LLM
        submitted_str = "unknown"
        if original_request.get("submitted_ts"):
            submitted_str = datetime.fromtimestamp(
                original_request["submitted_ts"], tz=timezone.utc
            ).strftime("%B %d, %Y")

        created_str = datetime.fromtimestamp(
            original_request.get("created_ts", time.time()), tz=timezone.utc
        ).strftime("%B %d, %Y")

        today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

        # Determine appeal type
        if denial_reason:
            appeal_type = f"DENIAL APPEAL — Agency denied the request citing: {denial_reason}"
            if denial_date:
                appeal_type += f" (denial dated {denial_date})"
        else:
            appeal_type = (
                "CONSTRUCTIVE DENIAL APPEAL — Agency failed to respond within the "
                "statutory deadline. The request was submitted on "
                f"{submitted_str} and no response has been received."
            )

        user_message = f"""Please draft a FOIA appeal letter for the following request:

## Original Request Details
- **Jurisdiction**: {original_request.get('jurisdiction_code', 'FEDERAL')}
- **Agency**: {original_request.get('target_agency', 'Unknown Agency')}
- **Subject**: {original_request.get('subject_summary', '')}
- **Date of Original Request**: {created_str}
- **Date Submitted to Agency**: {submitted_str}
- **Tracking Number**: {tracking_number or 'Not provided'}
- **Requester Name**: {original_request.get('requester_name', '')}
- **Requester Contact**: {original_request.get('requester_contact', '')}
- **Today's Date**: {today_str}

## Appeal Circumstances
{appeal_type}

## Additional Context
{additional_context or 'None provided.'}

## Original Request Letter (for reference)
{original_request.get('draft_letter', '')[:2000]}

Please generate the complete appeal letter and return it as a JSON object per the format specified in your instructions."""

        messages = [
            {"role": "system", "content": APPEAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        try:
            raw = await self._call_llm(messages)
        except Exception as exc:
            logger.error("FOIAAppealAgent._call_llm failed: %s", exc)
            latency_ms = int((time.time() - start_ts) * 1000)
            return AppealResult(
                appeal_letter="",
                appeal_authority="",
                legal_basis="",
                key_arguments=[],
                estimated_appeal_deadline_days=20,
                error=f"LLM error: {exc}",
                latency_ms=latency_ms,
            )

        # Parse the JSON response
        latency_ms = int((time.time() - start_ts) * 1000)
        try:
            # Strip markdown code fences if present
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("```", 2)[-1].strip()
                if clean.startswith("json"):
                    clean = clean[4:].strip()
                if clean.endswith("```"):
                    clean = clean[:-3].strip()
            data = json.loads(clean)
            return AppealResult(
                appeal_letter=data.get("appeal_letter", ""),
                appeal_authority=data.get("appeal_authority", "FOIA Appeals Officer"),
                legal_basis=data.get("legal_basis", ""),
                key_arguments=data.get("key_arguments", []),
                estimated_appeal_deadline_days=int(
                    data.get("estimated_appeal_deadline_days", 20)
                ),
                latency_ms=latency_ms,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("FOIAAppealAgent: failed to parse LLM response: %s\nRaw: %s", exc, raw[:500])
            # Fallback: return raw text as the letter if JSON parsing fails
            if raw.strip():
                return AppealResult(
                    appeal_letter=raw.strip(),
                    appeal_authority="FOIA Appeals Officer",
                    legal_basis="See letter",
                    key_arguments=[],
                    estimated_appeal_deadline_days=20,
                    latency_ms=latency_ms,
                )
            return AppealResult(
                appeal_letter="",
                appeal_authority="",
                legal_basis="",
                key_arguments=[],
                estimated_appeal_deadline_days=20,
                error=f"Failed to parse appeal letter: {exc}",
                latency_ms=latency_ms,
            )
