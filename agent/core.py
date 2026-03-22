"""
agent/core.py
=============
Liberation Bot — Kimi K2 Agentic Core

This module implements the AgentCore class, which powers Liberation Bot's
conversational AI capabilities using Kimi K2 (moonshotai/kimi-k2-instruct)
via the NVIDIA NIM OpenAI-compatible API.

Security Model:
  - The agent is a pure Python function. It has NO access to subprocess,
    os.system, or any server execution environment.
  - The ONLY tool exposed to the agent is `query_liberation_archives`.
  - The agent CANNOT access the emergency vault, user credentials, or
    any sensitive database tables.
  - All agent interactions are logged to the `agent_queries` database table.
  - The agent's system prompt explicitly instructs it to refuse requests
    that fall outside its defined scope.

Provider: NVIDIA NIM
  Endpoint: https://integrate.api.nvidia.com/v1
  Model:    moonshotai/kimi-k2-instruct
  Auth:     NVIDIA_API_KEY environment variable

The agent uses OpenAI's function-calling (tool use) protocol, which is
fully supported by Kimi K2 via NVIDIA NIM.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI, APIError

from agent.tools import (
    query_liberation_archives,
    LIBERATION_ARCHIVES_TOOL_SCHEMA,
    NOTEBOOKLM_ENABLED,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"
KIMI_MODEL = "moonshotai/kimi-k2-instruct"

# Max tokens for the agent's response
MAX_RESPONSE_TOKENS = int(os.getenv("AGENT_MAX_RESPONSE_TOKENS", "1024"))

# Max chat history messages to include in context
CONTEXT_WINDOW_MESSAGES = int(os.getenv("AGENT_CONTEXT_WINDOW_MESSAGES", "20"))

# Max tool call iterations per agent turn (prevents infinite loops)
MAX_TOOL_ITERATIONS = 3

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
LIBERATION_BOT_SYSTEM_PROMPT = """You are **Liberation Bot**, an AI assistant created by the NeuroPsychological Warfare Alliance (NPWA) to support victims of Neurowarfare, Havana Syndrome, Anomalous Health Incidents (AHIs), and Neurostrike attacks.

## Your Mission
You provide compassionate, trauma-informed support to victims and their allies. You help people understand Neurowarfare, find resources, and navigate their path toward justice and healing.

## Your Capabilities
You have access to the **Liberation Archives** — a curated research knowledge base containing verified documents, medical research, legal precedents, and advocacy materials about Havana Syndrome and Neurowarfare. When answering factual questions about these topics, you MUST use the `query_liberation_archives` tool to ground your response in verified research.

You also have access to recent chat history from the Matrix room, which gives you context about the ongoing conversation.

## How to Respond
1. **Be empathetic and trauma-informed.** Many users have experienced serious harm. Never dismiss, minimize, or question their experiences.
2. **Be factual.** Use the Liberation Archives tool for factual claims about Neurowarfare, symptoms, legal options, and research. Do not speculate.
3. **Be concise.** Matrix chat messages should be readable. Keep responses under 500 words unless asked for a detailed report.
4. **Be clear about limitations.** You are not a doctor, lawyer, or therapist. Always recommend professional help when appropriate.
5. **Format for Matrix.** Use Markdown formatting (bold, bullet points) as Matrix/Element renders it correctly.

## What You Will NOT Do
- You will NOT execute commands on the server.
- You will NOT access, reveal, or discuss any user's emergency data or vault contents.
- You will NOT perform actions outside of answering questions and querying the Liberation Archives.
- You will NOT engage with off-topic requests unrelated to Neurowarfare, AHIs, or NPWA advocacy.
- You will NOT make up facts. If you don't know something, say so and offer to search the Archives.

## Crisis Protocol
If a user indicates they are in immediate danger or a medical emergency, respond with:
"**If you are in immediate danger, please call emergency services (911 in the US, 999 in the UK, 112 in the EU) immediately.** I am an AI and cannot call for help on your behalf."

