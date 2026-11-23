# Shot list

Total runtime 2:51.5. 1920x1080, 30 fps, captions burned in.

| # | Start | Length | On screen | Source |
|---|---|---|---|---|
| 1 | 0:00.0 | 16.5 s | **Hook.** Animated title card: recruiting outreach fails in boring, expensive ways; Reachout is the self-hosted fix. | video/pages/hook.html |
| 2 | 0:16.5 | 22.4 s | **Setup.** Terminal: Python 3.10+, uv sync, one uvicorn command on port 8000, the SQLite database on first start, then the recorded backend test run. | video/pages/terminal.html, video/proofs.mjs (captured output) |
| 3 | 0:38.9 | 53.7 s | **Working demo.** The live dashboard on the seeded demo database: overview counts, the campaign deck, companies and contacts, endpoints with their own state, the send form, templates, and the failure history. | npm run demo, then the dashboard at 127.0.0.1:8000 (video/scenes/app.mjs drives it) |
| 4 | 1:32.6 | 23.5 s | **A real send.** Two real sends from the owner's machine to a second account. Camoufox opens WhatsApp Web and types the rendered message; the receiving phone shows it arrive. Email goes the same way: SMTP from the vault, then the body in the receiving inbox. PII boxes are masked. | scripts/capture_whatsapp.py, scripts/capture_live.py -> out/raw/*.mp4, composited by video/scenes/live.mjs |
| 5 | 1:56.0 | 26.3 s | **How it works.** Code tour, syntax-highlighted excerpts read from the repo: bootstrap, the company-first prioritization policy, the rate limiter, the credential vault, and the attempt state machine with recovery. | app/main.py, app/domain/policies/prioritization.py, app/infrastructure/scheduler/rate_limiter.py, app/infrastructure/security/credential_vault.py, app/domain/outreach_attempt.py |
| 6 | 2:22.4 | 17.9 s | **Architecture and honesty.** Architecture diagram (FastAPI + SQLite, Camoufox for WhatsApp Web, SMTP for email, all on one machine), then the real-versus-simulated table from the README. | video/pages/arch.html |
| 7 | 2:40.3 | 11.2 s | **Close.** Closing card: two real channels, company-first pacing, a record of every attempt, and the repository link. | video/pages/close.html |
