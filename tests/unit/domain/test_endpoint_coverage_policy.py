"""Unit tests for EndpointCoveragePolicy."""

from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.endpoint_coverage_policy import (
    get_contact_endpoint_metrics,
    get_next_uncovered_endpoint,
    get_uncovered_endpoints,
    has_ambiguous_or_inflight_blocker,
    is_contact_fully_covered,
    is_endpoint_covered,
)


def _make_contact(phones="+919876543210, +919876543211", emails="hr1@c.com, hr2@c.com") -> Contact:
    return Contact(
        contact_id="c1",
        company_id="company_a",
        name="John Doe",
        phone=phones,
        email=emails,
    )


def test_is_endpoint_covered():
    contact = _make_contact()
    ep_wa1 = contact.endpoints[0]  # 919876543210
    ep_wa2 = contact.endpoints[1]  # 919876543211

    # No attempts -> not covered
    assert not is_endpoint_covered(ep_wa1, [])

    # Failed attempt -> not covered
    att_fail = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    att_fail.mark_failed("FAILED", "Network error")
    assert not is_endpoint_covered(ep_wa1, [att_fail])

    # Sent attempt for ep1 -> ep1 covered, ep2 not covered
    att_sent = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    att_sent.mark_sent("ref-123")
    assert is_endpoint_covered(ep_wa1, [att_sent])
    assert not is_endpoint_covered(ep_wa2, [att_sent])


def test_has_ambiguous_or_inflight_blocker():
    contact = _make_contact()

    # Clean history
    assert not has_ambiguous_or_inflight_blocker(contact, [])

    # In flight attempt
    att_sending = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    att_sending.status = OutreachStatus.SENDING
    assert has_ambiguous_or_inflight_blocker(contact, [att_sending])

    # Unknown / recovery required attempt
    att_unknown = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    att_unknown.mark_unknown("Crash")
    assert has_ambiguous_or_inflight_blocker(contact, [att_unknown])


def test_is_contact_fully_covered_and_get_uncovered_endpoints():
    contact = _make_contact(phones="+919876543210", emails="hr@c.com")
    assert len(contact.endpoints) == 2  # 1 WA, 1 Email

    # Initially 2 uncovered endpoints
    uncovered = get_uncovered_endpoints(contact, [])
    assert len(uncovered) == 2
    assert not is_contact_fully_covered(contact, [])

    # Send WA
    att1 = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    att1.mark_sent("ref-1")
    uncovered = get_uncovered_endpoints(contact, [att1])
    assert len(uncovered) == 1
    assert uncovered[0].channel == Channel.EMAIL
    assert not is_contact_fully_covered(contact, [att1])

    # Send Email
    att2 = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="em_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.EMAIL,
        destination="hr@c.com",
        message_body="Test",
    )
    att2.mark_sent("ref-2")
    uncovered = get_uncovered_endpoints(contact, [att1, att2])
    assert len(uncovered) == 0
    assert is_contact_fully_covered(contact, [att1, att2])


def test_get_next_uncovered_endpoint():
    contact = _make_contact(phones="+919876543210", emails="hr@c.com")

    # Target WA channel -> returns WA endpoint
    ep_wa = get_next_uncovered_endpoint(contact, [], channel=Channel.WHATSAPP)
    assert ep_wa is not None
    assert ep_wa.channel == Channel.WHATSAPP
    assert ep_wa.normalized_address == "919876543210"

    # Once WA is covered, target Email -> returns Email endpoint
    att_wa = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    att_wa.mark_sent("ref-1")
    ep_em = get_next_uncovered_endpoint(contact, [att_wa], channel=Channel.EMAIL)
    assert ep_em is not None
    assert ep_em.channel == Channel.EMAIL
    assert ep_em.normalized_address == "hr@c.com"


def test_get_contact_endpoint_metrics():
    contact = _make_contact(phones="+919876543210, +919876543211", emails="hr1@c.com")
    att = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    att.mark_sent("ref-1")

    metrics = get_contact_endpoint_metrics(contact, [att])
    assert metrics["total_endpoints"] == 3
    assert metrics["covered_endpoints"] == 1
    assert metrics["whatsapp_total"] == 2
    assert metrics["whatsapp_covered"] == 1
    assert metrics["email_total"] == 1
    assert metrics["email_covered"] == 0
    assert not metrics["is_fully_covered"]
