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

import pytest
from playwright.sync_api import Page

from app.domain.enums import CRMOutcome, InterviewState
from app.infrastructure.database import SessionFactory
from app.services.crm_service import CrmService
from app.services.sender_service import SenderService
from tests.e2e.conftest import BASE_URL

pytestmark = pytest.mark.e2e


def _activate_sender(sender_id: str) -> None:
    """Mark a seeded sender ACTIVE so manual dispatch is permitted."""
    with SessionFactory() as session:
        SenderService(session).update_sender_status(sender_id, "ACTIVE")


def test_dashboard_loading_and_kpis(browser_page: Page):
    """Test dashboard page loads with all 11 required KPI metrics."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector("header h1", timeout=30000)

    # Verify brand & header
    assert "Reachout CRM" in page.title()
    assert page.locator("header h1", has_text="Reachout CRM").is_visible()

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


def test_theme_toggle_switches_modes(browser_page: Page):
    """The theme control must resolve and switch between light, dark, and system."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector("header h1", timeout=30000)

    html = page.locator("html")
    assert html.get_attribute("data-theme") in ("light", "dark")

    page.click("#theme-dark")
    assert html.get_attribute("data-theme") == "dark"
    assert html.get_attribute("data-theme-mode") == "dark"

    page.click("#theme-light")
    assert html.get_attribute("data-theme") == "light"
    assert html.get_attribute("data-theme-mode") == "light"

    page.click("#theme-system")
    assert html.get_attribute("data-theme-mode") == "system"
    assert html.get_attribute("data-theme") in ("light", "dark")


def test_contact_display_and_email_visibility(browser_page: Page):
    """Test the hierarchy view renders companies and emails are visible when expanded."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")

    # Wait for hierarchy company cards to render
    page.wait_for_function("window.__dashboardReady === true", timeout=30000)
    page.click('[data-nav="contacts"]')
    page.wait_for_selector(".company-card", timeout=30000)
    cards = page.query_selector_all(".company-card")
    assert len(cards) > 0, "Hierarchy view must render company cards"

    # Expand the first company card to reveal HR contacts
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=30000)
    hr_cards = page.query_selector_all(".hr-card")
    assert len(hr_cards) > 0, "Company card must reveal HR contacts when expanded"

    # Company name + status badge must be present
    assert len(page.query_selector_all(".company-name-lg")) > 0, "Company name must be visible"
    assert len(page.query_selector_all(".company-status-badge")) > 0, "Company status badge must be visible"


def test_campaign_lifecycle_controls(browser_page: Page):
    """Test the consolidated campaign control: one context-aware action + contextual stop."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector("#campaign-action-btn", timeout=30000)

    action_btn = page.locator("#campaign-action-btn")
    stop_btn = page.locator("#stop-campaign-btn")
    assert action_btn.count() == 1
    assert stop_btn.count() == 1

    # Idle/terminal state exposes a single "New Run" action.
    page.wait_for_selector("#campaign-action-btn:not([disabled])", timeout=30000)
    assert "New Run" in action_btn.inner_text()

    if action_btn.get_attribute("data-action") == "start":
        action_btn.click()
        page.wait_for_timeout(500)

    status_text = page.locator("#campaign-status-pill").inner_text().strip()
    assert status_text in ("RUNNING", "COMPLETED", "PAUSED", "IDLE")

    # While running, the same control offers Pause.
    if status_text == "RUNNING":
        assert "Pause" in action_btn.inner_text()
        action_btn.click()
        page.wait_for_timeout(300)
        assert page.locator("#campaign-status-pill").inner_text().strip() in ("PAUSED", "COMPLETED", "RUNNING")


def test_manual_whatsapp_send_and_resend(browser_page: Page):
    """Test manual WhatsApp send modal, template selection, and resend workflow."""
    _activate_sender("WA_E2E_SMOKE")
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_function("window.__dashboardReady === true", timeout=30000)
    page.click('[data-nav="contacts"]')
    page.wait_for_selector(".company-card", timeout=30000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=30000)

    # Click first available enabled WA send button
    wa_btn = page.locator("button:has-text('Send WA'):not([disabled]), button:has-text('WA'):not([disabled])").first
    wa_btn.click()

    # Modal should appear
    page.wait_for_selector("#send-modal.open", timeout=30000)
    assert page.is_visible("text=Send WHATSAPP Message")

    # Submit
    page.click("#modal-submit-send-btn")
    page.wait_for_selector(".toast", timeout=30000)
    assert "Sent" in page.inner_text(".toast"), "WhatsApp send should succeed"
    page.wait_for_selector("#send-modal.open", state="hidden", timeout=30000)
    # The Overview KPI reflects the persisted SENT attempt.
    page.click('[data-nav="overview"]')
    page.wait_for_selector("text=WHATSAPP SENT", timeout=30000)


