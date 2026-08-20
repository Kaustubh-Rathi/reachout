#!/usr/bin/env python3
"""Canonical contact ingestion and domain logic for Reachout CRM.

Provides single-source-of-truth contact parsing, E.164 phone normalization,
email extraction, deduplication, and CRM status classifications.
"""

from __future__ import annotations

import csv
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
UI_DIR = ROOT / "ui"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
UI_DIR.mkdir(exist_ok=True)


def find_default_workbook() -> Path:
    candidates = [
        DATA_DIR / "MNC_Final.xlsx",
        ROOT / "MNC_Final.xlsx",
        DATA_DIR / "Reachout.xlsx",
        ROOT / "Reachout.xlsx",
    ]
    for c in candidates:
        if c.exists():
            return c
    return DATA_DIR / "MNC_Final.xlsx"


DEFAULT_WORKBOOK = find_default_workbook()
DEFAULT_CLEAN_CSV = DATA_DIR / "mnc_cleaned_contacts.csv"
DEFAULT_LOG = (
    LOGS_DIR / "mnc_whatsapp_send_log.csv"
    if (LOGS_DIR / "mnc_whatsapp_send_log.csv").exists()
    else (
        ROOT / "mnc_whatsapp_send_log.csv"
        if (ROOT / "mnc_whatsapp_send_log.csv").exists()
        else LOGS_DIR / "mnc_whatsapp_send_log.csv"
    )
)
CRM_DB_FILE = (
    DATA_DIR / "crm_data.json"
    if (DATA_DIR / "crm_data.json").exists()
    else (
        ROOT / "crm_data.json"
        if (ROOT / "crm_data.json").exists()
        else DATA_DIR / "crm_data.json"
    )
)

MESSAGE_TEMPLATE = """Hey {first_name},

I'm Kaustubh Rathi, B.Tech 2026 grad from IIIT Allahabad (CGPA 8.52). Recently completed my SDE Internship at Amazon (Grocery Ordering team). I have strong experience in AI/ML, statistics, and competitive programming (Codeforces 1751, JEE AIR 4800).

Would love to explore opportunities at *{company}*. Please find my resume attached.

Portfolio: https://kaustubh-rathi.github.io/Kaustubh_Rathi_Portfolio/
LinkedIn: https://www.linkedin.com/in/kaustubh-rathi-9228ab255
GitHub: https://github.com/Kaustubh-Rathi

Thank you for your time!"""

# Canonical CRM Status Taxonomy
ALL_CRM_STATUSES: List[str] = [
    "Pending Reply",
    "Replied - Interested",
    "Replied - No Openings",
    "Referral Given",
    "Interview Scheduled",
    "Call Scheduled",
    "Got Reply",
    "Not Hiring Freshers",
    "Follow-up Needed",
    "Ghosted / No Reply",
    "No Status",
]

REPLY_STATUSES: Set[str] = {
    "Replied - Interested",
    "Replied - No Openings",
    "Referral Given",
    "Interview Scheduled",
    "Call Scheduled",
    "Got Reply",
    "Not Hiring Freshers",
}

POSITIVE_STATUSES: Set[str] = {
    "Replied - Interested",
    "Referral Given",
    "Interview Scheduled",
    "Call Scheduled",
}

NO_HIRING_STATUSES: Set[str] = {
    "Replied - No Openings",
    "Not Hiring Freshers",
}

FOLLOW_UP_STATUSES: Set[str] = {
    "Follow-up Needed",
}

UNREPLIED_STATUSES: Set[str] = {
    "No Status",
    "Pending Reply",
    "Ghosted / No Reply",
}

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": DOC_REL_NS, "pr": PKG_REL_NS}

CONTACT_SLOTS: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("B", ("C", "D"), "contact_1"),
    ("I", ("J",), "contact_2"),
    ("L", ("M",), "contact_3"),
    ("O", ("P",), "contact_4"),
)


@dataclass(frozen=True)
class Contact:
    source_row: int
    company: str
    name: str
    phone: str
    email: str = ""
    raw_number: str = ""
    source_columns: str = ""
    slot: str = "contact_1"
    status: str = "ready"
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{clean_text(self.company).casefold()}|{self.phone}"


def clean_text(value: object) -> str:
    """Normalize whitespace and strip leading/trailing spaces."""
    if value is None:
        return ""
    text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def column_name(cell_reference: str) -> str:
    """Extract column letters from an Excel cell reference (e.g. 'AA12' -> 'AA')."""
    match = re.match(r"[A-Z]+", cell_reference.upper())
    return match.group(0) if match else ""


