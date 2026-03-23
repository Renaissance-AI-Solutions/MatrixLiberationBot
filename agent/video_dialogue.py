"""
agent/video_dialogue.py
=======================
Liberation Bot — Video Dialogue Agent

This module implements the VideoDialogueAgent, a specialised Kimi K2
agent that drives the video brainstorming conversation in the
"Video Planning and Generation" Matrix room.

Responsibilities:
  1. Welcome the group and ask an opening question after !video_start.
  2. Conduct a natural multi-turn dialogue to gather:
       - The video topic and key messages
       - Target audience and tone
       - Visual style preference
       - Any specific scenes, facts, or talking points to include
  3. Synthesize the conversation into optimised prompts using best
     practices in NotebookLM video prompt engineering.
  4. Post a preview of the generated prompts and move the session to
     CONFIRMING state.
  5. Handle !video_revise requests by re-running the synthesis with
     the revision notes appended.

The agent uses structured JSON tool calls to signal when it has
finished building the prompts — this avoids fragile text parsing.

Security: This agent has NO tool access to the server, filesystem,
or any sensitive data.  It is a pure conversational agent.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, TYPE_CHECKING

from openai import AsyncOpenAI, APIError

from bot.video_session import (
    VideoSession,
    SessionState,
    VIDEO_STYLE_NAMES,
    VIDEO_STYLE_DESCRIPTIONS,
    NPWA_CTA_SUFFIX,
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
MAX_TOKENS      = int(os.getenv("AGENT_MAX_RESPONSE_TOKENS", "2048"))

# ---------------------------------------------------------------------------
# System prompt for the video dialogue specialist
# ---------------------------------------------------------------------------
_STYLE_LIST = "\n".join(
    f"  - {key}: {desc}" for key, desc in VIDEO_STYLE_DESCRIPTIONS.items()
)

VIDEO_DIALOGUE_SYSTEM_PROMPT = f"""You are **Liberation Bot**, the AI assistant of the NeuroPsychological Warfare Alliance (NPWA). You are currently acting as a **video production specialist** helping a group of advocates brainstorm and plan an advocacy video.

## Your Role in This Conversation
You are facilitating a video planning session. Your job is to:
1. Ask thoughtful, focused questions to understand what the group wants to create.
2. Listen carefully to all participants — multiple people may be contributing ideas.
3. Once you have enough information, synthesize everything into two optimised prompts for NotebookLM video generation.
4. Present the prompts for group review.

