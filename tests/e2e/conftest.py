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
from playwright.sync_api import sync_playwright

from app.infrastructure.database import SessionFactory, init_db
from app.main import app
from app.services.sync_service import SyncService

TEST_HOST = "127.0.0.1"
TEST_PORT = 8899
BASE_URL = f"http://{TEST_HOST}:{TEST_PORT}"


@pytest.fixture(scope="session", autouse=True)
def run_test_server():
    """Launch local ASGI test server in a background daemon thread."""
    init_db()

    # Seed initial test data
    with SessionFactory() as session:
        sync_svc = SyncService(session)
        try:
            sync_svc.sync_source()
        except Exception:
            pass

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
    """Yield a fresh headless browser page per test."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        yield page
        browser.close()