def normalize_phone(raw: str, default_country_code: str = "91") -> str | None:
    """Normalize phone number to E.164 digits without leading '+'.
    
    Handles:
    - 10-digit Indian numbers: prepends default country code (e.g. 9876543210 -> 919876543210)
    - 11-digit numbers with leading 0: strips 0 and prepends country code (e.g. 09876543210 -> 919876543210)
    - 12-digit Indian numbers (919876543210): preserved as-is
    - Float notation from Excel (8826363651.0): stripped of .0
    - International numbers (11 to 15 digits, e.g. US 12627491577): preserved as-is
    - Leading 00 international prefixes: stripped to international digits
    """
    value = clean_text(raw)
    if not value:
        return None
    value = re.sub(r"\.0$", "", value)
    digits = re.sub(r"\D", "", value)

    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = default_country_code + digits

    if not 10 <= len(digits) <= 15:
        return None
    return digits


def extract_numbers(raw: str, default_country_code: str = "91") -> list[str]:
    """Extract every unique normalized phone number from a cell string."""
    value = clean_text(raw)
    if not value:
        return []

    chunks = re.split(r"\s*(?:,|;|/|\||\n|\band\b|\bor\b)\s*", value, flags=re.I)
    numbers: list[str] = []
    for chunk in chunks:
        normalized = normalize_phone(chunk, default_country_code)
        if normalized and normalized not in numbers:
            numbers.append(normalized)
    return numbers


def read_mnc_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    """Read worksheet rows directly from an XLSX OpenXML archive."""
    if not path.exists():
        raise SystemExit(f"Workbook not found: {path}")

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise SystemExit(f"Cannot read workbook {path}: {exc}") from exc

    with archive:
        names = set(archive.namelist())
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(node.text or "" for node in item.findall(".//m:t", NS))
                for item in root.findall("m:si", NS)
            ]

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall("pr:Relationship", NS)
        }

        sheets = workbook.findall(".//m:sheet", NS)
        sheet = next(
            (
                item
                for item in sheets
                if item.attrib.get("name", "").strip().casefold() in ("mnc_cleaned", "mnc", "sheet1")
            ),
            sheets[0] if sheets else None,
        )
        if sheet is None:
            raise SystemExit(f"The workbook {path.name} does not contain any readable sheet.")

        relationship_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
        target = targets[relationship_id].lstrip("/")
        if not target.startswith("xl/"):
            target = str(PurePosixPath("xl") / target)
        worksheet = ET.fromstring(archive.read(target))

        result: list[tuple[int, dict[str, str]]] = []
        for row in worksheet.findall(".//m:sheetData/m:row", NS):
            row_number = int(row.attrib.get("r", len(result) + 1))
            values: dict[str, str] = {}
            for cell in row.findall("m:c", NS):
                col = column_name(cell.attrib.get("r", ""))
                cell_type = cell.attrib.get("t")
                value_node = cell.find("m:v", NS)
                t_nodes = cell.findall(".//m:t", NS)

                if t_nodes:
                    value = "".join(node.text or "" for node in t_nodes)
                elif cell_type == "s" and value_node is not None:
                    value = shared_strings[int(value_node.text or "0")]
                elif value_node is not None:
                    value = value_node.text or ""
                else:
                    value = ""
                values[col] = clean_text(value)
            result.append((row_number, values))
        return result


