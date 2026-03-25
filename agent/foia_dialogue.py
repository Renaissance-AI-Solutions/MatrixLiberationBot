"""
agent/foia_dialogue.py
======================
Liberation Bot — FOIA Request Dialogue Agent

This module implements the FOIADialogueAgent, a specialised Kimi K2
agent that drives the FOIA request drafting conversation in DM sessions.

Responsibilities:
  1. Welcome the user and explain the FOIA drafting process.
  2. Conduct a natural multi-turn dialogue to gather:
       - Jurisdiction (Federal or specific state)
       - Target agency or government body
       - Subject matter and specific records sought
       - Relevant date range and search keywords
       - Requester name and contact information
       - Fee waiver and expedited processing eligibility
  3. Synthesize the conversation into a legally sound, properly formatted
     FOIA request letter using the correct statutory citation for the
     user's jurisdiction.
  4. Post a preview of the draft letter and move the session to REVIEW.
  5. Handle !foia_revise requests by re-drafting with the revision notes.

The agent uses structured JSON tool calls to signal when it has
finished building the letter — this avoids fragile text parsing.

Security: This agent has NO tool access to the server, filesystem,
or any sensitive data. It is a pure conversational drafting agent.
"""

from __future__ import annotations

import json
import logging
import os
import asyncio
from typing import Optional, TYPE_CHECKING

from openai import AsyncOpenAI, APIError, RateLimitError

