"""Unit tests for ChannelRotationPolicy."""

from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, SenderStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.channel_rotation_policy import ChannelRotationPolicy
from app.domain.sender_account import SenderAccount


def _make_sender(sid: str, ch: Channel) -> SenderAccount:
    return SenderAccount(
        id=sid,
        channel=ch,
        provider="mock",
        identity=f"{sid}@example.com",
        display_name=f"Sender {sid}",
        status=SenderStatus.ACTIVE,
    )


def test_channel_rotation_sequence_generation():
    # WhatsApp only senders
    wa_senders = [_make_sender("wa1", Channel.WHATSAPP), _make_sender("wa2", Channel.WHATSAPP)]
    seq = ChannelRotationPolicy.build_dynamic_channel_sequence(wa_senders)
    assert seq == [Channel.WHATSAPP]

    # Email only senders
    em_senders = [_make_sender("em1", Channel.EMAIL)]
    seq = ChannelRotationPolicy.build_dynamic_channel_sequence(em_senders)
    assert seq == [Channel.EMAIL]

    # Both WhatsApp and Email senders -> sequence based on active senders
    both_senders = [
        _make_sender("wa1", Channel.WHATSAPP),
        _make_sender("wa2", Channel.WHATSAPP),
        _make_sender("em1", Channel.EMAIL),
        _make_sender("em2", Channel.EMAIL),
    ]
    seq = ChannelRotationPolicy.build_dynamic_channel_sequence(both_senders)
    assert seq == [Channel.WHATSAPP, Channel.WHATSAPP, Channel.EMAIL, Channel.EMAIL]


def test_channel_rotation_cursor_advancing():
    seq = [Channel.WHATSAPP, Channel.WHATSAPP, Channel.EMAIL, Channel.EMAIL]

    ch, next_c = ChannelRotationPolicy.get_preferred_channel(0, seq)
    assert ch == Channel.WHATSAPP
    assert next_c == 1

    ch, next_c = ChannelRotationPolicy.get_preferred_channel(1, seq)
    assert ch == Channel.WHATSAPP
    assert next_c == 2

    ch, next_c = ChannelRotationPolicy.get_preferred_channel(2, seq)
    assert ch == Channel.EMAIL
    assert next_c == 3

    ch, next_c = ChannelRotationPolicy.get_preferred_channel(3, seq)
    assert ch == Channel.EMAIL
    assert next_c == 0


def test_evaluate_contact_dispatch():
    contact = Contact(
        contact_id="c1",
        company_id="comp1",
        name="HR Leader",
        phone="+919876543210, +919876543211",
        email="hr@comp.com",
    )

    # 1. Preferred WhatsApp -> dispatches P1
    decision = ChannelRotationPolicy.evaluate_contact_dispatch(
        contact=contact,
        preferred_channel=Channel.WHATSAPP,
        historical_attempts=[],
    )
    assert decision.is_eligible
    assert decision.channel == Channel.WHATSAPP
    assert decision.endpoint.normalized_address == "919876543210"
    assert not decision.is_fallback

    # 2. P1 covered -> dispatches P2
    att1 = OutreachAttempt.prepare(
        contact_id="c1",
        sender_account_id="wa1",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543210",
        message_body="Hi",
    )
    att1.mark_sent("ref-1")
    decision2 = ChannelRotationPolicy.evaluate_contact_dispatch(
        contact=contact,
        preferred_channel=Channel.WHATSAPP,
        historical_attempts=[att1],
    )
    assert decision2.is_eligible
    assert decision2.channel == Channel.WHATSAPP
    assert decision2.endpoint.normalized_address == "919876543211"

    # 3. P1 and P2 covered -> preferred WA falls back to Email (E1)
    att2 = OutreachAttempt.prepare(
        contact_id="c1",
        sender_account_id="wa1",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.WHATSAPP,
        destination="919876543211",
        message_body="Hi",
    )
    att2.mark_sent("ref-2")
    decision3 = ChannelRotationPolicy.evaluate_contact_dispatch(
        contact=contact,
        preferred_channel=Channel.WHATSAPP,
        historical_attempts=[att1, att2],
    )
    assert decision3.is_eligible
    assert decision3.channel == Channel.EMAIL
    assert decision3.endpoint.normalized_address == "hr@comp.com"
    assert decision3.is_fallback

    # 4. All endpoints covered -> not eligible
    att3 = OutreachAttempt.prepare(
        contact_id="c1",
        sender_account_id="em1",
        attempt_type=AttemptType.AUTOMATIC,
        channel=Channel.EMAIL,
        destination="hr@comp.com",
        message_body="Hi",
    )
    att3.mark_sent("ref-3")
    decision4 = ChannelRotationPolicy.evaluate_contact_dispatch(
        contact=contact,
        preferred_channel=Channel.WHATSAPP,
        historical_attempts=[att1, att2, att3],
    )
    assert not decision4.is_eligible
    assert decision4.reason == "CONTACT_FULLY_COVERED"