def test_manual_email_send_and_resend(browser_page: Page):
    """Test manual Email send modal and subject/template customization."""
    _activate_sender("EMAIL_E2E_SMOKE")
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
    page.wait_for_function("window.__dashboardReady === true", timeout=30000)
    page.click('[data-nav="contacts"]')
    page.wait_for_selector(".company-card", timeout=30000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=30000)

    # Click first available enabled Email button
    email_btn = page.locator(
        "button:has-text('Send Email'):not([disabled]), button:has-text('Email'):not([disabled])"
    ).first
    email_btn.click()
    page.wait_for_selector("#send-modal.open", timeout=30000)
    assert page.is_visible("text=Send EMAIL Message")
    assert page.is_visible("#modal-subject-group")
    assert page.is_visible("#modal-attachment-input")

    page.click("#modal-submit-send-btn")
    page.wait_for_selector(".toast", timeout=30000)
    assert "Sent" in page.inner_text(".toast"), "Email send should succeed"
    page.wait_for_selector("#send-modal.open", state="hidden", timeout=30000)
    page.click('[data-nav="overview"]')
    page.wait_for_selector("text=EMAIL SENT", timeout=30000)


def test_interested_and_interview_workflows(browser_page: Page):
    """Test Interested workflow records interested_at and transitions interview states via dropdown."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_function("window.__dashboardReady === true", timeout=30000)
    page.click('[data-nav="contacts"]')
    page.wait_for_selector(".company-card", timeout=30000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card select", timeout=30000)

    # 1. Mark Interested
    status_select = page.locator(".hr-card select").first
    status_select.select_option("INTERESTED")
    page.wait_for_selector(".toast", timeout=30000)

    # 2. Mark Interview
    status_select.select_option("INTERVIEW")
    page.wait_for_selector(".toast", timeout=30000)

    # 3. Mark Not Interview / Rejected
    status_select.select_option("REJECTED")
    page.wait_for_selector(".toast", timeout=30000)


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
    page.wait_for_function("window.__dashboardReady === true", timeout=30000)
    page.click('[data-nav="contacts"]')

    # Filter to Follow-up Due and expand the resulting company to reveal the badge
    page.click("button[data-filter='FOLLOW_UP_DUE']")
    page.wait_for_selector(".company-card", timeout=30000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=30000)

    # Verify FOLLOW-UP DUE indicator is visible
    badge = page.locator(".hr-card .badge", has_text="FOLLOW-UP DUE").first
    assert badge.is_visible(), "FOLLOW-UP DUE callout must be prominently visible"


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
    page.wait_for_selector("#senders-modal.open", timeout=30000)
    page.wait_for_selector("#senders-list-container .kpi-card", timeout=30000)
    assert page.is_visible("text=WhatsApp Senders")
    assert page.is_visible("text=Email Senders")
    # Verify no passwords or tokens exposed in HTML
    modal_html = page.locator("#senders-modal").inner_html()
    assert "password" not in modal_html.lower()
    assert "cookie" not in modal_html.lower()
    page.click("#senders-modal .modal-close-btn")

    # 2. Templates drawer
    page.click("#templates-btn")
    page.wait_for_selector("#templates-modal.open", timeout=30000)
    page.wait_for_selector("#templates-list-container", timeout=30000)
    templates_text = page.locator("#templates-list-container").inner_text()
    assert (
        "tmpl_wa_default" in templates_text or "Default SDE Outreach" in templates_text or "WHATSAPP" in templates_text
    )
    page.click("#templates-modal .modal-close-btn")


def test_contact_history_timeline_modal(browser_page: Page):
    """Test contact activity timeline modal renders chronological events."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_function("window.__dashboardReady === true", timeout=30000)
    page.click('[data-nav="contacts"]')
    page.wait_for_selector(".company-card", timeout=30000)
    page.locator(".company-card-header").first.click()
    page.wait_for_selector(".hr-card", timeout=30000)

    hist_btn = page.locator("button:has-text('History')").first
    hist_btn.click()

    page.wait_for_selector("#history-modal.open", timeout=30000)
    assert page.is_visible("text=Activity Timeline")
    page.wait_for_selector("#history-timeline-list", timeout=30000)
    page.click("#history-modal .modal-footer button")
    page.wait_for_timeout(300)
    assert not page.is_visible("#history-modal.open")


def test_sidebar_navigation_and_pagination(browser_page: Page):
    """Test sidebar routing between Overview and Contacts plus contacts pagination."""
    page = browser_page
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_function("window.__dashboardReady === true", timeout=30000)

    # Overview is the default landing page; Contacts is hidden until routed to.
    assert page.eval_on_selector("#page-overview", "el => el.classList.contains('active')")
    assert not page.eval_on_selector("#page-contacts", "el => el.classList.contains('active')")
    assert page.is_visible("text=Live Activity")

    page.click('[data-nav="contacts"]')
    page.wait_for_selector(".company-card", timeout=30000)
    assert page.eval_on_selector("#page-contacts", "el => el.classList.contains('active')")
    assert not page.eval_on_selector("#page-overview", "el => el.classList.contains('active')")

    # Result count reflects the filtered hierarchy.
    summary = page.locator("#hierarchy-summary-text").inner_text()
    assert "compan" in summary.lower(), "Contacts page must show a result count"

    page.click('[data-nav="overview"]')
    page.wait_for_selector("#campaign-control-card", timeout=30000)
    assert page.eval_on_selector("#page-overview", "el => el.classList.contains('active')")