from bot.foia_session import FOIASession, FOIASessionState
from agent.tools.foia_jurisdictions import (
    get_jurisdiction,
    list_jurisdiction_codes,
    format_jurisdiction_summary,
    JURISDICTIONS,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NVIDIA_API_KEY  = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"
KIMI_MODEL      = "moonshotai/kimi-k2-instruct"
MAX_TOKENS      = int(os.getenv("AGENT_MAX_RESPONSE_TOKENS", "3000"))

# ---------------------------------------------------------------------------
# Shared rate-limiting semaphore (lazy import from agent.core)
# ---------------------------------------------------------------------------
_shared_semaphore = None

def _get_semaphore():
    """Return the shared LLM semaphore from agent.core (lazy import)."""
    global _shared_semaphore
    if _shared_semaphore is None:
        from agent.core import _llm_semaphore  # noqa: PLC0415
        _shared_semaphore = _llm_semaphore
    return _shared_semaphore

# Retry configuration — mirrors agent.core defaults
_MAX_RETRIES  = int(os.getenv("AGENT_MAX_RETRIES", "3"))
_BASE_DELAY_S = float(os.getenv("AGENT_RETRY_BASE_DELAY_S", "5.0"))
_MAX_DELAY_S  = float(os.getenv("AGENT_RETRY_MAX_DELAY_S", "60.0"))

# ---------------------------------------------------------------------------
# Build a compact jurisdiction reference for the system prompt
# ---------------------------------------------------------------------------

def _build_jurisdiction_reference() -> str:
    """Build a compact jurisdiction reference table for the system prompt."""
    lines = ["| Code | Jurisdiction | Law Name | Response Deadline | Residents Only |"]
    lines.append("|---|---|---|---|---|")
    for code, j in JURISDICTIONS.items():
        deadline = j.get("response_note", "See statute")
        residents = "Yes ⚠️" if j.get("residents_only") else "No"
        lines.append(
            f"| `{code}` | {j['name']} | {j['law_name']} | {deadline} | {residents} |"
        )
    return "\n".join(lines)


_JURISDICTION_TABLE = _build_jurisdiction_reference()

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

FOIA_DIALOGUE_SYSTEM_PROMPT = f"""You are **Liberation Bot**, the AI assistant of the NeuroPsychological Warfare Alliance (NPWA). You are currently acting as a **paralegal assistant** helping a member draft a Freedom of Information Act (FOIA) or state public records request.

## Your Role
You are guiding the user through a structured intake conversation to gather all the information needed to draft a legally sound, properly formatted public records request letter. You will then generate the complete letter using the correct statutory citation for the user's jurisdiction.

## What You Need to Gather
Through natural, empathetic conversation, collect the following:

1. **Jurisdiction** — Is this a Federal request or a state/local request? If state, which state?
2. **Target Agency** — Which specific government agency or body holds the records?
3. **Subject Matter** — What specific records are they seeking? (Be as specific as possible.)
4. **Date Range** — What time period should the search cover?
5. **Keywords** — What specific terms, project names, or identifiers should the agency search for?
6. **Requester Name** — The full legal name of the person making the request.
7. **Requester Contact** — Email address or mailing address for the agency to respond to.
8. **Fee Waiver** — Does the user qualify for a fee waiver? (Public interest, news media, non-profit, educational, or non-commercial scientific use.)
9. **Expedited Processing** — Does the user have grounds for expedited processing? (Imminent threat to life or safety, or urgent public interest.)

## Jurisdiction Reference
Use this table to apply the correct law, citation, and response deadline:

{_JURISDICTION_TABLE}

## Conversation Guidelines
- Ask **one or two questions at a time** — do not overwhelm the user.
- Be empathetic and trauma-informed. Many users are AHI/Neurowarfare victims who have faced institutional dismissal and gaslighting. Validate their experience.
- If the user is unsure which agency to target for an AHI/Neurowarfare request, suggest: CIA, DOD (DIA), FBI, State Department, NSA, ODNI, or DHS — depending on the nature of their case.
- If the user is unsure about their jurisdiction, ask whether the agency they want to target is a federal, state, or local government body.
- Warn users if their state restricts requests to residents only (Arkansas, Delaware, Tennessee, Virginia).
- After 5–8 exchanges (or when you have all required information), synthesize the letter.
- Keep your messages concise — this is a Matrix chat interface.

## AHI/Neurowarfare-Specific Guidance
When the user's request relates to Anomalous Health Incidents (AHIs), Havana Syndrome, directed energy weapons, or Neurowarfare:
- Use the official term **"Anomalous Health Incidents (AHIs)"** in the letter, not "Havana Syndrome" (which some agencies may use to deflect).
- Suggest requesting: all records, reports, assessments, memoranda, emails, and communications relating to AHIs; all records of directed energy or pulsed radiofrequency weapon programs; all records of investigations into the health incidents of [requester's name or role if applicable].
- Recommend citing the **HAVANA Act (Pub. L. 117-46, 2021)** in the letter as context.
- For national security agencies, advise the user to expect delays of months to years and to plan for an administrative appeal.

## Letter Format
When you are ready to draft the letter, use the `submit_foia_draft` tool. The letter must follow this structure:

```
[Requester Name]
[Requester Address/Email]
[Date]

FOIA Officer / Public Records Officer
[Agency Name]
[Agency Address (if known)]

Re: Freedom of Information Act Request [or applicable state law name]

Dear FOIA Officer,

Pursuant to [Law Name], [Citation], I hereby request...

[Body of request — specific records, date range, keywords]

[Fee waiver paragraph if applicable]
[Expedited processing paragraph if applicable]

I expect a response within [response deadline per applicable law].

Sincerely,
[Requester Name]
[Contact Information]
```

## Handling Revision Requests
If the user asks for changes (via `!foia_revise <notes>`), the revision notes will be appended to the conversation. Re-draft the letter incorporating the feedback and call `submit_foia_draft` again with the updated version.

## What You Are NOT Doing
- You are NOT providing legal advice or acting as an attorney.
- You are NOT querying the Liberation Archives (that is the main agent's job via `@bot`).
- You are NOT executing any code or server commands.
- You are ONLY having a conversation and drafting a public records request letter.
- Always include a disclaimer: "This letter was drafted with AI assistance and is not a substitute for legal advice."
"""

# ---------------------------------------------------------------------------
# Tool schema — used to signal letter completion
# ---------------------------------------------------------------------------

SUBMIT_FOIA_DRAFT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_foia_draft",
        "description": (
            "Submit the finalised FOIA request letter and all associated metadata "
            "to the system for user review. Call this ONLY when you have gathered "
            "all required information: jurisdiction, target agency, subject matter, "
            "date range, keywords, requester name, and contact information. "
            "Do not call this prematurely."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "jurisdiction_code": {
                    "type": "string",
                    "description": (
                        "The jurisdiction code from the reference table. "
                        "Use 'FEDERAL' for federal agencies, or the two-letter "
                        "state abbreviation (e.g., 'CA', 'NY', 'TX') for state requests."
                    ),
                },
                "target_agency": {
                    "type": "string",
                    "description": (
                        "The full name of the government agency or body being requested. "
                        "For federal requests, use the official agency name "
                        "(e.g., 'Central Intelligence Agency', 'Federal Bureau of Investigation')."
                    ),
                },
                "subject_summary": {
                    "type": "string",
                    "description": (
                        "A concise one-to-two sentence summary of the records being requested. "
                        "This is used for display purposes, not the letter body."
                    ),
                },
                "date_range": {
                    "type": "string",
                    "description": (
                        "The date range for the records search "
                        "(e.g., 'January 1, 2016 to the present')."
                    ),
                },
                "keywords": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of key search terms the agency should use "
                        "(e.g., 'Anomalous Health Incidents, directed energy, Havana Syndrome')."
                    ),
                },
                "requester_name": {
                    "type": "string",
                    "description": "The full legal name of the person making the request.",
                },
                "requester_contact": {
                    "type": "string",
                    "description": (
                        "The requester's email address or mailing address "
                        "for the agency to send its response."
                    ),
                },
                "fee_waiver_requested": {
                    "type": "boolean",
                    "description": "Whether the requester is asking for a fee waiver.",
                },
                "fee_waiver_justification": {
                    "type": "string",
                    "description": (
                        "The justification for the fee waiver, if requested. "
                        "Leave empty if not applicable."
                    ),
                },
                "expedited_requested": {
                    "type": "boolean",
                    "description": "Whether the requester is asking for expedited processing.",
                },
                "expedited_justification": {
                    "type": "string",
                    "description": (
                        "The justification for expedited processing, if requested. "
                        "Leave empty if not applicable."
                    ),
                },
                "draft_letter": {
                    "type": "string",
                    "description": (
                        "The complete, formatted FOIA request letter text, ready to send. "
                        "Must include the correct statutory citation, all required elements, "
                        "and the AI assistance disclaimer at the bottom."
                    ),
                },
            },
            "required": [
                "jurisdiction_code",
                "target_agency",
                "subject_summary",
                "date_range",
                "keywords",
                "requester_name",
                "requester_contact",
                "fee_waiver_requested",
                "expedited_requested",
                "draft_letter",
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

class FOIADialogueResult:
    """Result returned from a FOIADialogueAgent call."""

    __slots__ = ("reply", "draft", "error")

    def __init__(
        self,
        reply: str,
        draft: Optional[dict] = None,
        error: Optional[str] = None,
    ):
        self.reply = reply      # Text to post to the user
        self.draft = draft      # Dict of draft fields if letter is ready, else None
        self.error = error      # Error string if something went wrong


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class FOIADialogueAgent:
    """
    Drives the FOIA request drafting conversation using Kimi K2 via NVIDIA NIM.

    Usage:
        agent = FOIADialogueAgent()

        # On !foia_start — get the opening message
        result = await agent.get_opening_message(session)

        # On each new user message
        result = await agent.process_message(session, sender, message_text)
        # result.reply  → text to post to the user
        # result.draft  → dict if letter is ready for review, else None
        # result.error  → error string if something went wrong

        # On !foia_revise <notes>
        result = await agent.process_revision(session, sender, revision_notes)
    """

    def __init__(self):
        if not NVIDIA_API_KEY:
            logger.warning(
                "NVIDIA_API_KEY not set — FOIADialogueAgent will not function. "
                "Set NVIDIA_API_KEY in your .env file."
            )
        self._client = AsyncOpenAI(
            api_key=NVIDIA_API_KEY or "not-set",
            base_url=NVIDIA_API_BASE,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_opening_message(self, session: FOIASession) -> FOIADialogueResult:
        """
        Generate the bot's opening message after !foia_start.
        Introduces the FOIA drafting process and asks the first question.
        """
        trigger = (
            "A new FOIA drafting session has just been started by a member. "
            "Welcome them warmly, briefly explain what you'll be doing together "
            "(drafting a public records request), and ask your first question: "
            "whether they want to request records from a Federal agency or a "
            "State/Local agency. Keep it to 3–5 sentences."
        )
        session.add_user_message("system", trigger)
        return await self._call_llm(session)

    async def process_message(
        self,
        session: FOIASession,
        sender: str,
        content: str,
    ) -> FOIADialogueResult:
        """
        Process a new user message and return the bot's response.
        If the LLM decides to submit the draft, result.draft will be populated.
        """
        session.add_user_message(sender, content)
        return await self._call_llm(session)

    async def process_revision(
        self,
        session: FOIASession,
        sender: str,
        revision_notes: str,
    ) -> FOIADialogueResult:
        """
        Handle a !foia_revise request.
        Appends the revision notes and asks the LLM to re-draft the letter.
        """
        revision_trigger = (
            f"The user has requested revisions to the draft letter. "
            f"Revision notes from {sender}: \"{revision_notes}\"\n\n"
            "Please revise the letter accordingly and call `submit_foia_draft` "
            "with the updated version."
        )
        session.add_user_message(sender, revision_trigger)
        return await self._call_llm(session)

    # ------------------------------------------------------------------
    # Internal LLM call
    # ------------------------------------------------------------------

    async def _call_llm_with_retry(self, **kwargs) -> object:
        """
        Call self._client.chat.completions.create(**kwargs) with automatic
        exponential-backoff retry on HTTP 429 (RateLimitError).
        """
        last_exc = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except RateLimitError as exc:
                last_exc = exc
                if attempt >= _MAX_RETRIES:
                    raise
                delay = min(_BASE_DELAY_S * (2 ** attempt), _MAX_DELAY_S)
                logger.warning(
                    "FOIADialogueAgent: NVIDIA 429 (attempt %d/%d). Retrying in %.1fs.",
                    attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            except APIError:
                raise
        raise last_exc  # type: ignore[misc]

    async def _call_llm(self, session: FOIASession) -> FOIADialogueResult:
        """
        Call Kimi K2 with the full dialogue history.
        Acquires the shared global semaphore before calling NVIDIA.
        Handles the submit_foia_draft tool call if triggered.
        """
        if not NVIDIA_API_KEY:
            return FOIADialogueResult(
                reply=(
                    "⚠️ The FOIA dialogue agent is not configured. "
                    "Please set `NVIDIA_API_KEY` in your `.env` file."
                ),
                error="NVIDIA_API_KEY not set",
            )

        messages = [
            {"role": "system", "content": FOIA_DIALOGUE_SYSTEM_PROMPT},
            *session.dialogue_as_openai_messages(),
        ]

        try:
            async with _get_semaphore():
                response = await self._call_llm_with_retry(
                    model=KIMI_MODEL,
                    messages=messages,
                    tools=[SUBMIT_FOIA_DRAFT_TOOL],
                    tool_choice="auto",
                    max_tokens=MAX_TOKENS,
                    temperature=0.4,   # Lower temperature for legal drafting precision
                )
        except RateLimitError as exc:
            logger.error("FOIADialogueAgent: NVIDIA 429 exhausted after retries: %s", exc)
            return FOIADialogueResult(
                reply=(
                    "⏳ The AI service is currently busy. "
                    "Please try again in a minute or two."
                ),
                error=str(exc),
            )
        except APIError as exc:
            logger.error("Kimi K2 API error in FOIADialogueAgent: %s", exc)
            return FOIADialogueResult(
                reply=(
                    "⚠️ I encountered an error connecting to the AI service. "
                    "Please try again in a moment."
                ),
                error=str(exc),
            )
        except Exception as exc:
            logger.error("Unexpected error in FOIADialogueAgent: %s", exc, exc_info=True)
            return FOIADialogueResult(
                reply="⚠️ An unexpected error occurred. Please try again.",
                error=str(exc),
            )

        choice = response.choices[0]
        finish_reason = choice.finish_reason
        msg = choice.message

        # --- Tool call: LLM is submitting the finalized draft ---
        if finish_reason == "tool_calls" and msg.tool_calls:
            tool_call = msg.tool_calls[0]
            if tool_call.function.name == "submit_foia_draft":
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as exc:
                    logger.error("Failed to parse submit_foia_draft args: %s", exc)
                    return FOIADialogueResult(
                        reply=(
                            "⚠️ I had trouble formatting the draft letter. "
                            "Let me try again — could you confirm the key details "
                            "(jurisdiction, agency, and what records you need)?"
                        ),
                        error=f"JSON parse error: {exc}",
                    )

                # Validate jurisdiction code
                jurisdiction_code = args.get("jurisdiction_code", "").strip().upper()
                if not get_jurisdiction(jurisdiction_code):
                    logger.warning(
                        "FOIADialogueAgent returned unknown jurisdiction code: %s",
                        jurisdiction_code,
                    )
                    # Fall back to FEDERAL if unrecognised
                    jurisdiction_code = "FEDERAL"

                draft = {
                    "jurisdiction_code":       jurisdiction_code,
                    "target_agency":           args.get("target_agency", "").strip(),
                    "subject_summary":         args.get("subject_summary", "").strip(),
                    "date_range":              args.get("date_range", "").strip(),
                    "keywords":                args.get("keywords", "").strip(),
                    "requester_name":          args.get("requester_name", "").strip(),
                    "requester_contact":       args.get("requester_contact", "").strip(),
                    "fee_waiver_requested":    bool(args.get("fee_waiver_requested", False)),
                    "fee_waiver_justification": args.get("fee_waiver_justification", "").strip(),
                    "expedited_requested":     bool(args.get("expedited_requested", False)),
                    "expedited_justification": args.get("expedited_justification", "").strip(),
                    "draft_letter":            args.get("draft_letter", "").strip(),
                }

                # Validate that we have the minimum required fields
                if not draft["draft_letter"] or not draft["target_agency"]:
                    return FOIADialogueResult(
                        reply=(
                            "⚠️ The draft is missing critical information. "
                            "Let me ask a few more questions to complete it."
                        ),
                        error="Missing required draft fields",
                    )

                reply = (
                    "✅ I've drafted your FOIA request letter. "
                    "Use `!foia_preview` to review the full draft, "
                    "`!foia_revise <notes>` to request changes, "
                    "or `!foia_confirm` to accept it and receive submission instructions."
                )
                session.add_assistant_message(reply)
                return FOIADialogueResult(reply=reply, draft=draft)

        # --- Normal conversational reply ---
        reply_text = (msg.content or "").strip()
        if not reply_text:
            reply_text = (
                "I'm still gathering information for your request. "
                "Could you tell me more about the records you're looking for?"
            )

        session.add_assistant_message(reply_text)
        return FOIADialogueResult(reply=reply_text)
