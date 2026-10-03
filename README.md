# secure-n8n-assistant

A self-hosted personal assistant built on [n8n](https://n8n.io) and Telegram, designed **security-first**:
cyber threat intelligence, a daily briefing (text and two-voice podcast), email triage with
human-validated replies, and a conversational bot that understands voice messages.

Everything runs locally in hardened Docker containers. **No port is exposed to the Internet**,
Google access is **read-only** (plus a single, isolated permission for drafts), the AI **has no tools**,
and nothing is ever sent on your behalf without your explicit double confirmation.

> Personal project, built to learn automation and secure system design.
> Workflows, prompts and comments are written in French.

## Features

| | Feature | Details |
|---|---|---|
| 🛡️ | **Threat intelligence watch** | CISA KEV (actively exploited), CERT-FR alerts, critical NVD CVEs. Deduplicated, only new items, sent to Telegram and Discord. |
| ☀️ | **Daily report** | Today's and tomorrow's agenda (all Google calendars), unread school emails, unread important emails across several Gmail accounts. |
| 🎙️ | **Morning podcast** | Two-voice audio briefing (day + cyber news) written by the AI, delivered as a real Telegram voice message. |
| 🤖 | **Conversational bot** | Free text **or voice** questions, short-term memory, answers grounded in your real data (agenda, emails, system status, threat watch). |
| 📧 | **Email assistant** | New important email → notification with buttons → AI-drafted reply → edit by text or voice → Gmail draft **or** send after double confirmation. |
| 🩺 | **Remote supervision** | `/statut`, `/erreurs`, `/pause`, `/reprendre` from Telegram; failure alerts; nightly backups. |

## Architecture

```
                 Telegram ◄──────────── long polling (no inbound port)
                    ▲  │
                    │  ▼
 ┌──────────────── relais-telegram ─────────────────────────────────────┐
 │  only forwards messages from ONE allowed chat id; sends voice notes  │
 │  and inline buttons to that chat only (fixed recipient)               │
 └──────────────────────────┬───────────────────────────────────────────┘
                            │ shared secret
                            ▼
  Gmail / Calendar ◄──── n8n (127.0.0.1 only) ────► CISA KEV · CERT-FR · NVD · Discord webhook
  (read-only OAuth;        │   │   │
   drafts scope isolated)  │   │   └──► studio  : TTS (edge-tts, local Piper fallback) → OGG/Opus
                           │   └──────► voix    : local Whisper speech-to-text, NO Internet access
                           └──────────► cerveau : Claude Code in print mode, ALL TOOLS DISABLED
```

| Container | Role | Network |
|---|---|---|
| `n8n` | Orchestration (8 workflows) | `127.0.0.1:5678` only |
| `relais-telegram` | Telegram long polling, voice notes, inline buttons | internal Docker network |
| `cerveau` | LLM text generation (Claude Code, `--tools ""`) | internal Docker network |
| `voix` | Speech-to-text (faster-whisper, CPU) | **internal network with no Internet** |
| `studio` | Text-to-speech and audio mixing (edge-tts, Piper, ffmpeg) | internal Docker network |

## Security design

The full threat model is in [SECURITY.md](SECURITY.md). Highlights:

- **No exposed surface.** n8n listens on localhost only. Telegram is reached by *outbound* long polling, so there are no public webhooks and no tunnel.
- **Least privilege everywhere.**
  - Google: `gmail.readonly` + `calendar.readonly`. `gmail.compose` lives in a *separate* credential used by exactly two nodes with hard-coded endpoints (`POST /drafts`, `POST /drafts/send`).
  - The n8n API key used for supervision has 6 custom scopes and an expiry date.
- **The AI cannot act.** The LLM container runs with every tool disabled: it receives text and returns text. External data (email subjects and bodies, CVE descriptions) is wrapped in a `<donnees>` block and explicitly marked as *data, never instructions* (prompt-injection mitigation).
- **Human in the loop.** Sending an email requires two clicks on a summary screen, a warning is shown when `Reply-To` differs from `From`, and replies too long to be fully reviewed cannot be sent directly.
- **Defense in depth on inputs.** The sender is checked by the relay *and* by the workflow, webhook calls need a shared secret, button payloads are strictly validated, and Discord output is neutralised (no `@everyone`, escaped markdown).
- **Hardened containers.** Non-root users, read-only filesystems, `cap_drop: ALL`, `no-new-privileges`, memory/PID limits, images pinned by **sha256 digest**, dependencies pinned by version.
- **Secrets hygiene.** Whitelist `.gitignore`, **gitleaks** pre-commit hook (Docker image pinned by digest), credentials stored encrypted by n8n, backups split between Git (no secrets) and local encrypted exports.
- **Privacy by design.** The podcast only receives *generic* information about your day (counts and times, never titles), so nothing personal reaches the cloud TTS. Digits are masked in email subjects shown on Telegram.

## Requirements

- Linux host with Docker and Docker Compose
- A Telegram bot (via @BotFather) and your chat id
- A Google Cloud project (free) with the Gmail and Calendar APIs enabled and an OAuth client
- A Claude subscription and a long-lived token (`claude setup-token`) for the `cerveau` container
- Optional: a Discord channel webhook

## Setup

```sh
git clone https://github.com/0xH4shDumb/secure-n8n-assistant.git && cd secure-n8n-assistant
git config core.hooksPath hooks

# 1. Secrets (never commit them): copy each example and fill it in
for f in .env relais.env cerveau.env voix.env studio.env; do (umask 077 && cp "$f.example" "$f"); done

# 2. Start the stack
docker compose up -d --build

# 3. In n8n (http://localhost:5678): create the credentials listed below, then import the workflows
docker cp workflows n8n:/tmp/wf && docker exec n8n n8n import:workflow --separate --input=/tmp/wf
```

Then replace the placeholders in the workflows:

| Placeholder | Replace with |
|---|---|
| `YOUR_TELEGRAM_CHAT_ID` | your Telegram chat id |
| `school.example` | the e-mail domain of your school or company (emails always shown and replied to as copy-paste text) |

Credentials to create in n8n: one Telegram API, one **Google OAuth2 API** per Gmail account with scope
`https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar.readonly`, one
**Google OAuth2 API** per account with scope `https://www.googleapis.com/auth/gmail.compose` (drafts),
four **Header Auth** credentials (`X-Relais-Secret`, `X-Cerveau-Secret`, `X-Voix-Secret`, `X-Studio-Secret`)
matching your `.env` files, an **n8n API** key with minimal scopes, and optionally a **Discord Webhook**.

Finally publish the workflows and enable the nightly backup:

```sh
for id in $(docker exec n8n n8n list:workflow | cut -d'|' -f1); do docker exec n8n n8n publish:workflow --id="$id"; done
docker compose restart n8n
mkdir -p ~/.config/systemd/user && cp systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now assistant-sauvegarde.timer
```

## Lessons learned

- In an n8n Code node, `$execution.mode` is `"production"` for scheduled runs, **not** `"trigger"`. Use `$('Schedule node').isExecuted` to detect a scheduled run, and always test a "once a day" lock on a *real* scheduled trigger.
- `faster-whisper 1.2.1` breaks with PyAV ≥ 18 (`metadata_errors` removed): pin `av==17.1.0`.
- A plain-text Gmail draft gets hard-wrapped at ~76 characters by Gmail's editor: send `multipart/alternative` (text + HTML).
- Gmail has no "drafts only" scope (`gmail.compose` can also send): isolation has to come from the architecture.

## Disclaimer

This is a personal learning project, provided as is. `edge-tts` relies on an unofficial Microsoft endpoint
(the local Piper voice is used automatically when it fails). Check the terms of service of every
third-party service you connect, including your AI provider's terms for automated use.

## License

[MIT](LICENSE)
