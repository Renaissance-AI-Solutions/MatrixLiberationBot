# Liberation Bot — Dead Man's Switch Web UI

A standalone web application that lets registered Liberation Bot users view and edit their Dead Man's Switch profile through a browser, rather than through Matrix chat commands.

---

## Architecture

```
dms-ui/
├── backend/          FastAPI (Python) — REST API
│   ├── main.py       16 API endpoints, JWT auth, OTP flow, Dream memory API
│   ├── db.py         Async SQLite layer (shares the bot's DB file)
│   ├── matrix_otp.py OTP delivery via Matrix DM
│   └── .env.example  Environment variable reference
└── frontend/         Vite + React + TypeScript + TailwindCSS
    └── src/
        ├── api.ts          Axios client + session management + memory API types
        ├── App.tsx          Routing + auth guard
        ├── components/
        │   └── MemoryProfile.tsx  AI Memory Profile section (Dream Engine UI)
        └── pages/
            ├── Login.tsx    Matrix OTP login flow
            └── Dashboard.tsx Full profile editor (includes AI Memory Profile)
```

The backend opens the **same SQLite database file** as Liberation Bot. No data migration is needed — the UI reads and writes to the existing `registered_users`, `user_profiles`, and `emergency_vault` tables, and adds UI-specific and Dream memory tables.

---

## Authentication Flow

1. User navigates to the UI and enters their Matrix ID (`@alice:matrix.org`).
2. The backend generates a 6-digit OTP, hashes it with Argon2id, and delivers it via Matrix DM using the bot's credentials.
3. The user enters the code in the animated digit-box input.
4. On success, the backend issues an 8-hour JWT. The session is stored in `localStorage` and auto-expires.

This proves the user owns the Matrix account without requiring a separate password.

---

## What Users Can Do

| Section | Actions |
| :--- | :--- |
| **Status Banner** | See armed/disarmed status, last check-in time, next trigger time, and perform a manual check-in |
| **Personal Details** | Edit legal name, date of birth, physical address, and OSINT location |
| **Emergency Contacts** | Add/remove contacts with name, relationship, phone, email, and Matrix ID |
| **Social Media Profiles** | Add/remove platform + URL pairs (used by the OSINT scanner) |
| **Vault — Final Message** | Write/edit the Markdown final message released when the switch triggers |
| **Trigger Configuration** | Set the missing threshold (24h–30d) and configure release actions (Matrix DM, Matrix room, webhook) |
| **AI Memory Profile** | View, edit, and delete the long-term memories the Dream Engine has consolidated about you. See full version history for every memory. Restore deleted memories. View Dream Engine status and last cycle stats. |
| **Audit Log** | View a timestamped log of all logins, profile changes, check-ins, and memory edits |

---

## Dream Engine — AI Memory System

Liberation Bot includes a nightly memory consolidation system inspired by Claude Code's "Auto Dream" feature and research into sleep-time compute. The Dream Engine runs at **03:00 UTC** each night and:

1. Reviews all Matrix chat transcripts since the last successful cycle
2. Extracts information relevant to the NPWA mission (member situations, neurowarfare documentation, operational planning)
3. Consolidates extracted information into two permanent memory stores:
   - **User Memories** — per-member long-term memory (symptoms, legal status, history, preferences, threat profile)
   - **Operational Memories** — org-wide memory (neurowarfare programs, countermeasures, legal strategy, activism planning)
4. Merges new insights with existing memories, resolving contradictions and incrementing version numbers
5. Logs every cycle in `dream_cycles` for full auditability

### Memory API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/memories` | List all AI memories for the authenticated user |
| `GET` | `/api/memories/{id}` | Get a single memory with full version history |
| `PUT` | `/api/memories/{id}` | Edit a memory (user-initiated correction) |
| `DELETE` | `/api/memories/{id}` | Soft-delete a memory (preserves version history) |
| `POST` | `/api/memories/{id}/restore` | Restore a soft-deleted memory |
| `GET` | `/api/dream/status` | Get Dream Engine status, last cycle stats, and next run time |

### Memory Categories (User)

| Category | Contents |
| :--- | :--- |
| `symptoms` | Health disclosures, AHI symptoms, medical history |
| `legal_status` | Ongoing cases, attorneys, jurisdictions, legal strategies |
| `personal_history` | Background relevant to activism or victimization |
| `preferences` | Communication preferences, expertise level |
| `triggers` | Topics or situations that cause distress |
| `relationships` | Key contacts, allies, adversaries |
| `notes` | General notes that don't fit other categories |

### Operational Memory Topics

| Topic | Contents |
| :--- | :--- |
| `neurowarfare_programs` | Named programs, agencies, technologies, incidents |
| `countermeasures` | Protection strategies, tools, mitigation techniques |
| `legal_strategy` | Group-level legal approaches, precedents, filings |
| `operational_planning` | Activism plans, campaigns, events, timelines |
| `threat_actors` | Identified individuals, organizations, or agencies |
| `resources` | Useful documents, contacts, websites, tools |
| `brainstorming` | Significant ideas or proposals meriting follow-up |

---

## Setup

### 1. Backend

```bash
cd dms-ui/backend
cp .env.example .env
# Edit .env — set DATABASE_PATH, DMS_JWT_SECRET, and Matrix bot credentials
pip install -r requirements.txt
python main.py
# Runs on http://localhost:8001
```

### 2. Frontend

```bash
cd dms-ui/frontend
pnpm install
pnpm dev
# Runs on http://localhost:5173
# API calls are proxied to http://localhost:8001 automatically
```

### 3. Production Build

```bash
cd dms-ui/frontend
pnpm build
# Output in dist/ — serve with nginx, Caddy, or any static host
# Point the backend URL in vite.config.ts proxy or set VITE_API_URL
```

---

## Environment Variables (Backend)

| Variable | Required | Description |
| :--- | :---: | :--- |
| `DATABASE_PATH` | Yes | Path to the bot's SQLite file (e.g. `../../data/liberation_bot.db`) |
| `DMS_JWT_SECRET` | Yes | Random 64-char hex string for JWT signing |
| `MATRIX_HOMESERVER_URL` | Yes | Bot's homeserver URL |
| `MATRIX_BOT_ACCESS_TOKEN` | Yes* | Bot access token for sending OTP DMs |
| `MATRIX_BOT_USER_ID` | Yes* | Bot Matrix ID |
| `MATRIX_BOT_PASSWORD` | No | Used to obtain access token if not set directly |
| `DMS_SESSION_HOURS` | No | Session duration in hours (default: 8) |
| `CORS_ORIGINS` | No | Comma-separated allowed origins (default: `http://localhost:5173`) |

*One of `MATRIX_BOT_ACCESS_TOKEN` or `MATRIX_BOT_PASSWORD` must be set.

---

## Security Notes

- OTPs are hashed with **Argon2id** before storage and are single-use with a 10-minute expiry.
- Sessions are **JWT-signed** (HS256) and expire after 8 hours.
- The backend never stores plaintext OTPs.
- The vault text is stored server-side in the same security boundary as the bot (the server holds the master key for the bot's AES-GCM vault anyway).
- All profile changes and memory edits are written to the audit log.
- The Dream Engine **never reads emergency vault data**. OTP codes and bot commands are stripped from transcripts before LLM processing.
- User memories are soft-deleted only — version history is always preserved.
