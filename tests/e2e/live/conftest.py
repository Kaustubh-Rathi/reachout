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
