# Security

## Threat model

| Asset | Threat | Controls |
|---|---|---|
| Gmail and Calendar data | Exfiltration through a compromised workflow or prompt injection | Read-only OAuth scopes; the LLM has no tools and no network actions; only the email being answered is sent to the LLM when drafting (no other personal data to exfiltrate) |
| Ability to send email as the user | Abuse by an attacker or by the AI | `gmail.compose` isolated in a dedicated credential used by two nodes with fixed endpoints; sending requires two clicks on a summary screen; `Reply-To` ≠ `From` warning; unreviewable (too long) replies cannot be sent |
| Telegram bot | Anyone can message a public bot | Relay drops every update not coming from the allowed chat id; the workflow re-checks the sender; button payloads are validated with strict patterns |
| Internal HTTP services (relay, LLM, STT, TTS) | Lateral calls | No published ports; constant-time shared-secret check on every endpoint; the STT container sits on an `internal: true` network with no Internet route |
| Secrets (bot token, OAuth, LLM token, encryption key) | Leak through Git, logs or screenshots | Whitelist `.gitignore`; gitleaks pre-commit hook; the bot token is never logged (it is part of the API URL); n8n encrypts stored credentials; local backups are `chmod 600` |
| n8n instance | Remote access | Bound to `127.0.0.1`; no tunnel; the public API key has minimal scopes and an expiry |
| Supply chain | Malicious or breaking image/library update | Base images pinned by `sha256` digest; n8n, Claude Code and Python libraries pinned by version |
| Discord channel | Malicious CVE text pinging everyone or breaking formatting | Mentions neutralised (zero-width space), markdown escaped, link previews suppressed |

## Prompt injection

External text (email subjects/bodies, CVE descriptions, calendar titles) is placed in a delimited
`<donnees>` / `<mail>` block, and the system prompt states that it is data and must never be
followed as instructions. This lowers the risk but does **not** remove it. The real mitigations are
architectural: the model has no tools, receives only what a given task needs, and every
outbound action goes through a human confirmation.

## Out of scope / known limitations

- A compromised Telegram account can trigger the same actions as its owner: enable Telegram two-step verification.
- The host itself is trusted. Membership of the `docker` group is root-equivalent; consider rootless Docker on a dedicated server.
- `edge-tts` uses an unofficial endpoint; podcast text sent to it is deliberately generic.

## Reporting

This is a personal project. If you spot a vulnerability, please open a GitHub issue without
sensitive details and I will get in touch.
