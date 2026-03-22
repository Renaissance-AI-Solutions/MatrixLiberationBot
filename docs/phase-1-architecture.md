# Liberation Bot: Phase I Agentic Architecture

## 1. Overview
The Liberation Bot is evolving from a deterministic Matrix Wellness Monitor (Dead Man's Switch) into an **Agentic Activist Intelligence Assistant**. This Phase I upgrade introduces a secure, tool-restricted LLM agent powered by **Kimi K2** (via NVIDIA NIM) and grounded in a secure knowledge base using **Google NotebookLM** (via `notebooklm-py`).

## 2. Core Objectives
1. **Matrix Chat Memory:** Record all group chat history into a local SQLite database to provide context for the agent.
2. **Liberation Archives (NotebookLM):** Allow the agent to query a curated NotebookLM instance containing research on Havana Syndrome, Neurowarfare, and AHIs.
3. **Secure Agentic Core:** Implement a Kimi K2-powered agent that can communicate in Matrix but is strictly sandboxed from executing shell commands or accessing the server's file system.

## 3. Architecture Components

### 3.1 Matrix Communication Layer
- **Framework:** `simplematrixbotlib` / `matrix-nio` (existing).
- **Enhancement:** A new message router in `bot.py` that detects `@bot` mentions or direct queries and routes them to the Agent Core.

### 3.2 Memory & Persistence (`db/database.py`)
- **Chat History Table:** A new SQLite table `chat_history` storing `(message_id, room_id, sender_id, timestamp, content)`.
- **Agent Memory Table:** A new SQLite table `agent_queries` storing `(query_id, user_id, timestamp, query_text, notebooklm_response, final_agent_response)`.
- **Constraint:** The agent will only have read access to recent chat history and cannot access the `emergency_vault` table.

### 3.3 The Agent Core (Kimi K2)
- **Provider:** NVIDIA NIM API (`https://integrate.api.nvidia.com/v1`).
- **Model:** `moonshotai/kimi-k2-instruct` (1T MoE, 32B active, 128K context).
- **Integration:** Standard OpenAI Python client configured to use the NVIDIA endpoint.
- **System Prompt:** Instructs the agent to act as the Liberation Bot, providing trauma-informed support and relying strictly on the NotebookLM tool for factual claims about Neurowarfare.

### 3.4 The Knowledge Base Tool (`notebooklm-py`)
- **Library:** `teng-lin/notebooklm-py` (Async Python client).
- **Authentication:** `NOTEBOOKLM_AUTH_JSON` environment variable containing session cookies.
- **Tool Implementation:** A Python function `query_liberation_archives(query: str) -> str` that wraps `client.chat.ask(notebook_id, query)`.
- **Agent Binding:** The Kimi K2 agent will be provided this function as an OpenAI-compatible tool call.

## 4. Security Model
- **No Shell Access:** The agent is implemented as a pure Python function calling the OpenAI API. It has no access to `subprocess`, `os.system`, or any execution environment.
- **No File System Access:** The agent cannot read or write files. It only receives text context from the database and returns text.
- **Read-Only Context:** The agent is provided recent chat history as a string in its prompt. It cannot execute SQL queries directly.
- **Tool Restriction:** The only tool exposed to the agent is `query_liberation_archives`.

## 5. Data Flow
1. User sends `@bot What is the latest on Havana Syndrome?` in Matrix.
2. `bot.py` listener catches the message, saves it to `chat_history`.
3. `bot.py` retrieves the last 20 messages from `chat_history` for context.
4. `bot.py` calls `AgentCore.generate_response(context, user_query)`.
5. Kimi K2 decides it needs facts and emits a tool call to `query_liberation_archives`.
6. The tool queries NotebookLM via `notebooklm-py` and returns the summary.
7. Kimi K2 synthesizes the final response.
8. `bot.py` logs the interaction to `agent_queries` and sends the response back to Matrix.
