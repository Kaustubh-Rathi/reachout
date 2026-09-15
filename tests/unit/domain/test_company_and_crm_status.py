"""Unit tests for Phase 8 Company-level status calculation and CRM status management."""

from app.domain.company import calculate_company_status
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, CompanyStatus, CRMOutcome
from app.domain.outreach_attempt import OutreachAttempt


def test_company_status_not_contacted():
    """Verify company status is NOT_CONTACTED when 0 endpoints are contacted."""
    hr1 = Contact(
        contact_id="acme_hr1",
        company_id="acme",
        name="John Doe",
        phone="+919800000001",
        email="john@acme.com",
    )
    hr2 = Contact(
        contact_id="acme_hr2",
        company_id="acme",
        name="Jane Smith",
        phone="+919800000002",
        email="jane@acme.com",
    )

    status = calculate_company_status([hr1, hr2], historical_attempts=[])
    assert status == CompanyStatus.NOT_CONTACTED


def test_company_status_in_progress():
    """Verify company status is IN_PROGRESS when at least 1 endpoint is contacted but coverage incomplete."""
    hr1 = Contact(
        contact_id="beta_hr1",
        company_id="beta",
        name="Alice",
        phone="+919800000003",
        email="alice@beta.com",
    )
    hr2 = Contact(
        contact_id="beta_hr2",
        company_id="beta",
        name="Bob",
        phone="+919800000004",
        email="bob@beta.com",
    )

    # 1 attempt to hr1 phone
    attempt = OutreachAttempt.prepare(
        contact_id="beta_hr1",
        sender_account_id="WA1",
        channel=Channel.WHATSAPP,
        attempt_type=AttemptType.AUTOMATIC,
        message_body="Test message",
        destination="919800000003",
    )
    attempt.mark_sent("ref-123")

    status = calculate_company_status([hr1, hr2], historical_attempts=[attempt])
    assert status == CompanyStatus.IN_PROGRESS


def test_company_status_contacted():
    """Verify company status is CONTACTED when all endpoints across HRs are covered."""
    hr1 = Contact(
        contact_id="gamma_hr1",
        company_id="gamma",
        name="Charlie",
        phone="+919800000005",
        email="charlie@gamma.com",
    )

    att1 = OutreachAttempt.prepare(
        contact_id="gamma_hr1",
        sender_account_id="WA1",
        channel=Channel.WHATSAPP,
        attempt_type=AttemptType.AUTOMATIC,
        message_body="Test message 1",
        destination="919800000005",
    )
    att1.mark_sent("ref-1")

    att2 = OutreachAttempt.prepare(
        contact_id="gamma_hr1",
        sender_account_id="EMAIL1",
        channel=Channel.EMAIL,
        attempt_type=AttemptType.AUTOMATIC,
        message_body="Test message 2",
        destination="charlie@gamma.com",
    )
    att2.mark_sent("ref-2")

    status = calculate_company_status([hr1], historical_attempts=[att1, att2])
    assert status == CompanyStatus.CONTACTED


def test_company_status_closed_via_flag_or_dnc():
    """Verify company status is CLOSED when explicitly closed or all contacts marked DNC/closed."""
    hr1 = Contact(
        contact_id="delta_hr1",
        company_id="delta",
        name="David",
        phone="+919800000006",
        email="david@delta.com",
        crm_outcome=CRMOutcome.DO_NOT_CONTACT,
    )
    status = calculate_company_status([hr1], historical_attempts=[])
    assert status == CompanyStatus.CLOSED

    # Explicit flag
    hr2 = Contact(
        contact_id="delta_hr2",
        company_id="delta",
        name="Eve",
        phone="+919800000007",
    )
    status_explicit = calculate_company_status([hr2], is_closed=True)
    assert status_explicit == CompanyStatus.CLOSED


def test_all_required_crm_outcomes_supported():
    """Verify all required CRM status values exist in domain."""
    required = [
        "NOT_CONTACTED",
        "CONTACTED",
        "REPLIED",
        "FOLLOW_UP",
        "INTERESTED",
        "NOT_INTERESTED",
        "INTERVIEW",
        "OFFER",
        "CLOSED",
        "DO_NOT_CONTACT",
    ]
    for r in required:
        assert hasattr(CRMOutcome, r) or r in CRMOutcome.__members__
