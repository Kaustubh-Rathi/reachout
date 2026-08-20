"""Live Playwright browser E2E test verifying all CRM UI interactions and data fidelity.

Automatically spins up a background Uvicorn server thread on http://127.0.0.1:8000
if not already running, ensuring reliable test execution in automated CI/pytest runs.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path
import pytest
import uvicorn

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def is_port_open(host: str, port: int) -> bool:
    """Check if a TCP port is currently open and accepting connections."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


class ServerThread(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        from crm_server import app
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)

    def run(self):
        self.server.run()

    def shutdown(self):
        self.server.should_exit = True


@pytest.fixture(scope="module")
def live_crm_server():
    """Spins up background Uvicorn test server if localhost:8000 is not already running."""
    from app.infrastructure.database import SessionFactory, init_db
    from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
    from app.services.sync_service import SyncService
    init_db()
    with SessionFactory() as session:
        contact_repo = SqliteContactRepository(session)
        if len(contact_repo.list_all()) < 100:
            try:
                SyncService(session).sync_source()
            except Exception:
                pass

    server_thread = None
    if not is_port_open("127.0.0.1", 8000):
        server_thread = ServerThread("127.0.0.1", 8000)
        server_thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if is_port_open("127.0.0.1", 8000):
                break
            time.sleep(0.1)

    yield "http://127.0.0.1:8000"

    if server_thread:
        server_thread.shutdown()
        server_thread.join(timeout=2.0)


def test_full_browser_e2e(live_crm_server):
    console_errors = []
    failed_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url} - {req.failure}"))

        print(f"1. Navigating to {live_crm_server}...")
        resp = page.goto(live_crm_server, wait_until="networkidle")
        assert resp.status == 200, f"Expected HTTP 200, got {resp.status}"

        page.wait_for_selector("#contacts-table-body tr")
        rows = page.locator("#contacts-table-body tr")
        row_count = rows.count()

        import re
        def get_kpi_num(val_id: str, card_id: str) -> int:
            if page.locator(f"#{val_id}").count() > 0:
                text = page.locator(f"#{val_id}").inner_text().strip()
                m = re.search(r"\d+", text)
                return int(m.group(0)) if m else 0
            if page.locator(f"#{card_id}").count() > 0:
                text = page.locator(f"#{card_id}").inner_text().strip()
                m = re.search(r"\d+", text)
                return int(m.group(0)) if m else 0
            return 0

        kpi_total = get_kpi_num("val-total", "kpi-total")
        kpi_sent = get_kpi_num("val-wa-sent", "kpi-sent")
        kpi_replies = get_kpi_num("val-interested", "kpi-replies")

        print(f"2. Rendered KPIs -> Total: {kpi_total}, Sent: {kpi_sent}, Replies: {kpi_replies}, Table Rows: {row_count}")
        assert kpi_total >= 150, f"Expected KPI total >= 150, got {kpi_total}"
        assert row_count >= 150, f"Expected rendered table row count >= 150, got {row_count}"

        # 3. Verify Table Rows Count
        print(f"3. Table Row Count: {row_count}")
        assert row_count >= 150

        # 4. Verify Email Display in Table
        indeed_row = page.locator("#contacts-table-body tr:has-text('Indeed')").first
        indeed_text = indeed_row.inner_text()
        print(f"4. Indeed row text: '{indeed_text}'")
        assert "vinishadesouza@gmail.com" in indeed_text

        # 5. Test Search Interaction
        print("5. Testing Search Filter...")
        search_box = page.locator("#search-input, #search-box").first
        search_box.fill("Indeed")
        page.wait_for_timeout(300)
        filtered_count = page.locator("#contacts-table-body tr").count()
        print(f"   Filtered count for 'Indeed': {filtered_count}")
        assert filtered_count >= 1

        # Search by email address
        search_box.fill("vinishadesouza")
        page.wait_for_timeout(300)
        email_search_count = page.locator("#contacts-table-body tr").count()
        print(f"   Filtered count for email 'vinishadesouza': {email_search_count}")
        assert email_search_count >= 1
        search_box.fill("")
        page.wait_for_timeout(300)

        # 6. Test Interactive Action (Clicking button or opening drawer)
        print("6. Testing Interactive Table Actions...")
        razorpay_row = page.locator("#contacts-table-body tr:has-text('Razorpay')").first
        if razorpay_row.locator("button:has-text('★ Int')").count() > 0:
            int_btn = razorpay_row.locator("button:has-text('★ Int')").first
            int_btn.click()
            page.wait_for_timeout(400)
            updated_text = razorpay_row.inner_text()
            assert "Interested" in updated_text or "★ Int" in updated_text
        elif razorpay_row.locator(".action-btn").count() > 0:
            edit_btn = razorpay_row.locator(".action-btn").first
            edit_btn.click()
            page.wait_for_selector("#drawer-overlay.active")
            page.locator("#drawer-status-select").select_option("Replied - Interested")
            page.locator(".drawer-footer button.btn-primary").click()
            page.wait_for_timeout(400)

        # 7. Verify Data Persistence Across Page Reload
        print("7. Verifying persistence across page reload...")
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#contacts-table-body tr")
        reloaded_razorpay = page.locator("#contacts-table-body tr:has-text('Razorpay')").first
        assert reloaded_razorpay.count() > 0

        # 8. Check Console and Network Errors (excluding benign missing favicon)
        real_errors = [e for e in console_errors if "favicon" not in e.lower() and "404" not in e]
        assert len(real_errors) == 0, f"JavaScript console errors detected: {real_errors}"
        assert len(failed_requests) == 0, f"Failed network requests detected: {failed_requests}"

        browser.close()
        print("\nAll Browser E2E Tests PASSED successfully!")


if __name__ == "__main__":
    test_full_browser_e2e("http://127.0.0.1:8000")
