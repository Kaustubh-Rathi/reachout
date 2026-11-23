# Narration script

Total runtime 2:51.5. Times are where each line starts in the final video. The burned-in captions match this text exactly, and reachout-demo.srt carries the same cues for players that render their own.

To use your own voice, record each line and drop it in at its timestamp (or replace the audio track with one read straight through at the same pace). The captions-only cut needs no voice.

| Time | Scene | Line |
|---|---|---|
| 0:00.6 | Hook | Recruiting outreach fails in boring, expensive ways: duplicate messages, dead endpoints, and no record of what was sent. |
| 0:08.4 | Hook | Reachout is a self-hosted recruiting CRM that runs that work across WhatsApp and email, with the audit trail to prove it. |
| 0:17.2 | Setup | Setup is three steps. Python 3.10 or newer, uv sync for the backend, then one uvicorn command on port 8000. |
| 0:25.5 | Setup | The first start creates the SQLite database. The same FastAPI process serves the API and the dashboard, with no build step. |
| 0:33.0 | Setup | The repository ships unit, integration and browser tests. This is the recorded backend run. |
| 0:39.2 | Working demo | This is the seeded demo database: twenty-five contacts, fifty endpoints, forty attempts, all synthetic. |
| 0:47.2 | Working demo | Overview answers who still needs a message, what failed, and what is due for follow-up. |
| 0:52.8 | Working demo | The campaign deck shows the active run, its target, and attempt counters for sent, failed and unresolved. |
| 0:59.4 | Working demo | Contacts are grouped by company. The dispatcher reaches one person per company before it returns for a second. |
| 1:06.4 | Working demo | Every phone number and email is a separate endpoint with its own state, so one dead address never hides the rest. |
| 1:13.1 | Working demo | Open send, and the endpoint, sender and rendered template are all chosen before anything leaves the machine. |
| 1:19.7 | Working demo | Templates keep each message body in one place, with variables that fill in per contact. |
| 1:25.5 | Working demo | Failures stay visible, with masked destinations, the failure reason, and the attempt history. |
| 1:33.2 | A real send | A real send now, from this machine to a second account, so the whole path is visible. |
| 1:37.8 | A real send | Camoufox opens WhatsApp Web, finds the chat, and types the rendered message. |
| 1:42.2 | A real send | It leaves under the same spacing, retry and audit rules as any campaign send. |
| 1:46.6 | A real send | Email takes the same path: credentials from the vault, then the body in the inbox. |
| 1:51.2 | A real send | Both channels here are real. Every contact in the demo database is synthetic. |
| 1:56.6 | How it works | The codebase is hexagonal Python: domain rules in the middle, FastAPI and providers at the edges. |
| 2:04.0 | How it works | The prioritization policy interleaves companies. The rate limiter enforces channel spacing. The vault encrypts SMTP secrets. |
| 2:13.8 | How it works | Dispatch moves through prepared, sending, sent or failed, so a crash leaves a recovery record instead of a silent retry. |
| 2:23.1 | Architecture and honesty | FastAPI and SQLite, Camoufox for WhatsApp Web, SMTP for email. Everything runs on one machine. |
| 2:31.5 | Architecture and honesty | Real versus simulated: the application, dispatch pipeline and tests are real. The companies and contacts are synthetic. |
| 2:41.0 | Close | Reachout: two real channels, company-first pacing, and a record of every attempt. |
| 2:46.2 | Close | Two real sends, to the owner's own accounts. The repository link is below. |
