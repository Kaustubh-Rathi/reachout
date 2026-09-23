"""Camoufox / Playwright WhatsApp Web Provider Adapter.

Implements the WhatsAppProvider port using isolated Camoufox browser contexts
per sender account with realistic anti-detection, humanized typing, and delivery verification.
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.domain.enums import OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.providers.attachments import resolve_attachment_path
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.ports.providers import ProviderSendResult

logger = logging.getLogger(__name__)

SEND_LOG = Path(__file__).resolve().parent.parent.parent.parent / "logs" / "whatsapp_send.log"


def _dbg(message: str) -> None:
    try:
        SEND_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SEND_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] {message}\n")
    except OSError:
        logger.debug("Unable to write WhatsApp send log at %s", SEND_LOG, exc_info=True)


class PlaywrightWhatsAppProvider:
    """WhatsAppProvider adapter driving WhatsApp Web via Camoufox with human-like interaction."""

    def __init__(
        self,
        session_manager: WhatsAppSessionManager,
        headless: bool = False,
        timeout_seconds: int = 60,
        expected_identity: Optional[str] = None,
    ) -> None:
        self.session_manager = session_manager
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self.expected_identity = expected_identity

    def send_message(
        self,
        attempt: OutreachAttempt,
        recipient_phone: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        """Dispatch a single WhatsApp text and optional attachment."""
        if not recipient_phone:
            return ProviderSendResult.failed(
                failure_code="ERR_INVALID_PHONE",
                failure_detail="Recipient phone number is empty",
            )

        clean_phone = re.sub(r"\D", "", recipient_phone)
        if not (10 <= len(clean_phone) <= 15):
            return ProviderSendResult.failed(
                failure_code="ERR_INVALID_PHONE_FORMAT",
                failure_detail=f"Phone number '{recipient_phone}' contains invalid digit count ({len(clean_phone)})",
            )

        # Validate the attachment up front so we never send text-only while
        # reporting a full success (the resume must not be silently dropped).
        att_file = resolve_attachment_path(attachment_path)
        if attachment_path and (att_file is None or not att_file.exists()):
            return ProviderSendResult.failed(
                failure_code="ERR_ATTACHMENT_NOT_FOUND",
                failure_detail=f"Attachment not found: {attachment_path}",
            )

        try:
            from camoufox.sync_api import Camoufox
        except ImportError:
            return ProviderSendResult.failed(
                failure_code="ERR_PLAYWRIGHT_NOT_INSTALLED",
                failure_detail="Camoufox is not installed in the environment",
            )

        session_dir = self.session_manager.get_session_dir(attempt.sender_account_id)
        timeout_ms = self.timeout_seconds * 1000

        session_dir.mkdir(parents=True, exist_ok=True)
        # Overwrite (not append): append mode accumulated hundreds of duplicate
        # prefs across runs and risks Playwright clobbering on launch.
        with open(session_dir / "user.js", "w") as f:
            f.write('user_pref("privacy.trackingprotection.enabled", false);\n')
            f.write('user_pref("privacy.trackingprotection.pbmode.enabled", false);\n')
            f.write('user_pref("privacy.partition.network_state", false);\n')
            f.write('user_pref("media.peerconnection.enabled", true);\n')
            f.write('user_pref("permissions.default.image", 1);\n')

        try:
            with Camoufox(
                persistent_context=True,
                user_data_dir=str(session_dir),
                headless=self.headless,
                humanize=True,
                os="windows",
                window=(1280, 900),
            ) as context:
                page = context.pages[0] if context.pages else context.new_page()

                # Step 1: Pre-flight sync check
                # Navigate to the base URL first to let the React app fully initialize
                # and sync before we try to use the /send?phone deep link.
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=timeout_ms)

                # The QR canvas renders several seconds after page load, so an
                # instant check races it: a logged-out session would slip past
                # and later misreport ERR_SYNC_TIMEOUT instead of
                # ERR_AUTH_REQUIRED (which the sender downgrade logic keys off).
                # Settle first: either #side (synced) or a canvas (logged out).
                qr_locator = page.locator('canvas[aria-label="Scan this QR code to link a device"], canvas')
                settle_deadline = time.monotonic() + min(20.0, self.timeout_seconds / 3)
                saw_qr = False
                while time.monotonic() < settle_deadline:
                    if page.locator("#side").count() > 0:
                        break
                    if qr_locator.count() > 0:
                        saw_qr = True
                        break
                    time.sleep(1.0)

                # Check for QR code (auth required)
                if saw_qr or qr_locator.count() > 0:
                    return ProviderSendResult.failed(
                        failure_code="ERR_AUTH_REQUIRED",
                        failure_detail="WhatsApp session requires QR code scan authentication",
                    )

                # Wait for the chat list (#side) to prove the app is fully synced and ready.
                # Large histories can take over a minute to download ("Loading your
                # chats"), so honor the configured timeout instead of a fixed 30s.
                try:
                    page.wait_for_selector("#side", timeout=timeout_ms)
                except Exception:
                    # Re-check QR last: a session that expired mid-sync shows the
                    # login screen instead of the chat list.
                    if qr_locator.count() > 0:
                        return ProviderSendResult.failed(
                            failure_code="ERR_AUTH_REQUIRED",
                            failure_detail="WhatsApp session requires QR code scan authentication",
                        )
                    return ProviderSendResult.failed(
                        failure_code="ERR_SYNC_TIMEOUT",
                        failure_detail="WhatsApp Web failed to sync chats within timeout",
                    )
                if self.expected_identity is not None:
                    actual_identity = self.session_manager.extract_phone(page)
                    if actual_identity != re.sub(r"\D", "", self.expected_identity):
                        return ProviderSendResult.failed(
                            failure_code="ERR_SENDER_IDENTITY_MISMATCH",
                            failure_detail="Browser account does not match the selected sender; nothing sent",
                        )
                # Step 2: Navigate to chat url now that app is fully bootstrapped
                # The UI Search method fails for unsaved numbers. We use JS location assignment
                # to trigger the SPA deep link directly.
                _dbg(f"[send] sender={attempt.sender_account_id} -> {clean_phone} (JS Deep Link Method)")
                page.evaluate(f'window.location.href = "https://web.whatsapp.com/send/?phone={clean_phone}";')

                invalid_text = page.get_by_text(re.compile(r"phone number shared via url is invalid", re.I))

                # Step 3: Robust Selectors for 2025/2026
                composer = page.locator(
                    'footer div[contenteditable="true"], [data-testid="conversation-compose-box-input"], div[title="Type a message"]'
                )
                send_button = page.locator(
                    '[data-testid="send"], footer button[aria-label="Send"], footer button:has(span[data-icon="send"])'
                )

                # Wait for composer to become interactable
                deadline = time.monotonic() + self.timeout_seconds
                text_sent = False

                while time.monotonic() < deadline:
                    if invalid_text.count() and invalid_text.first.is_visible():
                        return ProviderSendResult.failed(
                            failure_code="ERR_NOT_ON_WHATSAPP",
                            failure_detail="WhatsApp rejected phone number as invalid or not registered",
                        )

                    if composer.count() and composer.first.is_visible():
                        # We are in the chat!
                        composer.first.click(force=True)
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        time.sleep(0.5)

                        try:
                            page.keyboard.type(message_body, delay=random.uniform(40, 100))
                        except Exception:
                            composer.first.fill(message_body)

                        time.sleep(1.0)

                        if send_button.count() and send_button.first.is_visible():
                            try:
                                send_button.first.click()
                            except Exception:
                                page.keyboard.press("Enter")
                        else:
                            page.keyboard.press("Enter")

                        time.sleep(random.uniform(1.5, 2.5))
                        text_sent = True
                        break

                    time.sleep(1.0)

                if not text_sent:
                    return ProviderSendResult.unknown(
                        reason="Message composer did not become ready within timeout period"
                    )

                # Step 4: Attachment (validated as existing before any text was sent)
                if att_file is not None:
                    try:
                        time.sleep(random.uniform(0.8, 1.5))

                        # --- Find & click the attach (plus-rounded) button via JS coords ---
                        attach_coords = page.evaluate("""() => {
                            let el = document.querySelector('[title="Attach"]');
                            if (!el) {
                                for (let ic of ['plus-rounded', 'plus', 'clip']) {
                                    let svg = document.querySelector('span[data-icon="' + ic + '"]');
                                    if (svg) { el = svg.closest('button, span[role="button"], div[role="button"]'); if (el) break; }
                                }
                            }
                            if (!el) el = document.querySelector('[aria-label="Attach"]');
                            if (el) {
                                let r = el.getBoundingClientRect();
                                return {x: r.left + r.width/2, y: r.top + r.height/2};
                            }
                            return null;
                        }""")

                        if not attach_coords:
                            raise Exception("Could not find Attach button in the footer")

                        _dbg(f"[attach] clicking attach btn at {attach_coords}")
                        page.mouse.click(attach_coords["x"], attach_coords["y"])
                        time.sleep(1.5)

                        # --- Find Document menu item by role=menuitem (strict, avoids chat bubbles) ---
                        doc_coords = page.evaluate("""() => {
                            let items = document.querySelectorAll('[role="menuitem"]');
                            for (let item of items) {
                                let txt = (item.innerText || item.textContent || '').trim();
                                if (txt === 'Document') {
                                    let r = item.getBoundingClientRect();
                                    if (r.width > 0 && r.height > 0) {
                                        return {x: r.left + r.width/2, y: r.top + r.height/2};
                                    }
                                }
                            }
                            return null;
                        }""")

                        if not doc_coords:
                            raise Exception("Could not find Document menuitem after clicking attach")

                        _dbg(f"[attach] clicking Document menuitem at {doc_coords}")

                        # Click Document and catch the native file chooser
                        with page.expect_file_chooser(timeout=7000) as fc_info:
                            page.mouse.click(doc_coords["x"], doc_coords["y"])

                        fc = fc_info.value
                        fc.set_files(str(att_file))
                        _dbg(f"[attach] file set: {att_file}")
                        time.sleep(1.5)

                        # --- Wait for and click the dedicated send button in the attachment modal ---
                        send_doc_btn = page.locator(
                            'div[role="button"][aria-label^="Send"], '
                            'div[role="button"][aria-label="Send"], '
                            'span[data-icon="wds-ic-send-filled"], '
                            '[data-testid="send"], '
                            'span[data-icon="send"]'
                        ).last
                        send_doc_btn.wait_for(state="visible", timeout=15000)
                        _dbg("[attach] send button visible, clicking...")
                        send_doc_btn.click()

                        # --- Wait for send button to disappear (UI transition) ---
                        try:
                            send_doc_btn.wait_for(state="hidden", timeout=30000)
                            _dbg("[attach] send button gone (UI).")
                        except Exception:
                            _dbg("[attach] send button did not hide within 30s.")

                        # CRITICAL: The send button disappears immediately on click (UI transition),
                        # but the actual file upload POST to media-hyd1-1.cdn.whatsapp.net fires
                        # ~15-20 seconds later asynchronously. We MUST keep the browser alive.
                        # Network debug confirmed: click at T+0, CDN POST at T+17s.
                        _dbg("[attach] waiting 25s for async CDN upload to complete...")
                        time.sleep(25.0)
                        _dbg("[attach] upload wait done.")

                    except Exception as e:
                        # The text was already sent, so this is a partial side effect:
                        # surface it for operator recovery instead of reporting success.
                        _dbg(f"[attach] failed: {e}")
                        return ProviderSendResult.recovery_required(f"Text delivered but attachment upload failed: {e}")

                return ProviderSendResult(
                    success=True,
                    status=OutreachStatus.SENT,
                    provider_reference=f"wa_{clean_phone}_{int(time.time())}",
                )

        except Exception as exc:
            return ProviderSendResult.unknown(reason=f"Camoufox automation encountered unexpected exception: {exc}")