## What You Need to Gather
Through natural conversation, collect:
- **Topic & Core Message**: What is this video about? What is the single most important thing viewers should take away?
- **Target Audience**: Who is this for? (general public, policymakers, victims, journalists, etc.)
- **Tone**: Serious/documentary, urgent/activist, educational, emotional/personal, or a mix?
- **Key Content**: Specific facts, events, names, documents, or stories to include.
- **Visual Style**: Preferred aesthetic (you will recommend one based on the content if they don't have a preference).
- **Length/Scope**: Short awareness clip, medium explainer, or longer documentary-style?

## Conversation Guidelines
- Ask **one or two questions at a time** — do not overwhelm the group with a long list.
- Be conversational and warm. These are advocates and victims dealing with serious trauma.
- If someone gives a vague answer, gently probe for specifics.
- If the group seems to have given you enough information, say so and offer to draft the prompts.
- Keep your messages concise — this is a chat interface, not an email.
- After 4–6 exchanges (or when you have enough information), synthesise the prompts.

## Available Visual Styles
{_STYLE_LIST}

Recommend a style based on the content. For serious documentary/advocacy content, `heritage` or `classic` work well. For educational explainers, `whiteboard` or `classic`. For emotional/personal stories, `watercolor` or `heritage`. For cutting-edge tech topics, `cinematic` (if the group has NotebookLM Ultra).

## Prompt Engineering Best Practices for NotebookLM Video
When building the **Custom Prompt** (content instructions):
- Be specific about the narrative arc: opening hook → key evidence → emotional impact → call to action.
- Name specific documents, events, or people if the group mentioned them.
- Specify the tone explicitly (e.g., "urgent and factual", "compassionate and personal").
- Include the target audience so the language level is appropriate.
- Mention any specific visual metaphors or scenes if discussed.
- Keep the prompt between 150–400 words for best results.
- The NPWA closing slide is automatically appended — do NOT include it in your prompt draft.

When building the **Style Prompt** (visual instructions):
- The style prompt is actually the style KEY (e.g., "classic", "heritage") — not a free-text description.
- Choose the single best style from the available list.
- If the group has a saved favourite style they want to reuse, use that key.

## When You Are Ready to Present the Prompts
Use the `submit_video_prompts` tool to submit the final prompts. This signals to the system that you are done building and ready for group confirmation. Do NOT just write the prompts in chat — always use the tool so the system can record them properly.

## Handling Revision Requests
If the group asks for changes (via `!video_revise <notes>`), the revision notes will be appended to the conversation. Re-synthesize the prompts incorporating the feedback and call `submit_video_prompts` again with the updated versions.

## What You Are NOT Doing
- You are NOT generating the video yourself.
- You are NOT querying the Liberation Archives (that is the main agent's job).
- You are NOT executing any code or server commands.
- You are ONLY having a conversation and building prompts.
"""

# ---------------------------------------------------------------------------
# Tool schema — used to signal prompt completion
# ---------------------------------------------------------------------------
SUBMIT_PROMPTS_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_video_prompts",
        "description": (
            "Submit the finalised video title, visual style key, and content prompt "
            "to the system for group confirmation. Call this ONLY when you have gathered "
            "enough information and are ready to present the prompts for review. "
            "Do not call this prematurely — make sure you have covered topic, audience, "
            "tone, key content, and style."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "A compelling, concise video title (5–12 words). "
                        "Should clearly convey the topic and be suitable for YouTube/social media."
                    ),
                },
                "style_key": {
                    "type": "string",
                    "enum": list(VIDEO_STYLE_NAMES.keys()),
                    "description": "The visual style key from the available styles list.",
                },
                "custom_prompt": {
                    "type": "string",
                    "description": (
                        "The full content prompt for NotebookLM video generation. "
                        "150–400 words. Covers narrative arc, tone, audience, key facts/events, "
                        "and any specific visual elements. Do NOT include the NPWA closing slide — "
                        "it is appended automatically."
                    ),
                },
                "style_rationale": {
                    "type": "string",
                    "description": "A one-sentence explanation of why you chose this style.",
                },
            },
            "required": ["title", "style_key", "custom_prompt", "style_rationale"],
        },
    },
}


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class VideoDialogueAgent:
    """
    Drives the video planning conversation using Kimi K2 via NVIDIA NIM.

    Usage:
        agent = VideoDialogueAgent()

        # On !video_start — get the opening message
        opening = await agent.get_opening_message(session)

        # On each new user message
        result = await agent.process_message(session, sender, message_text)
        # result.reply      → text to post to the room
        # result.prompts    → dict if prompts are ready, else None
        # result.error      → error string if something went wrong
    """

    def __init__(self):
        if not NVIDIA_API_KEY:
            logger.warning(
                "NVIDIA_API_KEY not set — VideoDialogueAgent will not function. "
                "Set NVIDIA_API_KEY in your .env file."
            )
        self._client = AsyncOpenAI(
            api_key=NVIDIA_API_KEY or "not-set",
            base_url=NVIDIA_API_BASE,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_opening_message(self, session: VideoSession) -> "DialogueResult":
        """
        Generate the bot's opening message after !video_start.
        Introduces the session and asks the first question.
        """
        # Seed the dialogue with a system-level trigger
        trigger = (
            "A new video planning session has just been started. "
            "Welcome the group warmly, briefly explain what you'll be doing together, "
            "and ask your first question to get the brainstorming started. "
            "Keep it to 3–5 sentences."
        )
        session.add_user_message("system", trigger)
        return await self._call_llm(session)

    async def process_message(
        self,
        session: VideoSession,
        sender: str,
        content: str,
    ) -> "DialogueResult":
        """
        Process a new user message and return the bot's response.
        If the LLM decides to submit prompts, result.prompts will be populated.
        """
        session.add_user_message(sender, content)
        return await self._call_llm(session)

    async def process_revision(
        self,
        session: VideoSession,
        sender: str,
        revision_notes: str,
    ) -> "DialogueResult":
        """
        Handle a !video_revise request.
        Appends the revision notes and asks the LLM to re-synthesize.
        """
        revision_trigger = (
            f"The group has requested revisions to the prompts. "
            f"Revision notes from {sender}: \"{revision_notes}\"\n\n"
            "Please revise the prompts accordingly and call `submit_video_prompts` "
            "with the updated versions."
        )
        session.add_user_message(sender, revision_trigger)
        return await self._call_llm(session)

    # ------------------------------------------------------------------
    # Internal LLM call
    # ------------------------------------------------------------------

    async def _call_llm(self, session: VideoSession) -> "DialogueResult":
        """
        Call Kimi K2 with the full dialogue history.
        Handles the submit_video_prompts tool call if triggered.
        """
        if not NVIDIA_API_KEY:
            return DialogueResult(
                reply=(
                    "⚠️ The video dialogue agent is not configured. "
                    "Please set `NVIDIA_API_KEY` in your `.env` file."
                ),
                prompts=None,
                error="NVIDIA_API_KEY not set",
            )

        messages = [
            {"role": "system", "content": VIDEO_DIALOGUE_SYSTEM_PROMPT},
            *session.dialogue_as_openai_messages(),
        ]

        try:
            response = await self._client.chat.completions.create(
                model=KIMI_MODEL,
                messages=messages,
                tools=[SUBMIT_PROMPTS_TOOL],
                tool_choice="auto",
                max_tokens=MAX_TOKENS,
                temperature=0.7,
            )
        except APIError as exc:
            logger.error("Kimi K2 API error in VideoDialogueAgent: %s", exc)
            return DialogueResult(
                reply=(
                    "⚠️ I encountered an error connecting to the AI service. "
                    "Please try again in a moment."
                ),
                prompts=None,
                error=str(exc),
            )
        except Exception as exc:
            logger.error("Unexpected error in VideoDialogueAgent: %s", exc, exc_info=True)
            return DialogueResult(
                reply="⚠️ An unexpected error occurred. Please try again.",
                prompts=None,
                error=str(exc),
            )

        choice = response.choices[0]
        finish_reason = choice.finish_reason
        msg = choice.message

        # --- Tool call: LLM is submitting the final prompts ---
        if finish_reason == "tool_calls" and msg.tool_calls:
            tool_call = msg.tool_calls[0]
            if tool_call.function.name == "submit_video_prompts":
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as exc:
                    logger.error("Failed to parse submit_video_prompts args: %s", exc)
                    return DialogueResult(
                        reply=(
                            "⚠️ I had trouble formatting the prompts. "
                            "Let me try again — could you summarise the key points one more time?"
                        ),
                        prompts=None,
                        error=f"JSON parse error: {exc}",
                    )

                title         = args.get("title", "").strip()
                style_key     = args.get("style_key", "classic").strip().lower()
                custom_prompt = args.get("custom_prompt", "").strip()
                rationale     = args.get("style_rationale", "")

                # Validate style key
                if style_key not in VIDEO_STYLE_NAMES:
                    style_key = "classic"

                # Update the session
                session.title         = title
                session.style_key     = style_key
                session.custom_prompt = custom_prompt
                session.state         = SessionState.CONFIRMING

                # Build the preview reply
                style_desc = VIDEO_STYLE_DESCRIPTIONS.get(style_key, "")
                preview_reply = (
                    f"## 🎬 Here's the Video Plan I've Built\n\n"
                    f"Based on our conversation, here are the prompts I've crafted:\n\n"
                    f"**Title:** {title}\n\n"
                    f"**Visual Style:** `{style_key}` — {style_desc}\n"
                    f"_{rationale}_\n\n"
                    f"**Content Prompt:**\n"
                    f"```\n{custom_prompt}\n```\n\n"
                    f"**Closing Slide (auto-appended):**\n"
                    f"```\n{NPWA_CTA_SUFFIX.strip()}\n```\n\n"
                    f"---\n"
                    f"✅ **Ready to generate!**\n\n"
                    f"Type `!video_confirm` to start generation, "
                    f"or `!video_revise <your notes>` if you'd like me to adjust anything."
                )

                # Record the assistant's preview in dialogue history
                session.add_assistant_message(preview_reply)

                return DialogueResult(
                    reply=preview_reply,
                    prompts={
                        "title": title,
                        "style_key": style_key,
                        "custom_prompt": custom_prompt,
                        "style_rationale": rationale,
                    },
                    error=None,
                )

        # --- Normal conversational reply ---
        reply_text = (msg.content or "").strip()
        if not reply_text:
            reply_text = (
                "I'm thinking through the best approach for your video. "
                "Could you tell me a bit more about the key message you want viewers to take away?"
            )

        session.add_assistant_message(reply_text)
        return DialogueResult(reply=reply_text, prompts=None, error=None)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

class DialogueResult:
    """
    The result of a VideoDialogueAgent call.

    Attributes:
        reply   — The text to post to the Matrix room.
        prompts — Dict with title/style_key/custom_prompt if the LLM submitted
                  final prompts; None otherwise.
        error   — Error string if something went wrong; None on success.
    """

    __slots__ = ("reply", "prompts", "error")

    def __init__(
        self,
        reply: str,
        prompts: Optional[dict],
        error: Optional[str],
    ):
        self.reply   = reply
        self.prompts = prompts
        self.error   = error

    @property
    def has_prompts(self) -> bool:
        return self.prompts is not None
