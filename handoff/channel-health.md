# Outreach Channel Health Check

- **Date/time:** 2026-11-22 ~18:50–18:57 UTC
- **Repo:** `D:/Reachout` (read-only)
- **App:** `http://127.0.0.1:8000` against `D:/Reachout/data/demo.db`
- **Mode:** Verification only. **No message or email was sent.** No `send-*` / `resend-*` endpoint was called. No files in `app/`, `ui/`, `.env`, or the database were edited by hand.

Masking: phone numbers `9174****82` / `9178****06`; addresses `1.0****@gmail.com`.

---

## TL;DR

**Yes.** Both channels can send right now **without the owner re-authenticating anything**.

- Both WhatsApp senders' stored sessions are **still logged in** (live browser probe returned `ACTIVE`).
- Both email senders' SMTP credentials **connect, STARTTLS, and authenticate** successfully.
- No sender is reported ready by the app but actually dead.

---

## Readiness snapshot — `GET /api/senders/readiness`

| Channel | active_count | total_count | ready | overall_ready |
|---|---|---|---|---|
| WhatsApp | 2 | 2 | true | true |
| Email | 2 | 2 | true | true |

`GET /api/senders` confirms all four rows `status = ACTIVE`.

---

## Per-sender results

### WhatsApp

Probe: `POST /api/senders/whatsapp/{sender_id}/auth/check?timeout_seconds=60`
(route `app/api/senders.py:104`, handler `WhatsAppAuthService.check_whatsapp_session_health` → `WhatsAppSessionManager.check_session_status`). The handler opens the **persisted profile** in a headed Camoufox browser, loads `web.whatsapp.com`, and reports whether the chat list (logged in) or the QR canvas (logged out) is shown. It does **not** dispatch anything.

| Sender id (masked) | Probe result | Stored status after | status_changed | Wall-clock time |
|---|---|---|---|---|
| `wa_9174****82` | `ACTIVE` | `ACTIVE` | false | **120,883 ms (~121 s)** |
| `wa_9178****06` | `ACTIVE` | `ACTIVE` | false | **108,048 ms (~108 s)** |

Both sessions are authenticated. Neither needs a QR re-scan.

### Email

Test: `POST /api/senders/email/{sender_id}/verify` (route `app/api/senders.py:136`, handler `EmailSenderService.verify_email_sender` → `SmtpEmailProvider.verify_credentials`).

| Sender id | verified | Status after | Error | Wall-clock time |
|---|---|---|---|---|
| `EMAIL_1` | true | `ACTIVE` | none | 3,830 ms |
| `EMAIL_2` | true | `ACTIVE` | none | 3,313 ms |

Both SMTP credentials connect and authenticate successfully.

---

## Proof the email verify path cannot send

`app/infrastructure/providers/smtp_email_provider.py`, `verify_credentials` (lines 85–116) — the only network actions are connect / STARTTLS / login; there is **no `send_message` call**:

```
100:            context = ssl.create_default_context()
101:            if port == 465:
102:                # Implicit TLS (SMTPS) — no STARTTLS handshake.
103:                with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as server:
104:                    server.login(user, pwd)
105:            else:
106:                # Explicit TLS via STARTTLS (e.g. 587).
107:                with smtplib.SMTP(host, port, timeout=15) as server:
108:                    server.ehlo()
109:                    server.starttls(context=context)
110:                    server.ehlo()
111:                    server.login(user, pwd)
112:            return True, None
```

`send_message` appears only in the separate `send_email` method (lines 188 and 195), which this test never calls.

The WhatsApp `auth/check` path (`session_manager.py:144–173`) likewise only opens the page and reads the DOM; the only dispatch path (`playwright_whatsapp_provider.py`) is elsewhere and was not invoked.

---

## Senders the app reports ready but that are actually dead

**None.** All four ACTIVE senders passed a real live probe (browser login check for WhatsApp, SMTP login for email).

---

## Can a real send work right now?

**Yes — both channels, with no owner action required.**

- **WhatsApp `wa_*A*` → `wa_*B*`:** the sending session `wa_9174****82` is live-logged-in (`probe=ACTIVE`), and the destination number `wa_9178****06` is a valid WhatsApp number. The send path (`playwright_whatsapp_provider.send_message`) uses the same persisted profile and gates on `ERR_AUTH_REQUIRED` only when the WhatsApp Web login screen is shown — which it is not. Expected to send.
- **Email `EMAIL_1` → `EMAIL_2`:** both accounts authenticate over SMTP (`verified: true`) and the recipients are valid addresses. Expected to send.

**Owner action needed:** none. No QR re-scan, no password re-entry.

### Caveat / operational note for the recording

Each WhatsApp session check is what the recording will have to wait for, and it is **slow**: measured **~121 s** and **~108 s** wall-clock per sender (browser launch + page load + teardown dominate; both far exceeded the 60 s probe timeout even while authenticated). Budget ~2 minutes per WhatsApp sender probe.

---

## Side effects observed (all attributable to the sanctioned endpoints)

- `auth/check` rewrote `D:/Reachout/.sessions/whatsapp/<sender_id>/user.js` and opened/closed the persistent browser profile. This is the endpoint's normal behaviour, which the task explicitly deemed acceptable and non-destructive. **No stored sender status changed** (`status_changed: false` for both).
- `email/{id}/verify` succeeded, so it left the rows `ACTIVE` (no status change). Note for future runs: on *failure* this endpoint sets the sender to `ERROR` and commits — so it is not strictly read-only. It caused no failure here.
