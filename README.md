# Liberation Bot — Agentic Phase I

**An activist intelligence assistant for victims of Neurowarfare, Havana Syndrome, and Anomalous Health Incidents (AHIs).**

Built by the [NeuroPsychological Warfare Alliance (NPWA)](https://github.com/Renaissance-AI-Solutions) on the Matrix/Element protocol, powered by **Kimi K2** (via NVIDIA NIM) and grounded in the **Liberation Archives** (Google NotebookLM).

---

## What is Liberation Bot?

Liberation Bot serves two core missions:

1. **Dead Man's Switch (DMS):** A secure, encrypted emergency data release system for activists and victims of Neurowarfare. If a registered user goes missing beyond their configured threshold, the bot triggers an automated safety verification pipeline and, upon group consensus, releases their encrypted emergency data.

2. **Agentic AI Assistant (Phase I):** A Kimi K2-powered AI agent that answers questions about Havana Syndrome, Neurowarfare, AHIs, and related topics by querying the **Liberation Archives** — a curated NotebookLM knowledge base maintained by NPWA researchers.

---

## Phase I Features

| Feature | Description |
|---|---|
| **Matrix Chat Memory** | All messages in monitored rooms are stored in a local SQLite database, giving the agent a 90-day rolling context window. |
| **Liberation Archives** | The agent queries a Google NotebookLM notebook containing verified research on Havana Syndrome, Neurowarfare, and AHIs via `notebooklm-py`. |
| **Kimi K2 Agent Core** | Powered by `moonshotai/kimi-k2-instruct` (1T MoE, 32B active parameters) via NVIDIA NIM's free OpenAI-compatible API. |
| **Video Planning Workflow** | A dedicated Matrix room where the bot conducts a natural dialogue with the group to brainstorm, plan, and automatically generate advocacy videos via NotebookLM. |
| **Secure Tool Sandbox** | The agent can ONLY call `query_liberation_archives` and `submit_video_prompts`. No shell access, no file system access, no vault access. |
| **Knowledge Base Log** | Every query to the Liberation Archives and every agent response is logged to `agent_queries` for auditability. |
| **Dead Man's Switch** | All original DMS functionality is fully preserved and unchanged. |

---

## Architecture

```
Matrix Room
    │
    ▼
bot/bot.py ──── on_any_message ──► db.save_message()  [chat_history]
    │
    ├── @bot <query> ──► AgentCore.generate_response()
    │                         │
    │                         ├── Kimi K2 (NVIDIA NIM)
    │                         │       └── tool_call: query_liberation_archives()
    │                         │                   └── NotebookLMClient.chat.ask()
    │                         │                       [Liberation Archives notebook]
    │                         └── db.log_agent_query()  [agent_queries]
    │
    └── DMS Commands ──► [Unchanged Dead Man's Switch pipeline]
```

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Renaissance-AI-Solutions/LiberationBot-Agentic.git
cd LiberationBot-Agentic
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### 3. Set up NotebookLM authentication

```bash
pip install notebooklm-py
notebooklm login   # Opens browser for Google auth
# Copy ~/.notebooklm/storage_state.json contents into NOTEBOOKLM_AUTH_JSON in .env
```

### 4. Get your NVIDIA API key

1. Go to [build.nvidia.com/settings/api-keys](https://build.nvidia.com/settings/api-keys)
2. Create a free API key
3. Set `NVIDIA_API_KEY=nvapi-...` in `.env`

### 5. Run

```bash
mkdir -p data
python3 main.py
```

---

## Configuration

See `.env.example` for the full reference. Key variables:

| Variable | Required | Description |
|---|---|---|
| `MATRIX_BOT_USER_ID` | Yes | Bot's Matrix user ID |
| `MATRIX_BOT_PASSWORD` | Yes | Bot account password |
| `MATRIX_HOMESERVER_URL` | Yes | Matrix homeserver URL |
| `MATRIX_GROUP_ROOM_ID` | Yes | Monitored group room ID |
| `BOT_MASTER_KEY` | Yes | 64-char hex key for vault encryption |
| `NVIDIA_API_KEY` | Yes | NVIDIA NIM API key for Kimi K2 |
| `LIBERATION_ARCHIVES_NOTEBOOK_ID` | Yes | NotebookLM notebook ID |
| `NOTEBOOKLM_AUTH_JSON` | Yes* | Google session auth JSON |

---

## Usage

### Agentic AI (Group Room or DM)

```
@bot What are the neurological symptoms of Havana Syndrome?
@bot What legal options do AHI victims have in the United States?
@bot What is the Frey effect and how does it relate to AHIs?
!archives   — Show Liberation Archives topic overview
!help       — Show full command reference
```

### Video Planning Room (Video Planning and Generation room only)

```
!video_start              — Begin a new video planning session. The bot will lead a dialogue to build the prompts.
!video_styles             — List all available visual styles and saved favourites.
!video_save_style <name>  — Save the current session's style as a reusable named favourite.
!video_preview            — Show the current prompt preview at any time.
!video_revise <notes>     — Ask the bot to revise the prompts based on your feedback.
!video_confirm            — Confirm the prompts and start video generation (any group member).
!video_cancel             — Cancel the current session.
!video_history            — Show recent completed videos.
```

### Dead Man's Switch (DM the bot)

```
!register_switch       — Begin registration
!checkin               — Reset your activity timer
!my_status             — View your status
!update_emergency_data — Update your emergency data
!deregister            — Remove your registration
```

### Dead Man's Switch (Group room)

```
!activate_switch @user:server  — Cast a consensus vote
!cancel_alert @user:server     — Cancel an active alert (admin)
```

---

## Security Model

- **No shell execution.** The agent cannot call `subprocess`, `os.system`, or any execution primitive.
- **No file system access.** The agent cannot read or write files on the server.
- **No vault access.** The agent has no access to the `emergency_vault` table or encrypted user data.
- **Single tool.** The only tool exposed to the agent is `query_liberation_archives` (read-only HTTP to Google).
- **Full audit trail.** Every agent interaction is logged to `agent_queries` and `audit_log`.

---

## Phase II Roadmap

- Web search tool (read-only) for real-time news
- YouTube / Substack / TikTok automated publishing
- FOIA request drafting for victims
- NPWA advocacy strategy assistant

---

## Testing

```bash
pytest tests/ -v --asyncio-mode=auto
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

*Built with care for the victims of Neurowarfare and Havana Syndrome. You are not alone.*
