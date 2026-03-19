# Matrix Ecosystem Security and Wellness Monitor

A highly secure, privacy-first AI agent designed to act as an automated "Dead Man's Switch" and wellness monitor for group members within the Element/Matrix ecosystem.

## Features

- **End-to-End Encryption (E2EE):** Fully supports Matrix E2EE for all direct messages and group communications.
- **Secure Data Vaulting:** Emergency data is encrypted immediately upon receipt using AES-256-GCM. The plaintext is never stored on disk or in memory.
- **Heartbeat Monitoring:** Tracks user activity in the group chat and alerts if a user exceeds their custom "missing" threshold.
- **Automated OSINT Verification:** Before escalating, the bot ethically checks provided public social media handles and searches local news/obituaries (via SerpAPI) to find legitimate reasons for absence.
- **Group Consensus Escalation:** If no activity is found, the bot alerts the group. A configurable number of group members must vote (`!activate_switch`) to release the data.
- **Zero-Knowledge Decryption:** The decryption key is derived at runtime only when group consensus is reached.

## Architecture & Security Model

- **Database:** SQLite (via `aiosqlite`) storing only ciphertext, IVs, and salts.
- **Encryption:** `cryptography` library using AES-256-GCM and PBKDF2-HMAC-SHA256.
- **Matrix Client:** Built on `simplematrixbotlib` and `matrix-nio`.

## Prerequisites

- A Matrix homeserver account for the bot.
- A Matrix group room where the bot is invited and has permission to read messages.
- Python 3.11+ and `libolm-dev` (for E2EE support).
- (Optional) A [SerpAPI](https://serpapi.com/) key for news and obituary searches.

## Installation (Docker - Recommended)

1. Clone the repository.
2. Copy `.env.example` to `.env` and fill in your configuration:
   ```bash
   cp .env.example .env
   ```
3. Generate a secure 256-bit master key for your `.env` file:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
4. Build and run using Docker Compose (or standard Docker):
   ```bash
   docker build -t matrix-wellness-bot .
   docker run -d --name wellness-bot --env-file .env -v $(pwd)/data:/app/data matrix-wellness-bot
   ```

## Installation (Manual)

1. Install system dependencies (Ubuntu/Debian):
   ```bash
   sudo apt-get install libolm-dev gcc python3-dev
   ```
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure your `.env` file as described above.
4. Run the bot:
   ```bash
   python3 main.py
   ```

## Usage Guide

### For Users (Direct Message the Bot)
- `!register_switch` — Begin the interactive onboarding flow to set your threshold and vault your emergency data.
- `!checkin` — Manually reset your activity timer.
- `!my_status` — View your current timer and registration status.
- `!update_emergency_data` — Replace your vaulted data.
- `!deregister` — Delete all your data from the bot.

### For the Group (In the Monitored Room)
- **Activity Tracking:** The bot passively monitors the room. Any message sent by a registered user resets their timer.
- `!activate_switch @username:server.org` — Cast a vote to activate a missing user's switch.
- `!cancel_alert @username:server.org` — (Admin) Cancel an active alert if the user is confirmed safe out-of-band.
- `!help` — Display the command reference.

## Testing

Run the test suite using `pytest`:
```bash
pytest tests/ -v --asyncio-mode=auto
```

## Disclaimer

This software is provided as-is. While designed with strict security and privacy constraints, it should not be relied upon as a sole life-safety mechanism. Always ensure trusted individuals have alternative means of accessing critical emergency information.
