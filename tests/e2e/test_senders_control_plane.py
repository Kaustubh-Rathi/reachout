"""E2E browser tests for the sender control plane, authentication, and readiness."""

import pytest
from playwright.sync_api import Page

from app.domain.enums import Channel, SenderStatus
from app.infrastructure import database as database_module
from app.services.sender_service import SenderService
from tests.e2e.conftest import BASE_URL, wait_for_dashboard_ready

pytestmark = pytest.mark.e2e


def test_senders_control_plane(browser_page: Page):
    """Verify the sender & authentication control plane renders with its controls."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # 1. Verify header senders indicator is visible
    page.wait_for_selector("#header-senders-indicator", timeout=25000)
    wait_for_dashboard_ready(page)
    assert page.is_visible("#hdr-wa-text")
    assert page.is_visible("#hdr-em-text")

    # 2. Open the Senders page
    page.click('[data-nav="senders"]')
    page.wait_for_selector("#senders-list-container .sender-card", timeout=30000)
    assert page.is_visible("text=WhatsApp Senders")
    assert page.is_visible("text=Email Senders")

    # 3. Sender action controls are present (session add + state refresh).
    assert page.locator("button:has-text('Add WhatsApp Session')").count() == 1
    assert page.locator("button:has-text('Add Email Sender')").count() == 1

    # 4. Refresh state (no external auth side-effects).
    page.click("button:has-text('Refresh State')")
    page.wait_for_timeout(300)

    # Route back to Overview
    page.click('[data-nav="overview"]')
    page.wait_for_selector("#campaign-control-card", timeout=30000)
    assert page.eval_on_selector("#page-senders", "el => !el.classList.contains('active')")


def test_resume_blocked_without_active_sender(browser_page: Page):
    """Resuming a PAUSED campaign without an active sender is refused server-side.

    The Overview deck is pause/resume only, so the readiness gate is exercised
    through Resume rather than the removed "New Run" action.
    """
    with database_module.SessionFactory() as session:
        from app.domain.campaign import Campaign
        from app.domain.enums import CampaignStatus
        from app.services.campaign_service import CampaignService

        camp_svc = CampaignService(session)
        for c in camp_svc.campaign_repo.list_all():
            c.status = CampaignStatus.IDLE
            camp_svc.campaign_repo.save(c)

        sender_svc = SenderService(session)
        for sender_channel in (Channel.WHATSAPP, Channel.EMAIL):
            for sender in sender_svc.repo.list_by_channel(sender_channel):
                sender.status = SenderStatus.AUTH_REQUIRED
                sender_svc.repo.save(sender)

        campaign = Campaign.create("E2E Blocked Resume", Channel.WHATSAPP)
        campaign.status = CampaignStatus.PAUSED
        camp_svc.campaign_repo.save(campaign)
        session.commit()
        campaign_id = campaign.id

    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    wait_for_dashboard_ready(page)

    # Select the paused campaign explicitly; other tests may leave campaigns behind.
    page.wait_for_selector("#campaign-selector", timeout=30000)
    page.select_option("#campaign-selector", campaign_id)
    page.wait_for_selector("#campaign-action-btn:not([hidden])", timeout=30000)
    assert "Resume" in page.locator("#campaign-action-btn").inner_text()

    page.click("#campaign-action-btn")
    toast = page.locator(".toast").first
    toast.wait_for(state="visible", timeout=30000)
    assert "resume" in toast.inner_text().lower()

    # The campaign must stay paused: the readiness gate is enforced, not skipped.
    assert page.locator("#campaign-status-pill").inner_text().strip() == "PAUSED"

    page.click('[data-nav="overview"]')