def clean_contacts(
    rows: list[tuple[int, dict[str, str]]], default_country_code: str = "91"
) -> list[Contact]:
    """Clean and extract contacts with full phone normalization and email preservation."""
    contacts: list[Contact] = []

    # Detect if sheet is 4-column format (Company, Person, Phone, Email) or multi-column
    is_4_col = False
    if rows:
        header_vals = [str(v).lower() for v in rows[0][1].values()]
        if any("phone" in v for v in header_vals) or len(rows[0][1]) <= 4:
            is_4_col = True

    for row_number, row in rows:
        if row_number == 1:
            continue
        company = clean_text(row.get("A"))
        if not company:
            continue

        if is_4_col:
            name = clean_text(row.get("B")) or "there"
            raw_phones = clean_text(row.get("C"))
            email = clean_text(row.get("D"))
            row_phones: set[str] = set()
            for phone in extract_numbers(raw_phones, default_country_code):
                if phone in row_phones:
                    continue
                row_phones.add(phone)
                contacts.append(
                    Contact(
                        source_row=row_number,
                        company=company,
                        name=name,
                        phone=phone,
                        email=email,
                        raw_number=raw_phones,
                        source_columns="B/C",
                        slot="contact_1",
                    )
                )
        else:
            primary_name = clean_text(row.get("B"))
            row_phones: set[str] = set()
            for name_col, number_cols, slot in CONTACT_SLOTS:
                name = clean_text(row.get(name_col)) or primary_name or "there"
                for number_col in number_cols:
                    raw = clean_text(row.get(number_col))
                    email = clean_text(row.get("E"))
                    for phone in extract_numbers(raw, default_country_code):
                        if phone in row_phones:
                            continue
                        row_phones.add(phone)
                        contacts.append(
                            Contact(
                                source_row=row_number,
                                company=company,
                                name=name,
                                phone=phone,
                                email=email,
                                raw_number=raw,
                                source_columns=f"{name_col}/{number_col}",
                                slot=slot,
                            )
                        )

    # Clean exact repeats, but block ambiguous numbers assigned to different
    # people or companies to avoid sending to the wrong recipient.
    by_phone: dict[str, list[int]] = defaultdict(list)
    for index, contact in enumerate(contacts):
        by_phone[contact.phone].append(index)

    cleaned = list(contacts)
    for phone, indexes in by_phone.items():
        if len(indexes) < 2:
            continue

        names = {
            clean_text(contacts[index].name).casefold() for index in indexes
        }
        identities = {
            (
                clean_text(contacts[index].company).casefold(),
                clean_text(contacts[index].name).casefold(),
            )
            for index in indexes
        }
        if len(identities) == 1:
            for index in indexes[1:]:
                cleaned[index] = replace(
                    cleaned[index],
                    status="duplicate_skipped",
                    detail=f"same person and phone already listed at row {contacts[indexes[0]].source_row}",
                )
        elif len(names) == 1:
            # Same person listed under two company rows; keep both
            continue
        else:
            assignments = "; ".join(
                f"row {contacts[index].source_row}: "
                f"{contacts[index].name} @ {contacts[index].company}"
                for index in indexes
            )
            for index in indexes:
                cleaned[index] = replace(
                    cleaned[index],
                    status="duplicate_conflict",
                    detail=f"+{phone} is assigned to multiple contacts: {assignments}",
                )
    return cleaned


def sanitize_for_csv(value: Any) -> str:
    """Sanitize a value to prevent CSV Formula Injection (CWE-1236).
    
    If the string begins with =, +, -, @, \\t, \\r, or %, prepend a single quote
    so spreadsheet software (Excel, Calc) treats it as a text literal.
    """
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r", "%"):
        return f"'{text}"
    return text


def write_clean_csv(path: Path, contacts: list[Contact]) -> None:
    """Write cleaned contacts list to CSV with formula injection protection."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source_row",
                "company",
                "name",
                "phone",
                "email",
                "raw_number",
                "source_columns",
                "slot",
                "status",
                "detail",
            ],
        )
        writer.writeheader()
        for contact in contacts:
            row_dict = {
                k: sanitize_for_csv(v) if isinstance(v, str) else v
                for k, v in contact.__dict__.items()
            }
            writer.writerow(row_dict)


def first_name(full_name: str) -> str:
    """Extract greeting first name from person name."""
    name = re.sub(r"\([^)]*\)", "", clean_text(full_name))
    name = re.sub(r"^(?:mr|mrs|ms|dr)\.?\s+", "", name, flags=re.I)
    return name.split()[0] if name and name.casefold() != "there" else "there"


def render_message(contact: Contact) -> str:
    """Render WhatsApp job outreach template for contact."""
    return MESSAGE_TEMPLATE.format(
        first_name=first_name(contact.name),
        name=contact.name,
        company=contact.company,
    )


def load_sent_keys(path: Path) -> set[str]:
    """Load sent keys from send log CSV."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row.get("key", "")
            for row in csv.DictReader(handle)
            if row.get("status") in ("sent", "sent_text_only")
        }


def ready_contacts(
    contacts: list[Contact], sent_keys: set[str], force_resend: bool
) -> tuple[list[Contact], int]:
    """Filter ready contacts for sending."""
    ready: list[Contact] = []
    already_sent = 0
    for contact in contacts:
        if contact.status != "ready":
            continue
        if not force_resend and contact.key in sent_keys:
            already_sent += 1
            continue
        ready.append(contact)
    return ready, already_sent
