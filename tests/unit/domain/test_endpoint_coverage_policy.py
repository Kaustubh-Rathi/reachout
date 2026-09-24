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

    att_prepared = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    assert has_ambiguous_or_inflight_blocker(contact, [att_prepared])

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


def test_endpoint_coverage_states_match_dispatch_policy():
    contact = _make_contact(phones="+919876543210", emails="")

    ready = get_contact_endpoint_metrics(contact, [])["whatsapp_endpoints"][0]
    assert ready["coverage_state"] == "NEVER_ATTEMPTED"
    assert ready["is_ready"] is True

    failed = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    failed.mark_failed("ERR_SYNC_TIMEOUT", "Temporary provider failure")
    retryable = get_contact_endpoint_metrics(contact, [failed])["whatsapp_endpoints"][0]
    assert retryable["coverage_state"] == "RETRYABLE"
    assert retryable["is_ready"] is True
    assert retryable["failure_code"] == "ERR_SYNC_TIMEOUT"

    permanent = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    permanent.mark_failed("ERR_NOT_ON_WHATSAPP", "Number is not on WhatsApp")
    permanent_metrics = get_contact_endpoint_metrics(contact, [failed, permanent])["whatsapp_endpoints"][0]
    assert permanent_metrics["coverage_state"] == "PERMANENT_FAILED"
    assert permanent_metrics["is_ready"] is False
    assert permanent_metrics["is_covered"] is False

    blocked_prior = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    blocked_prior.mark_failed("ERR_SYNC_TIMEOUT", "Temporary provider failure")

    blocked = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    blocked.mark_unknown("Provider status unavailable")
    blocked_metrics = get_contact_endpoint_metrics(
        contact,
        [failed, blocked_prior, blocked],
    )
    assert blocked_metrics["whatsapp_endpoints"][0]["coverage_state"] == "BLOCKED"
    assert blocked_metrics["whatsapp_endpoints"][0]["is_blocked"] is True
    assert blocked_metrics["retryable_failed_endpoints"] == 0


def test_endpoint_metrics_scope_blockers_to_matching_channel():
    contact = _make_contact(phones="+919876543210", emails="hr@c.com")
    blocked = OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id="wa_sender",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Test",
    )
    blocked.mark_recovery_required("Provider outcome requires recovery")

    metrics = get_contact_endpoint_metrics(contact, [blocked])
    whatsapp = metrics["whatsapp_endpoints"][0]
    email = metrics["email_endpoints"][0]

    assert whatsapp["coverage_state"] == "BLOCKED"
    assert whatsapp["is_blocked"] is True
    assert whatsapp["is_ready"] is False
    assert email["coverage_state"] == "NEVER_ATTEMPTED"
    assert email["status"] == "NOT_CONTACTED"
    assert email["is_ready"] is True
    assert email["is_blocked"] is False


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