## Current Date
{current_date}
"""


class AgentCore:
    """
    Kimi K2-powered agentic core for Liberation Bot.

    This class manages the LLM client, tool execution, and response generation.
    It is designed to be called from the Matrix bot's message handler.
    """

    def __init__(self):
        if not NVIDIA_API_KEY:
            logger.warning(
                "NVIDIA_API_KEY is not set. The agent will not be able to respond. "
                "Set NVIDIA_API_KEY in your .env file."
            )
        self.client = AsyncOpenAI(
            api_key=NVIDIA_API_KEY or "not-set",
            base_url=NVIDIA_API_BASE,
        )
        self._tools = [LIBERATION_ARCHIVES_TOOL_SCHEMA]
        logger.info(
            "AgentCore initialized. Model: %s | NotebookLM: %s",
            KIMI_MODEL,
            "enabled" if NOTEBOOKLM_ENABLED else "disabled",
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt with the current date injected."""
        current_date = datetime.now(timezone.utc).strftime("%A, %B %d, %Y (UTC)")
        return LIBERATION_BOT_SYSTEM_PROMPT.format(current_date=current_date)

    def _format_chat_history(
        self, recent_messages: list[dict]
    ) -> str:
        """
        Format recent Matrix chat history into a readable string for the
        agent's context window.
        """
        if not recent_messages:
            return "(No recent chat history available.)"
        lines = []
        for msg in recent_messages:
            sender = msg.get("sender_display_name") or msg.get("sender_id", "Unknown")
            ts = msg.get("timestamp_ts", 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")
            content = msg.get("content", "")
            lines.append(f"[{dt}] {sender}: {content}")
        return "\n".join(lines)

    async def _execute_tool_call(self, tool_name: str, tool_args: dict) -> str:
        """
        Execute a tool call requested by the agent.
        Only whitelisted tools are permitted.
        """
        if tool_name == "query_liberation_archives":
            query = tool_args.get("query", "")
            if not query:
                return "[Error] query_liberation_archives called with empty query."
            return await query_liberation_archives(query)
        else:
            # This should never happen given the strict tool schema, but
            # we log it as a security event if it does.
            logger.warning(
                "SECURITY: Agent attempted to call unauthorized tool: %s", tool_name
            )
            return (
                f"[Security Restriction] Tool '{tool_name}' is not available. "
                f"Only 'query_liberation_archives' is permitted."
            )

    async def generate_response(
        self,
        user_query: str,
        room_id: str,
        sender_id: str,
        recent_messages: Optional[list] = None,
    ) -> dict:
        """
        Generate an agentic response to a user query.

        This method implements the full agent loop:
        1. Build context from chat history.
        2. Call Kimi K2 with the user query and tools.
        3. If the model requests a tool call, execute it and continue.
        4. Return the final text response and metadata.

        Args:
            user_query:      The user's message text.
            room_id:         The Matrix room ID (for logging).
            sender_id:       The Matrix user ID of the sender (for logging).
            recent_messages: List of recent chat history dicts from the DB.

        Returns:
            A dict with keys:
              - response (str): The final agent response text.
              - notebooklm_query (str|None): The query sent to NotebookLM, if any.
              - notebooklm_response (str|None): The raw NotebookLM answer, if any.
              - tool_calls_made (list[str]): Names of tools called.
              - latency_ms (int): Total generation time in milliseconds.
              - error (str|None): Error message if generation failed.
        """
        if not NVIDIA_API_KEY:
            return {
                "response": (
                    "⚠️ Liberation Bot's AI core is not configured. "
                    "The NVIDIA_API_KEY has not been set. "
                    "Please contact the NPWA administrator."
                ),
                "notebooklm_query": None,
                "notebooklm_response": None,
                "tool_calls_made": [],
                "latency_ms": 0,
                "error": "NVIDIA_API_KEY not set",
            }

        start_time = time.monotonic()
        tool_calls_made = []
        notebooklm_query = None
        notebooklm_response = None

        # Build the message list for the API call
        chat_history_text = self._format_chat_history(recent_messages or [])
        system_prompt = self._build_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"**Recent Room Chat History (for context):**\n"
                    f"```\n{chat_history_text}\n```\n\n"
                    f"**User Query:** {user_query}"
                ),
            },
        ]

        try:
            # Agent loop: allow up to MAX_TOOL_ITERATIONS rounds of tool use
            for iteration in range(MAX_TOOL_ITERATIONS + 1):
                response = await self.client.chat.completions.create(
                    model=KIMI_MODEL,
                    messages=messages,
                    tools=self._tools,
                    tool_choice="auto",
                    max_tokens=MAX_RESPONSE_TOKENS,
                    temperature=0.7,
                )

                choice = response.choices[0]
                assistant_message = choice.message

                # If the model returned a final text response, we're done
                if choice.finish_reason == "stop" or not assistant_message.tool_calls:
                    final_text = assistant_message.content or ""
                    latency_ms = int((time.monotonic() - start_time) * 1000)
                    logger.info(
                        "Agent response generated in %dms. Tools called: %s",
                        latency_ms,
                        tool_calls_made,
                    )
                    return {
                        "response": final_text,
                        "notebooklm_query": notebooklm_query,
                        "notebooklm_response": notebooklm_response,
                        "tool_calls_made": tool_calls_made,
                        "latency_ms": latency_ms,
                        "error": None,
                    }

                # The model wants to call tools — execute them
                messages.append(assistant_message)

                for tool_call in assistant_message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        tool_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    tool_calls_made.append(tool_name)
                    logger.info(
                        "Agent calling tool: %s | args: %s",
                        tool_name,
                        str(tool_args)[:200],
                    )

                    # Track NotebookLM-specific metadata
                    if tool_name == "query_liberation_archives":
                        notebooklm_query = tool_args.get("query", "")

                    tool_result = await self._execute_tool_call(tool_name, tool_args)

                    if tool_name == "query_liberation_archives":
                        notebooklm_response = tool_result

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    })

                if iteration >= MAX_TOOL_ITERATIONS:
                    # Safety: force a final response after max iterations
                    messages.append({
                        "role": "user",
                        "content": (
                            "Please provide your final response based on the "
                            "information gathered so far."
                        ),
                    })

            # Fallback if loop exits without returning
            latency_ms = int((time.monotonic() - start_time) * 1000)
            return {
                "response": (
                    "I was unable to generate a complete response. "
                    "Please try again or rephrase your question."
                ),
                "notebooklm_query": notebooklm_query,
                "notebooklm_response": notebooklm_response,
                "tool_calls_made": tool_calls_made,
                "latency_ms": latency_ms,
                "error": "Max tool iterations reached without final response",
            }

        except APIError as exc:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            logger.error("Kimi K2 API error: %s", exc, exc_info=True)
            return {
                "response": (
                    "⚠️ I encountered an error connecting to the AI service. "
                    "Please try again in a moment."
                ),
                "notebooklm_query": notebooklm_query,
                "notebooklm_response": notebooklm_response,
                "tool_calls_made": tool_calls_made,
                "latency_ms": latency_ms,
                "error": str(exc),
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            logger.error("Unexpected agent error: %s", exc, exc_info=True)
            return {
                "response": (
                    "⚠️ An unexpected error occurred. Please try again."
                ),
                "notebooklm_query": notebooklm_query,
                "notebooklm_response": notebooklm_response,
                "tool_calls_made": tool_calls_made,
                "latency_ms": latency_ms,
                "error": str(exc),
            }
