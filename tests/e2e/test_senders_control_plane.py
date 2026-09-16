"""E2E browser tests for the sender control plane, authentication, and readiness."""

import pytest
from playwright.sync_api import Page

from app.domain.enums import Channel, SenderStatus
from app.infrastructure.database import SessionFactory
from app.services.sender_service import SenderService

BASE_URL = "http://127.0.0.1:8899"

pytestmark = pytest.mark.e2e


def test_senders_control_plane(browser_page: Page):
    """Verify the sender & authentication control plane renders with its controls."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # 1. Verify header senders indicator is visible
    page.wait_for_selector("#header-senders-indicator", timeout=25000)
    assert page.is_visible("#hdr-wa-text")
    assert page.is_visible("#hdr-em-text")

    # 2. Open Senders & Auth modal
    page.click("#senders-btn")
    page.wait_for_selector("#senders-modal.open", timeout=20000)
    assert page.is_visible("text=WhatsApp Senders")
    assert page.is_visible("text=Email Senders")

    # 3. Sender action controls are present (session add + state refresh).
    assert page.locator("button:has-text('Add WhatsApp Session')").count() == 1
    assert page.locator("button:has-text('Add Email Sender')").count() == 1

    # 4. Refresh state (no external auth side-effects).
    page.click("button:has-text('Refresh State')")
    page.wait_for_timeout(300)

    # Close modal
    page.click("#senders-modal .modal-close-btn")
    page.wait_for_timeout(300)
    assert not page.is_visible("#senders-modal.open")


def test_readiness_modal_on_blocked_start(browser_page: Page):
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
    page.wait_for_selector("#start-campaign-btn:not([disabled])", timeout=20000)

    # Click New Run
    page.click("#start-campaign-btn")
    page.wait_for_selector("#readiness-modal.open", timeout=20000)
    assert page.is_visible("#readiness-error-reason")
    assert page.is_visible("text=NO_ACTIVE_WHATSAPP_SESSION")

    # Click Open Senders & Authenticate button inside readiness modal
    page.click("#readiness-modal button:has-text('Open Senders')")
    page.wait_for_selector("#senders-modal.open", timeout=20000)

    # Re-activate sender
    confirm_btn = page.locator("button:has-text('Confirm')").first
    if confirm_btn.is_visible():
        confirm_btn.click()
        page.wait_for_selector(".toast", timeout=20000)

    page.click("#senders-modal .modal-close-btn")
