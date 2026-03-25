# Liberation Bot — Agentic Phase I

**An activist intelligence assistant for victims of Neurowarfare, Havana Syndrome, and Anomalous Health Incidents (AHIs).**

Built by the [NeuroPsychological Warfare Alliance (NPWA)](https://github.com/Renaissance-AI-Solutions) on the Matrix/Element protocol, powered by **Kimi K2** (via NVIDIA NIM) and grounded in the **Liberation Archives** (Google NotebookLM).

---

## What is Liberation Bot?

Liberation Bot serves three core missions:

1. **Dead Man's Switch (DMS):** A secure, encrypted emergency data release system for activists and victims of Neurowarfare. If a registered user goes missing beyond their configured threshold, the bot triggers an automated safety verification pipeline and, upon group consensus, releases their encrypted emergency data.

2. **Agentic AI Assistant (Phase I):** A Kimi K2-powered AI agent that answers questions about Havana Syndrome, Neurowarfare, AHIs, and related topics by querying the **Liberation Archives** — a curated NotebookLM knowledge base maintained by NPWA researchers.

3. **FOIA Request Generator:** A guided, multi-turn conversational workflow that helps members draft legally sound Freedom of Information Act (FOIA) and state public records requests targeting federal agencies (CIA, DOD, FBI, NSA, ODNI, DHS, State Department) and all 50 state governments.

---

## Phase I Features

| Feature | Description |
|---|---|
| **Matrix Chat Memory** | All messages in monitored rooms are stored in a local SQLite database, giving the agent a 90-day rolling context window. |
| **Liberation Archives** | The agent queries a Google NotebookLM notebook containing verified research on Havana Syndrome, Neurowarfare, and AHIs via `notebooklm-py`. |
| **Kimi K2 Agent Core** | Powered by `moonshotai/kimi-k2-instruct` (1T MoE, 32B active parameters) via NVIDIA NIM's free OpenAI-compatible API. |
| **FOIA Request Generator** | A stateful, multi-turn DM workflow that guides users through drafting FOIA and state public records requests. Covers Federal FOIA (5 U.S.C. § 552) and all 50 state laws. Includes AHI-specific guidance, fee waiver drafting, and submission instructions. |
| **Video Planning Workflow** | A dedicated Matrix room where the bot conducts a natural dialogue with the group to brainstorm, plan, and automatically generate advocacy videos via NotebookLM. |
| **Secure Tool Sandbox** | The agent can ONLY call `query_liberation_archives` and `submit_video_prompts`. No shell access, no file system access, no vault access. |
| **Knowledge Base Log** | Every query to the Liberation Archives and every agent response is logged to `agent_queries` for auditability. |
| **Dead Man's Switch** | All original DMS functionality is fully preserved and unchanged. |
| **Dream Engine** | Nightly LLM-powered memory consolidation. Synthesizes chat history into long-term user and operational memories. |

---

## Architecture

```
Matrix DM or Group Room
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
    ├── !foia_start ──► FOIASessionManager.start_session()
    │       │
    │       └── [DM free-text] ──► FOIADialogueAgent.process_message()
    │                                   │
    │                                   ├── Kimi K2 (NVIDIA NIM)
    │                                   │       └── tool_call: submit_foia_draft()
    │                                   │           [Jurisdiction data: foia_jurisdictions.py]
    │                                   └── [on !foia_confirm] ──► db.save_foia_request()
    │
    ├── !video_start ──► VideoRoomHandler  [Video Planning Room only]
    │
    └── DMS Commands ──► [Unchanged Dead Man's Switch pipeline]
```

---

## FOIA Request Generator

The FOIA feature enables members to draft public records requests through a guided, conversational workflow directly in a Matrix DM with the bot.

### How It Works

1. **Start a session** with `!foia_start` in a DM with the bot.
2. **Answer questions** — the bot (powered by Kimi K2) asks one or two questions at a time to gather: jurisdiction, target agency, subject matter, date range, keywords, requester name, contact information, and fee waiver eligibility.
3. **Review the draft** — the bot generates the complete, formatted letter using the correct statutory citation for your jurisdiction and presents it for review.
4. **Revise or confirm** — use `!foia_revise <notes>` to request changes, or `!foia_confirm` to accept the draft.
5. **Receive submission instructions** — upon confirmation, the bot provides the agency's FOIA email/portal, the legal response deadline, and next steps including appeal information.

### Jurisdiction Coverage

The FOIA generator covers all 51 jurisdictions:

| Jurisdiction | Law | Response Deadline |
|---|---|---|
| **Federal** | Freedom of Information Act (5 U.S.C. § 552) | 20 working days |
| **California** | California Public Records Act (CPRA) | 10 calendar days |
| **New York** | Freedom of Information Law (FOIL) | 5 business days (acknowledgment) |
| **Texas** | Texas Public Information Act (PIA) | 10 business days |
| **Florida** | Florida Sunshine Law | Prompt (no specific deadline) |
| **Virginia** | Virginia FOIA ⚠️ *Residents only* | 5 business days |
| **All other states** | State-specific laws | Varies (see `!foia_jurisdictions`) |

> **Note:** Arkansas, Delaware, Tennessee, and Virginia restrict public records requests to state residents. The bot warns users of this restriction automatically.

### AHI/Neurowarfare-Specific Guidance

The bot's system prompt includes specialized guidance for AHI-related requests:
- Uses the official term **"Anomalous Health Incidents (AHIs)"** in all letters.
- Recommends citing the **HAVANA Act (Pub. L. 117-46, 2021)** as context.
- Suggests targeting CIA, DOD (DIA), FBI, State Department, NSA, ODNI, and DHS.
- Advises on realistic timelines (months to years for national security agencies).
- Guides fee waiver justification for public interest requests.

### Federal Agency Quick Reference

Use `!foia_agencies` to see the full list. Key targets for AHI/Neurowarfare requests:

| Agency | FOIA Email | Portal |
|---|---|---|
| CIA | `cia-foia@ucia.gov` | [cia.gov/foia](https://www.cia.gov/resources/foia/) |
| DOD | `osd.pentagon.osd-cmo.mbx.osd-foia@mail.mil` | [esd.whs.mil/FOIA](https://www.esd.whs.mil/FOIA/) |
| FBI | `foiparequest@fbi.gov` | [fbi.gov/foipa](https://www.fbi.gov/services/records-management/foipa) |
| State Dept | `FOIArequest@state.gov` | [foia.state.gov](https://foia.state.gov/) |
| NSA | `nsafoia@nsa.gov` | [nsa.gov/FOIA](https://www.nsa.gov/Resources/Everyone/FOIA/) |
| ODNI | `odni-foia@dni.gov` | [dni.gov/FOIA](https://www.dni.gov/index.php/who-we-are/organizations/general-counsel/foia) |
| DHS | `dhsfoia@hq.dhs.gov` | [dhs.gov/foia](https://www.dhs.gov/foia) |
| DIA | `dia-foia@dodiis.mil` | [dia.mil/FOIA](https://www.dia.mil/About/FOIA/) |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Renaissance-AI-Solutions/MatrixLiberationBot.git
cd MatrixLiberationBot
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
| `NVIDIA_API_KEY` | Yes | NVIDIA NIM API key for Kimi K2 (used by agent, video, and FOIA) |
| `LIBERATION_ARCHIVES_NOTEBOOK_ID` | Yes | NotebookLM notebook ID |
| `NOTEBOOKLM_AUTH_JSON` | Yes* | Google session auth JSON |
| `MATRIX_VIDEO_ROOM_ID` | No | Matrix room ID for the video planning room |

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

### FOIA Request Generator (DM the bot)

```
!foia_start              — Begin a new FOIA drafting session
!foia_jurisdictions      — List all supported jurisdictions (Federal + 50 states)
!foia_agencies           — List recommended federal agencies for AHI requests
!foia_preview            — Re-show your current draft letter
!foia_revise <notes>     — Ask the bot to revise the draft
!foia_confirm            — Accept the draft and get submission instructions
!foia_cancel             — Cancel the current session
!foia_history            — View your past finalized FOIA requests
```

### Video Planning Room (Video Planning and Generation room only)

```
!video_start              — Begin a new video planning session
!video_styles             — List all available visual styles and saved favourites
!video_save_style <name>  — Save the current session's style as a reusable named favourite
!video_preview            — Show the current prompt preview at any time
!video_revise <notes>     — Ask the bot to revise the prompts based on your feedback
!video_confirm            — Confirm the prompts and start video generation (any group member)
!video_cancel             — Cancel the current session
!video_history            — Show recent completed videos
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
- **FOIA agent isolation.** The FOIA dialogue agent has no access to the Liberation Archives, vault, or any sensitive tables. It is a pure conversational drafting agent.
- **Full audit trail.** Every agent interaction is logged to `agent_queries` and `audit_log`. Every FOIA session is logged to `foia_sessions`. Every finalized letter is stored in `foia_requests`.
- **Privacy-safe FOIA storage.** The full dialogue history of a FOIA session is held in memory only and is never persisted to the database. Only the finalized letter and metadata are saved.

---

## Database Schema

| Table | Purpose |
|---|---|
| `registered_users` | Dead Man's Switch registrations |
| `user_profiles` | OSINT-relevant public profile data |
| `emergency_vault` | AES-256-GCM encrypted emergency data |
| `consensus_votes` | Group consensus vote tracking |
| `audit_log` | Privacy-safe event log |
| `chat_history` | Full Matrix chat history for agent memory |
| `agent_queries` | Liberation Archives query/response log |
| `video_sessions` | Video planning session archive |
| `video_style_library` | Saved reusable visual style prompts |
| `user_memories` | Per-user long-term consolidated memories (Dream Engine) |
| `operational_memories` | Org-wide operational long-term memories (Dream Engine) |
| `dream_cycles` | Audit log of Dream consolidation runs |
| `foia_requests` | **[NEW]** Finalized FOIA request letters archive |
| `foia_sessions` | **[NEW]** FOIA drafting session audit log |

---

## Phase II Roadmap

- Web search tool (read-only) for real-time news
- YouTube / Substack / TikTok automated publishing
- FOIA status tracking and appeal deadline reminders *(FOIA generator implemented in Phase I)*
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
