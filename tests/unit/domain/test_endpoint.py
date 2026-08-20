"""Unit tests for CommunicationEndpoint and endpoint normalization in Phase 7."""

import pytest
from app.domain.endpoint import (
    CommunicationEndpoint,
    extract_endpoints_from_raw,
    normalize_email_addresses,
    normalize_phone_numbers,
)
from app.domain.enums import Channel


def test_normalize_phone_numbers_single_and_multiple():
    # Clean phone numbers
    raw = "+91 98765 43210, +91-9876543211"
    normalized = normalize_phone_numbers(raw)
    assert normalized == ["919876543210", "919876543211"]

    # Deduplication preserving order
    raw_dupes = "+919876543210, +91 9876543210, 9876543211"
    assert normalize_phone_numbers(raw_dupes) == ["919876543210", "9876543211"]

    # Filter out invalid or garbage numbers
    raw_garbage = "123, invalid-phone, +919876543210, 555"
    assert normalize_phone_numbers(raw_garbage) == ["919876543210"]

    # None and empty
    assert normalize_phone_numbers(None) == []
    assert normalize_phone_numbers("") == []


def test_normalize_email_addresses_single_and_multiple():
    raw = "HR@company.com,  recruiter.lead@company.org ; Talent@Company.COM"
    normalized = normalize_email_addresses(raw)
    assert normalized == [
        "hr@company.com",
        "recruiter.lead@company.org",
        "talent@company.com",
    ]

    # Deduplication and garbage filtering
    raw_garbage = "not-an-email, valid.user@domain.co.in, VALID.USER@domain.co.in, @domain.com"
    assert normalize_email_addresses(raw_garbage) == ["valid.user@domain.co.in"]

    # None and empty
    assert normalize_email_addresses(None) == []
    assert normalize_email_addresses("") == []


def test_communication_endpoint_matching():
    ep1 = CommunicationEndpoint(
        channel=Channel.WHATSAPP,
        address="+91 98765 43210",
        normalized_address="919876543210",
        ordinal=0,
    )
    assert ep1.matches("+919876543210")
    assert ep1.matches("919876543210")
    assert ep1.matches("+91 98765 43210")
    assert not ep1.matches("+919876543211")

    ep2 = CommunicationEndpoint(
        channel=Channel.EMAIL,
        address="HR@Company.com",
        normalized_address="hr@company.com",
        ordinal=0,
    )
    assert ep2.matches("hr@company.com")
    assert ep2.matches("HR@Company.com")
    assert not ep2.matches("other@company.com")


def test_extract_endpoints_from_raw():
    phone_raw = "+919876543210, +919876543211"
    email_raw = "hr1@company.com, hr2@company.com"

    endpoints = extract_endpoints_from_raw(phone_raw, email_raw)
    assert len(endpoints) == 4

    wa_endpoints = [e for e in endpoints if e.channel == Channel.WHATSAPP]
    assert len(wa_endpoints) == 2
    assert wa_endpoints[0].ordinal == 0
    assert wa_endpoints[0].normalized_address == "919876543210"
    assert wa_endpoints[1].ordinal == 1
    assert wa_endpoints[1].normalized_address == "919876543211"

    em_endpoints = [e for e in endpoints if e.channel == Channel.EMAIL]
    assert len(em_endpoints) == 2
    assert em_endpoints[0].ordinal == 0
    assert em_endpoints[0].normalized_address == "hr1@company.com"
    assert em_endpoints[1].ordinal == 1
    assert em_endpoints[1].normalized_address == "hr2@company.com"
