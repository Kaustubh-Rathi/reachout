from types import SimpleNamespace

from app.infrastructure.source.synchronizer import (
    DatabaseSourceSynchronizer,
    extract_emails,
    extract_phone_numbers,
    normalize_phone_number,
)
from app.ports.source import SourceRow


def make_synchronizer(default_country_code="1"):
    return DatabaseSourceSynchronizer(
        session=SimpleNamespace(),
        repository_factory=lambda _session: SimpleNamespace(
            company=SimpleNamespace(),
            contact=SimpleNamespace(),
            suppression=SimpleNamespace(),
        ),
        default_country_code=default_country_code,
    )


def test_phone_and_email_extraction_normalizes_and_deduplicates():
    assert normalize_phone_number("") is None
    assert normalize_phone_number("(020) 1234-5678.0", "44") == "442012345678"
    assert normalize_phone_number("001-202-555-0100") == "12025550100"
    assert normalize_phone_number("123") is None
    assert extract_phone_numbers("202-555-0100 / 202-555-0100 and 202-555-0199", "1") == [
        "12025550100",
        "12025550199",
    ]
    assert extract_emails("A@example.com, <a@example.com> and invalid") == ["a@example.com"]


def test_named_source_rows_count_invalid_values_and_create_contacts():
    synchronizer = make_synchronizer()
    rows = [
        SourceRow(
            "contacts.csv", 1, {"company": "Acme", "name": "Alice", "phone": "202-555-0100", "email": "a@acme.com"}
        ),
        SourceRow("contacts.csv", 2, {"company": "Acme", "name": "Bad", "phone": "invalid", "email": "bad"}),
        SourceRow("contacts.csv", 3, {"name": "No company", "phone": ""}),
        SourceRow("contacts.csv", 4, {}),
    ]

    parsed, invalid = synchronizer._parse_source_rows(rows, "contacts.csv")

    assert len(parsed) == 1
    assert parsed[0][0].company_id == "Acme"
    assert parsed[0][0].phones == ["12025550100"]
    assert parsed[0][0].emails == ["a@acme.com"]
    assert invalid == 5


def test_four_column_excel_rows_skip_headers_and_parse_endpoints():
    synchronizer = make_synchronizer()
    rows = [
        SourceRow("contacts.xlsx", 1, {"A": "Company", "B": "Name", "C": "Phone", "D": "Email"}),
        SourceRow("contacts.xlsx", 2, {"A": "Acme", "B": "Alice", "C": "202-555-0100", "D": "alice@acme.com"}),
        SourceRow("contacts.xlsx", 3, {"A": "Beta", "B": "Bob", "C": "invalid", "D": "invalid"}),
    ]

    parsed, invalid = synchronizer._parse_source_rows(rows, "contacts.xlsx")

    assert len(parsed) == 1
    assert parsed[0][0].company_id == "Acme"
    assert parsed[0][0].name == "Alice"
    assert parsed[0][0].phones == ["12025550100"]
    assert parsed[0][0].emails == ["alice@acme.com"]
    assert invalid == 3


def test_wide_excel_rows_create_each_configured_contact_slot():
    synchronizer = make_synchronizer()
    rows = [
        SourceRow(
            "contacts.xlsx",
            1,
            {"A": "Company", "B": "Primary", "C": "Value", "D": "Value", "E": "Shared", "I": "Second", "J": "Number"},
        ),
        SourceRow(
            "contacts.xlsx",
            2,
            {
                "A": "Acme",
                "B": "Alice",
                "C": "202-555-0100",
                "D": "202-555-0101",
                "E": "alice@acme.com",
                "I": "Bob",
                "J": "202-555-0102",
            },
        ),
        SourceRow("contacts.xlsx", 3, {"A": "", "B": "Missing company", "C": "202-555-0103"}),
    ]

    parsed, invalid = synchronizer._parse_source_rows(rows, "contacts.xlsx")

    assert len(parsed) == 2
    assert invalid == 1
    assert [contact.name for contact, _ in parsed] == ["Alice", "Bob"]
    assert parsed[0][0].phones == ["12025550100", "12025550101"]
    assert parsed[0][0].emails == ["alice@acme.com"]
    assert parsed[1][0].phones == ["12025550102"]
