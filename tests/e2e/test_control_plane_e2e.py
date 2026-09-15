"""E2E Playwright tests for Phase 8 Control Plane, Sender Management, QR Authentication & Readiness."""

from playwright.sync_api import Page

from app.domain.enums import Channel, SenderStatus
from app.infrastructure.database import SessionFactory
from app.services.sender_service import SenderService

BASE_URL = "http://127.0.0.1:8899"


def test_phase8_header_and_senders_control_plane(browser_page: Page):
    """Test full Phase 8 Sender & Authentication Drawer workflow from dashboard UI."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # 1. Verify header senders indicator is visible
    page.wait_for_selector("#header-senders-indicator", timeout=5000)
    assert page.is_visible("#hdr-wa-text")
    assert page.is_visible("#hdr-em-text")

    # 2. Open Senders & Auth modal
    page.click("#senders-btn")
    page.wait_for_selector("#senders-modal.open", timeout=5000)
    assert page.is_visible("text=WhatsApp Senders")
    assert page.is_visible("text=Email Senders")

    # 3. Configure WhatsApp Session Count
    count_input = page.locator("#wa-session-count-input")
    count_input.fill("2")
    page.click("button:has-text('Apply')")
    page.wait_for_selector(".toast", timeout=5000)

    # 4. Start Authentication for WA_SESSION_1
    auth_btn = page.locator("button:has-text('Start Auth'), button:has-text('Re-Authenticate')").first
    auth_btn.click()
    page.wait_for_selector(".toast", timeout=5000)

    # 5. Confirm QR scan in mock mode
    confirm_btn = page.locator("button:has-text('Confirm')").first
    if confirm_btn.is_visible():
        confirm_btn.click()
        page.wait_for_selector(".toast", timeout=5000)

    # Close modal
    page.click("#senders-modal .modal-close-btn")
    page.wait_for_timeout(300)
    assert not page.is_visible("#senders-modal.open")


def test_phase8_readiness_modal_on_blocked_start(browser_page: Page):
    """Test that starting outreach when unauthenticated triggers the Readiness Error Modal."""
    with SessionFactory() as session:
        from app.domain.enums import CampaignStatus
        from app.services.campaign_service import CampaignService

        camp_svc = CampaignService(session)
        for c in camp_svc.campaign_repo.list_all():
            c.status = CampaignStatus.IDLE
            camp_svc.campaign_repo.save(c)

        sender_svc = SenderService(session)
        senders = sender_svc.repo.list_by_channel(Channel.WHATSAPP)
        for s in senders:
            s.status = SenderStatus.AUTH_REQUIRED
            sender_svc.repo.save(s)
        session.commit()

    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # Wait for start button to be enabled
    page.wait_for_selector("#start-campaign-btn:not([disabled])", timeout=10000)

    # Click New Run
    page.click("#start-campaign-btn")
    page.wait_for_selector("#readiness-modal.open", timeout=5000)
    assert page.is_visible("#readiness-error-reason")
    assert page.is_visible("text=NO_ACTIVE_WHATSAPP_SESSION")

    # Click Open Senders & Authenticate button inside readiness modal
    page.click("#readiness-modal button:has-text('Open Senders')")
    page.wait_for_selector("#senders-modal.open", timeout=5000)

    # Re-activate sender
    confirm_btn = page.locator("button:has-text('Confirm')").first
    if confirm_btn.is_visible():
        confirm_btn.click()
        page.wait_for_selector(".toast", timeout=5000)

    page.click("#senders-modal .modal-close-btn")
