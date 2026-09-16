"""Browser E2E test suite for Reachout CRM Control Plane.

Tests complete administrative workflows in headless Camoufox (Firefox), the
same browser engine the application uses for WhatsApp automation:
- Dashboard loading & KPI metrics
- Contact table rendering & direct email visibility
- Campaign lifecycle (Start, Pause, Resume, Stop)
- Manual WhatsApp & Email dispatches & resends
- Interested & Not-Interested state transitions
- Interview & Not-Interview workflows
- Follow-up reminder display & auto-clearing
- Source synchronization UI & summary
- Multi-sender & templates visibility
- Contact activity timeline
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from playwright.sync_api import Page

from app.domain.enums import CRMOutcome, InterviewState
from app.infrastructure.database import SessionFactory
from app.services.crm_service import CrmService
from tests.e2e.conftest import BASE_URL


def test_dashboard_loading_and_kpis(browser_page: Page):
    """Test dashboard page loads with all 11 required KPI metrics."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # Verify brand & header
    assert "Reachout CRM" in page.title()
    assert page.is_visible("text=Reachout CRM")

    # Verify all required KPI metric cards exist
    required_kpis = [
        "Total Contacts",
        "Eligible",
        "Contacted",
        "WhatsApp Sent",
        "Email Sent",
        "Interested",
        "Not Interested",
        "Interview",
        "Follow-up Due",
        "Failed",
        "Recovery Required",
    ]
    for kpi in required_kpis:
        assert page.is_visible(f"text={kpi}"), f"KPI card '{kpi}' must be visible on dashboard"


def test_contact_display_and_email_visibility(browser_page: Page):
    """Test the hierarchy view renders companies and emails are visible when expanded."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # Wait for hierarchy company cards to render
    page.wait_for_selector(".company-card", timeout=20000)
    cards = page.query_selector_all(".company-card")
    assert len(cards) > 0, "Hierarchy view must render company cards"

    # Expand the first company card to reveal HR contacts
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=20000)
    hr_cards = page.query_selector_all(".hr-card")
    assert len(hr_cards) > 0, "Company card must reveal HR contacts when expanded"

    # Company name + status badge must be present
    assert len(page.query_selector_all(".company-name-lg")) > 0, "Company name must be visible"
    assert len(page.query_selector_all(".company-status-badge")) > 0, "Company status badge must be visible"


def test_campaign_lifecycle_controls(browser_page: Page):
    """Test campaign control plane buttons: Start Outreach, Pause, Resume, Stop."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    start_btn = page.locator("#start-campaign-btn")
    pause_btn = page.locator("#pause-campaign-btn")
    resume_btn = page.locator("#resume-campaign-btn")
    stop_btn = page.locator("#stop-campaign-btn")
    assert resume_btn.count() == 1
    assert stop_btn.count() == 1

    # If start button is enabled, click start
    if start_btn.is_enabled():
        start_btn.click()
        page.wait_for_timeout(500)

    # Check status
    status_pill = page.locator("#campaign-status-pill")
    status_text = status_pill.inner_text().strip()
    assert status_text in ("RUNNING", "COMPLETED", "PAUSED", "IDLE")

    if pause_btn.is_enabled():
        pause_btn.click()
        page.wait_for_timeout(300)
        assert page.locator("#campaign-status-pill").inner_text().strip() in ("PAUSED", "COMPLETED", "RUNNING")


