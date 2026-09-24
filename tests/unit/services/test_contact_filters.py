from app.services.contact_filters import (
    apply_contact_filters,
    company_matches_filters,
    matches_priority,
    matches_send_status,
)


def test_send_status_prefers_explicit_channel_status_over_timestamp():
    contact = {
        "whatsapp_status": "SENT",
        "last_whatsapp_at": None,
        "email_status": "NOT_SENT",
        "last_email_at": "2026-01-01",
    }
    assert matches_send_status(contact, "SENT") is True
    assert matches_send_status(contact, "WHATSAPP_SENT") is True
    assert matches_send_status(contact, "EMAIL_SENT") is False
    assert matches_send_status(contact, "NOT_SENT") is False
    assert matches_send_status(contact, "UNKNOWN") is True


def test_send_status_falls_back_to_legacy_timestamps():
    assert matches_send_status({"last_whatsapp_at": "2026-01-01"}, "WHATSAPP_SENT") is True
    assert matches_send_status({"last_email_at": None}, "EMAIL_SENT") is False
    assert matches_send_status({}, "NOT_SENT") is True


def test_priority_buckets_cover_crm_activity_and_uncontacted_states():
    assert matches_priority({"crm_outcome": "INTERESTED"}, "INTERESTED") is True
    assert matches_priority({"crm_outcome": "NOT_INTERESTED"}, "NOT_INTERESTED") is True
    assert matches_priority({"follow_up_due": True}, "FOLLOW_UP_DUE") is True
    assert matches_priority({"last_activity_at": "2026-01-01"}, "RECENTLY_ACTIVE") is True
    assert matches_priority({"last_contacted": "2026-01-01"}, "RECENTLY_ACTIVE") is True
    assert matches_priority({}, "UNCONTACTED") is True
    assert matches_priority({}, "UNKNOWN") is True


def test_apply_contact_filters_materializes_generators_and_combines_filters():
    contacts = (
        value
        for value in [
            {"crm_outcome": "INTERESTED", "whatsapp_status": "SENT"},
            {"crm_outcome": "INTERESTED", "whatsapp_status": "NOT_SENT"},
            {"crm_outcome": "NOT_INTERESTED", "whatsapp_status": "SENT"},
        ]
    )
    result = apply_contact_filters(contacts, send_status="SENT", priority_filter="INTERESTED")
    assert result == [{"crm_outcome": "INTERESTED", "whatsapp_status": "SENT"}]


def test_company_filters_use_aggregate_coverage_and_nested_endpoints():
    hierarchy = {
        "covered_endpoints": 1,
        "contacts": [
            {
                "endpoints": [
                    {"channel": "WHATSAPP", "coverage_state": "SENT"},
                    {"channel": "EMAIL", "coverage_state": "READY"},
                ],
                "crm_outcome": "INTERESTED",
            }
        ],
    }
    assert company_matches_filters(hierarchy, channel_status="SENT") is True
    assert company_matches_filters(hierarchy, channel_status="NOT_SENT") is False
    assert company_matches_filters(hierarchy, channel_status="WHATSAPP_SENT") is True
    assert company_matches_filters(hierarchy, channel_status="EMAIL_SENT") is False
    assert company_matches_filters(hierarchy, priority_filter="INTERESTED") is True
    assert company_matches_filters({"contacts": []}, channel_status="WHATSAPP_SENT") is False
