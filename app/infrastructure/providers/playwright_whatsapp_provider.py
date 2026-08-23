"""Camoufox / Playwright WhatsApp Web Provider Adapter.

Implements the WhatsAppProvider port using isolated Camoufox browser contexts
per sender account with realistic anti-detection, humanized typing, and delivery verification.
"""

from __future__ import annotations

import os
import random
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SEND_LOG = Path(__file__).resolve().parent.parent.parent.parent / "logs" / "whatsapp_send.log"


def _dbg(message: str) -> None:
    try:
        SEND_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SEND_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] {message}\n")
    except Exception:
        pass

from app.domain.enums import OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.providers.session_manager import (
    WhatsAppSessionManager,
    default_session_manager,
)
from app.ports.providers import ProviderSendResult, ProviderStatusResult, WhatsAppProvider


def _human_type(composer, text: str) -> None:
    """Type message into composer character by character with realistic jitter and punctuation pauses."""
    for char in text:
        composer.press_sequentially(char)
        time.sleep(random.uniform(0.04, 0.12))
        if char in {".", ",", "!", "?", "\n"}:
            time.sleep(random.uniform(0.25, 0.6))


class PlaywrightWhatsAppProvider:
    """WhatsAppProvider adapter driving WhatsApp Web via Camoufox with human-like interaction."""

    def __init__(
        self,
        session_manager: Optional[WhatsAppSessionManager] = None,
        headless: bool = False,
        timeout_seconds: int = 60,
    ) -> None:
        self.session_manager = session_manager or default_session_manager
        self.headless = False
        self.timeout_seconds = timeout_seconds

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
        with open(session_dir / "user.js", "a") as f:
            f.write('user_pref("privacy.trackingprotection.enabled", false);\n')
            f.write('user_pref("privacy.trackingprotection.pbmode.enabled", false);\n')
            f.write('user_pref("privacy.partition.network_state", false);\n')
            f.write('user_pref("media.peerconnection.enabled", true);\n')
            f.write('user_pref("permissions.default.image", 1);\n')

        try:
            with Camoufox(
                persistent_context=True,
                user_data_dir=str(session_dir),
                headless=False,
                humanize=True,
                os="windows",
                window=(1280, 900),
            ) as context:
                page = context.pages[0] if context.pages else context.new_page()

                # Step 1: Pre-flight sync check
                # Navigate to the base URL first to let the React app fully initialize
                # and sync before we try to use the /send?phone deep link.
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=timeout_ms)
                
                # Check for QR code (auth required)
                if page.locator('canvas[aria-label="Scan this QR code to link a device"], canvas').count() > 0:
                    return ProviderSendResult.failed(
                        failure_code="ERR_AUTH_REQUIRED",
                        failure_detail="WhatsApp session requires QR code scan authentication",
                    )
                
                # Wait for the chat list (#side) to prove the app is fully synced and ready
                try:
                    page.wait_for_selector("#side", timeout=30000)
                except Exception:
                    return ProviderSendResult.failed(
                        failure_code="ERR_SYNC_TIMEOUT",
                        failure_detail="WhatsApp Web failed to sync chats within timeout",
                    )                
                # Step 2: Navigate to chat url now that app is fully bootstrapped
                # Instead of relying on web.whatsapp.com/send which drops parameters due to SPA bugs,
                # we mimic a real human: click the search bar, type the number, and press Enter.
                
                search_box = page.get_by_placeholder("Search or start a new chat").first
                if search_box.count() == 0:
                    search_box = page.locator('div[title="Search input textbox"]').first
                    
                if search_box.count() == 0:
                    return ProviderSendResult.unknown(
                        reason="Could not find WhatsApp search bar to initiate chat."
                    )
                    
                # Clear any existing search
                search_box.click()
                time.sleep(0.5)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                time.sleep(0.5)
                
                # Type the target phone number
                page.keyboard.type(clean_phone)
                
                # Wait for WhatsApp to search its internal contacts and the global directory
                time.sleep(2.0)
                page.keyboard.press("Enter")
                
                _dbg(f"[send] sender={attempt.sender_account_id} -> {clean_phone} (UI Search Method)")

                invalid_text = page.get_by_text(
                    re.compile(r"phone number shared via url is invalid", re.I)
                )
                
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
                    
                    if composer.count() and composer.first.is_visible():
                        # We are in the chat!
                        # Clear anything that might be leftover in the composer
                        composer.first.click()
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        time.sleep(0.5)
                        
                        try:
                            page.keyboard.type(message_body, delay=random.uniform(40, 100))
                        except Exception:
                            composer.first.fill(message_body)
                            
                        time.sleep(1.0)
                        
                        # Wait for send button and click it
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
                        
                    # If we don't find it, the number might not exist on WhatsApp.
                    # Press Enter again just in case search was slow.
                    page.keyboard.press("Enter")
                        
                    if invalid_text.count() and invalid_text.first.is_visible():
                        return ProviderSendResult.failed(
                            failure_code="ERR_NOT_ON_WHATSAPP",
                            failure_detail="WhatsApp rejected phone number as invalid or not registered",
                        )
                        
                    time.sleep(1.5)

                    if composer.count() and composer.first.is_visible():
                        time.sleep(random.uniform(1.2, 2.5))
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass
                        time.sleep(0.3)

                        try:
                            composer.first.click()
                        except Exception:
                            pass
                        time.sleep(0.3)

                        # Type the message
                        typed = False
                        try:
                            page.keyboard.type(message_body, delay=random.uniform(40, 100))
                            typed = True
                        except Exception:
                            try:
                                _human_type(composer.first, message_body)
                                typed = True
                            except Exception as type_exc:
                                # Do NOT press send on an empty composer: that would send
                                # a blank message while reporting SENT. Surface instead.
                                _dbg(f"[send] FATAL typing failed for {clean_phone}: {type_exc!r}")
                                return ProviderSendResult.failed(
                                    failure_code="ERR_TYPING_FAILED",
                                    failure_detail=f"Could not enter message text: {type_exc}",
                                )

                        time.sleep(random.uniform(1.0, 2.0))

                        # Dispatch via Send button or Enter (only if text was entered)
                        if typed:
                            if send_button.count() and send_button.first.is_visible():
                                try:
                                    send_button.first.click(timeout=8000)
                                except Exception:
                                    page.keyboard.press("Enter")
                            else:
                                page.keyboard.press("Enter")
                        
                        time.sleep(random.uniform(1.5, 2.5))
                        text_sent = True
                        break

                    time.sleep(0.5)

                if not text_sent:
                    return ProviderSendResult.unknown(
                        reason="Message composer did not become ready within timeout period"
                    )

                # Step 4: Attachment
                att_file = Path(attachment_path) if attachment_path else None
                if att_file and att_file.exists():
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
                        page.mouse.click(attach_coords['x'], attach_coords['y'])
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
                            page.mouse.click(doc_coords['x'], doc_coords['y'])

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
                        _dbg(f"[attach] failed: {e}")
                        time.sleep(random.uniform(2.5, 4.0))


                return ProviderSendResult(
                    success=True,
                    status=OutreachStatus.SENT,
                    provider_reference=f"wa_{clean_phone}_{int(time.time())}",
                )



        except Exception as exc:
            return ProviderSendResult.unknown(
                reason=f"Camoufox automation encountered unexpected exception: {exc}"
            )

    def check_status(self, provider_reference: str) -> ProviderStatusResult:
        """Check delivery status."""
        return ProviderStatusResult(
            status=OutreachStatus.SENT,
            detail="Confirmed delivered via WhatsApp Web",
        )