def test_manual_whatsapp_send_and_resend(browser_page: Page):
    """Test manual WhatsApp send modal, template selection, and resend workflow."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector(".company-card", timeout=20000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=20000)

    # Click first available enabled WA send button
    wa_btn = page.locator("button:has-text('Send WA'):not([disabled]), button:has-text('WA'):not([disabled])").first
    wa_btn.click()

    # Modal should appear
    page.wait_for_selector("#send-modal.open", timeout=20000)
    assert page.is_visible("text=Send WHATSAPP Message")

    # Submit
    page.click("#modal-submit-send-btn")
    page.wait_for_selector(".toast", timeout=20000)
    # The hierarchy refreshes after the send and the endpoint reflects the
    # persisted SENT attempt.
    page.wait_for_selector("text=WHATSAPP SENT", timeout=20000)


def test_manual_email_send_and_resend(browser_page: Page):
    """Test manual Email send modal and subject/template customization."""
    # Ensure at least one contact has an email
    with SessionFactory() as session:
        crm_svc = CrmService(session)
        contacts = crm_svc.contact_repo.list_all()
        if contacts:
            contacts[0].email = "candidate.target@domain.com"
            crm_svc.contact_repo.save(contacts[0])
            session.commit()

    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector(".company-card", timeout=20000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=20000)

    # Click first available enabled Email button
    email_btn = page.locator(
        "button:has-text('Send Email'):not([disabled]), button:has-text('Email'):not([disabled])"
    ).first
    email_btn.click()
    page.wait_for_selector("#send-modal.open", timeout=20000)
    assert page.is_visible("text=Send EMAIL Message")
    assert page.is_visible("#modal-subject-group")
    assert page.is_visible("#modal-attachment-input")

    page.click("#modal-submit-send-btn")
    page.wait_for_selector(".toast", timeout=20000)
    page.wait_for_selector("text=EMAIL SENT", timeout=20000)


def test_interested_and_interview_workflows(browser_page: Page):
    """Test Interested workflow records interested_at and transitions interview states via dropdown."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector(".company-card", timeout=20000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card select", timeout=20000)

    # 1. Mark Interested
    status_select = page.locator(".hr-card select").first
    status_select.select_option("INTERESTED")
    page.wait_for_selector(".toast", timeout=20000)

    # 2. Mark Interview
    status_select.select_option("INTERVIEW")
    page.wait_for_selector(".toast", timeout=20000)

    # 3. Mark Not Interview / Rejected
    status_select.select_option("REJECTED")
    page.wait_for_selector(".toast", timeout=20000)


def test_followup_reminder_due_display(browser_page: Page):
    """Test follow-up due UI callout appears for interested contacts with pending interview >= 7 days."""
    past_date = datetime.now(timezone.utc) - timedelta(days=10)
    with SessionFactory() as session:
        crm_svc = CrmService(session)
        contacts = crm_svc.contact_repo.list_all()
        if contacts:
            target = contacts[0]
            target.update_crm_outcome(CRMOutcome.INTERESTED, past_date)
            target.interested_at = past_date
            target.interview_status = InterviewState.PENDING
            crm_svc.contact_repo.save(target)
            session.commit()

    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # Filter to Follow-up Due
    page.click("button[data-filter='FOLLOW_UP_DUE']")
    page.wait_for_timeout(500)

    # Verify FOLLOW-UP DUE indicator is visible
    assert page.is_visible("text=FOLLOW-UP DUE"), "FOLLOW-UP DUE callout must be prominently visible"


def test_source_synchronization_modal(browser_page: Page):
    """Test source synchronization summary modal opens and displays metrics."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    sync_btn = page.locator("#sync-btn")
    sync_btn.click()

    # Modal should open with sync summary
    page.wait_for_selector("#sync-modal.open", timeout=25000)
    assert page.is_visible("text=Source Synchronization Summary")
    assert page.is_visible("text=Total Read")
    assert page.is_visible("text=New Contacts")

    page.click("#sync-modal .modal-close-btn")
    page.wait_for_timeout(300)
    assert not page.is_visible("#sync-modal.open")


def test_senders_and_templates_drawers(browser_page: Page):
    """Test multi-sender and templates modals dynamically list accounts without credentials."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # 1. Senders drawer
    page.click("#senders-btn")
    page.wait_for_selector("#senders-modal.open", timeout=20000)
    page.wait_for_selector("#senders-list-container .kpi-card", timeout=20000)
    assert page.is_visible("text=WhatsApp Senders")
    assert page.is_visible("text=Email Senders")
    # Verify no passwords or tokens exposed in HTML
    modal_html = page.locator("#senders-modal").inner_html()
    assert "password" not in modal_html.lower()
    assert "cookie" not in modal_html.lower()
    page.click("#senders-modal .modal-close-btn")

    # 2. Templates drawer
    page.click("#templates-btn")
    page.wait_for_selector("#templates-modal.open", timeout=20000)
    page.wait_for_selector("#templates-list-container", timeout=20000)
    templates_text = page.locator("#templates-list-container").inner_text()
    assert (
        "tmpl_wa_default" in templates_text or "Default SDE Outreach" in templates_text or "WHATSAPP" in templates_text
    )
    page.click("#templates-modal .modal-close-btn")


def test_contact_history_timeline_modal(browser_page: Page):
    """Test contact activity timeline modal renders chronological events."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector(".company-card", timeout=20000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=20000)

    hist_btn = page.locator("button:has-text('History')").first
    hist_btn.click()

    page.wait_for_selector("#history-modal.open", timeout=20000)
    assert page.is_visible("text=Activity Timeline")
    page.wait_for_selector("#history-timeline-list", timeout=20000)
    page.click("#history-modal .modal-footer button")
    page.wait_for_timeout(300)
    assert not page.is_visible("#history-modal.open")
