"""Fixtures and validation gates for Live Provider E2E tests.

Strict rules:
1. Tests must NOT run during standard automated test runs (uv run pytest).
2. Live E2E tests require LIVE_E2E=1 or specific flags (LIVE_WHATSAPP_E2E=1, LIVE_EMAIL_E2E=1).
3. Missing credentials result in explicit SKIP, never synthetic fake PASS.
4. Production provider classes (PlaywrightWhatsAppProvider, SmtpEmailProvider) are used exclusively.
"""

from __future__ import annotations

import os

import pytest

from app.composition import get_credential_vault, get_session_manager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from tests.e2e.live.live_accounts import LiveAccountError, discover_accounts, normalize_identity, select_pair


@pytest.fixture(scope="session", autouse=True)
def run_test_server():
    yield None


def resolve_pair(channel, ready):
    if os.environ.get("LIVE_E2E") != "1" and os.environ.get(f"LIVE_{channel}_E2E") != "1":
        pytest.skip(f"LIVE {channel}: not opted in")
    try:
        accounts = discover_accounts(os.environ["REACHOUT_LIVE_DATABASE_URL"], channel)
        pair = select_pair(accounts, channel, ready)
    except LiveAccountError as exc:
        pytest.fail(str(exc))
    if pair is None:
        pytest.skip(f"LIVE {channel}: no ready registered account; log in through the app first")
    print(f"LIVE {channel}: {pair.sender.id} ({pair.sender.identity}) -> {pair.recipient}")
    return pair


@pytest.fixture
def live_whatsapp_pair():
    def ready(account):
        from camoufox.sync_api import Camoufox

        manager = get_session_manager()
        if not manager.has_persisted_session(account.id):
            return False
        with Camoufox(
            persistent_context=True,
            user_data_dir=str(manager.session_dir_path(account.id)),
            headless=False,
            humanize=False,
            os="windows",
            window=(1280, 900),
        ) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=180000)
            try:
                page.locator('#side, [data-testid="chat-list"]').first.wait_for(state="visible", timeout=180000)
            except Exception:
                return False
            actual = manager.extract_phone(page)
            if actual != account.identity:
                raise LiveAccountError(f"Browser identity mismatch for {account.id}; no message sent")
            return True

    return resolve_pair("WHATSAPP", ready)


@pytest.fixture
def live_email_pair():
    provider = SmtpEmailProvider(credential_vault=get_credential_vault())

    def ready(account):
        credentials = provider.get_sender_credentials(account.id)
        if not credentials.get("user") or not credentials.get("password"):
            return False
        if normalize_identity("EMAIL", credentials["user"]) != account.identity:
            raise LiveAccountError(f"SMTP identity mismatch for {account.id}")
        if normalize_identity("EMAIL", credentials.get("from_address") or credentials["user"]) != account.identity:
            raise LiveAccountError(f"SMTP from-address mismatch for {account.id}")
        verified, _ = provider.verify_credentials(account.id)
        return verified

    return resolve_pair("EMAIL", ready), provider


@pytest.fixture(autouse=True)
def live_e2e_guard():
    """Ensure tests in this directory only execute when explicitly opted in."""
    is_live_all = os.environ.get("LIVE_E2E") == "1"
    is_live_wa = os.environ.get("LIVE_WHATSAPP_E2E") == "1"
    is_live_em = os.environ.get("LIVE_EMAIL_E2E") == "1"

    if not (is_live_all or is_live_wa or is_live_em):
        pytest.skip(
            "LIVE PROVIDER E2E NOT RUN: To run live provider tests, set LIVE_E2E=1 or "
            "LIVE_WHATSAPP_E2E=1 / LIVE_EMAIL_E2E=1 with legitimate external credentials."
        )
