"""Shared fixtures for E2E tests."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
import uvicorn
from camoufox.sync_api import Camoufox

from app.domain.enums import Channel, SenderStatus
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory, init_db
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.main import app
from app.services.sync_service import SyncService

TEST_HOST = "127.0.0.1"
TEST_PORT = 8899
BASE_URL = f"http://{TEST_HOST}:{TEST_PORT}"


@pytest.fixture(scope="session")
def e2e_server_storage(tmp_path_factory):
    """Give the non-live E2E server disposable credential/session storage.

    The server starts before per-test isolation fixtures run. Without this,
    startup reconciliation would read production credentials/sessions while
    using the isolated test database. Live provider tests use their own
    no-server fixture and are unaffected.
    """
    from app.composition import get_credential_vault, get_session_manager
    from app.infrastructure.providers import session_store as session_store_module

    credential_vault = get_credential_vault()
    session_manager = get_session_manager()

    storage_root = tmp_path_factory.mktemp("e2e-server-storage")
    sessions_root = storage_root / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)

    old_vault_path = credential_vault.vault_path
    old_key_path = credential_vault.key_path
    old_cache = credential_vault._cache
    old_default_sessions_root = session_store_module.DEFAULT_SESSIONS_ROOT
    old_manager_sessions_root = session_manager.sessions_root
    old_auth_state = session_manager._auth_state

    credential_vault.vault_path = storage_root / "smtp_vault.enc"
    credential_vault.key_path = storage_root / "vault_key"
    credential_vault._cache = {}
    session_store_module.DEFAULT_SESSIONS_ROOT = sessions_root
    session_manager.sessions_root = sessions_root
    session_manager._auth_state = {}

    try:
        yield storage_root
    finally:
        credential_vault.vault_path = old_vault_path
        credential_vault.key_path = old_key_path
        credential_vault._cache = old_cache
        session_store_module.DEFAULT_SESSIONS_ROOT = old_default_sessions_root
        session_manager.sessions_root = old_manager_sessions_root
        session_manager._auth_state = old_auth_state


@pytest.fixture(scope="session", autouse=True)
def run_test_server(e2e_server_storage):
    """Launch local ASGI test server in a background daemon thread."""
    init_db()

    # Seed initial test data
    with SessionFactory() as session:
        sync_svc = SyncService(session)
        try:
            sync_svc.sync_source()
        except Exception:
            pass

        # Seed display-only senders so sender UI lists accounts without credentials.
        # AUTH_REQUIRED rows survive startup reconciliation and cannot dispatch.
        sender_repo = SqliteSenderRepository(session)
        if sender_repo.get_by_id("WA_E2E_SMOKE") is None:
            wa_sender = SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="playwright_whatsapp",
                identity="100000000001",
                display_name="E2E Smoke WhatsApp Sender",
                sender_id="WA_E2E_SMOKE",
            )
            wa_sender.mark_status(SenderStatus.AUTH_REQUIRED)
            sender_repo.save(wa_sender)
        if sender_repo.get_by_id("EMAIL_E2E_SMOKE") is None:
            email_sender = SenderAccount.create(
                channel=Channel.EMAIL,
                provider="smtp",
                identity="e2e-smoke@example.invalid",
                display_name="E2E Smoke Email Sender",
                sender_id="EMAIL_E2E_SMOKE",
            )
            email_sender.mark_status(SenderStatus.AUTH_REQUIRED)
            sender_repo.save(email_sender)
        session.commit()

    config = uvicorn.Config(app, host=TEST_HOST, port=TEST_PORT, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to become responsive
    import urllib.request

    max_wait = 15
    start_t = time.time()
    while time.time() - start_t < max_wait:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/crm/kpis", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.2)

    yield server
    server.should_exit = True


@pytest.fixture(scope="function")
def browser_page():
    """Yield a fresh headless Camoufox (Firefox) page per test.

    Camoufox is the browser engine the application itself uses, so the E2E
    control-plane tests run against the same engine as production instead of
    requiring a separately installed Playwright Chromium.
    """
    with Camoufox(headless=True, window=(1440, 900)) as browser:
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            reduced_motion="reduce",
            # The app ships a strict CSP; bypass it so Playwright's injected
            # selector/eval scripts are not blocked in Firefox.
            bypass_csp=True,
        )
        # Camoufox (Firefox) is slower to first paint than Chromium; give UI
        # selectors a generous default so tests are not timing-flaky.
        context.set_default_timeout(20000)
        context.set_default_navigation_timeout(45000)
        # Disable CSS animations/transitions so Playwright's actionability
        # "stable" check does not time out on pulsing/transitioning elements.
        context.add_init_script(
            """
            const __noAnim = () => {
              const style = document.createElement('style');
              style.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
              (document.head || document.documentElement).appendChild(style);
            };
            if (document.readyState === 'loading') {
              document.addEventListener('DOMContentLoaded', __noAnim);
            } else {
              __noAnim();
            }
            """
        )
        page = context.new_page()
        yield page
        browser.close()
