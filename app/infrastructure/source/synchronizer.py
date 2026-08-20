"""One-way source synchronization service.

Reconciles raw Excel/CSV rows into the relational domain repository without
ever mutating the source files. Handles new rows, updated contact details,
duplicate records, new companies, and respects deletion tombstones.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.domain.company import Company, normalize_company_name
from app.domain.contact import Contact
from app.domain.source_record import SourceRecord
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
from app.infrastructure.source.excel_reader import TabularSourceReader, clean_text
from app.ports.source import SourceReader, SourceRow, SourceSynchronizer, SyncSummary

CONTACT_SLOTS: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("B", ("C", "D"), "contact_1"),
    ("I", ("J",), "contact_2"),
    ("L", ("M",), "contact_3"),
    ("O", ("P",), "contact_4"),
)


def normalize_phone_number(raw: str, default_country_code: str = "91") -> Optional[str]:
    """Normalize phone number to E.164 digits without leading '+'."""
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


def extract_phone_numbers(raw: str, default_country_code: str = "91") -> List[str]:
    """Extract all unique valid phone numbers from a cell string."""
    value = clean_text(raw)
    if not value:
        return []
    chunks = re.split(r"\s*(?:,|;|/|\||\n|\band\b|\bor\b)\s*", value, flags=re.I)
    numbers: List[str] = []
    for chunk in chunks:
        norm = normalize_phone_number(chunk, default_country_code)
        if norm and norm not in numbers:
            numbers.append(norm)
    return numbers


def extract_emails(raw: str) -> List[str]:
    """Extract all unique valid emails from a cell string."""
    value = clean_text(raw)
    if not value:
        return []
    email_re = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
    chunks = re.split(r"\s*(?:,|;|/|\||\n|\band\b)\s*", value, flags=re.I)
    emails: List[str] = []
    for chunk in chunks:
        cleaned = chunk.strip().strip("<>").strip().lower()
        if email_re.match(cleaned) and cleaned not in emails:
            emails.append(cleaned)
    return emails


class DatabaseSourceSynchronizer:
    """Synchronizes external sheets/CSVs into the SQLite relational database."""

    def __init__(
        self,
        session: Session,
        reader: Optional[SourceReader] = None,
        default_country_code: str = "91",
    ) -> None:
        self.session = session
        self.reader = reader or TabularSourceReader()
        self.company_repo = SqliteCompanyRepository(session)
        self.contact_repo = SqliteContactRepository(session)
        self.suppression_repo = SqliteSuppressionRepository(session)
        self.default_country_code = default_country_code

    def sync_source(
        self, source_path: str, sheet_name: Optional[str] = None
    ) -> SyncSummary:
        path = Path(source_path).resolve()
        raw_rows = self.reader.read_source(str(path), sheet_name)

        total_read = len(raw_rows)
        new_contacts = 0
        updated_contacts = 0
        unchanged_contacts = 0
        new_companies = 0
        updated_companies = 0
        new_phone_endpoints = 0
        new_email_endpoints = 0
        skipped_invalid = 0
        errors: List[str] = []

        # Parse rows into candidate extracted contacts
        parsed_candidates, invalid_count = self._parse_source_rows(raw_rows, path.name)
        skipped_invalid += invalid_count

        now = datetime.now(timezone.utc)
        created_company_ids: Set[str] = set()

        for candidate, src_rec in parsed_candidates:
            try:
                # 1. Check if suppressed / tombstoned
                canonical_k = candidate.canonical_key
                if self.suppression_repo.is_suppressed(
                    phone=candidate.phone,
                    email=candidate.email,
                    canonical_key=canonical_k,
                ):
                    continue

                # 2. Ensure Company exists
                norm_comp = normalize_company_name(candidate.company_id)
                comp = self.company_repo.get_by_normalized_name(norm_comp)
                if not comp:
                    comp = Company.create(
                        name=candidate.company_id.strip().title() or "Unknown Company",
                        company_id=norm_comp,
                    )
                    self.company_repo.save(comp)
                    new_companies += 1
                    created_company_ids.add(comp.id)

                candidate.company_id = comp.id

                # 3. Deterministic Identity Matching
                # Look up existing contacts in this company
                existing_contacts = self.contact_repo.find_by_company(comp.id)
                existing: Optional[Contact] = None

                # Matching rule 1: Match by exact canonical key
                for ec in existing_contacts:
                    if ec.canonical_key == candidate.canonical_key:
                        existing = ec
                        break

                # Matching rule 2: Match by normalized phone overlap
                if not existing and candidate.phones:
                    candidate_phones_set = set(candidate.phones)
                    for ec in existing_contacts:
                        if set(ec.phones) & candidate_phones_set:
                            existing = ec
                            break

                # Matching rule 3: Match by normalized email overlap
                if not existing and candidate.emails:
                    candidate_emails_set = set(candidate.emails)
                    for ec in existing_contacts:
                        if set(ec.emails) & candidate_emails_set:
                            existing = ec
                            break

                if existing:
                    # Update contact fields if needed, preserving historical metadata
                    changed = False

                    # Merge new phone endpoints
                    existing_phones = list(existing.phones)
                    added_phones = 0
                    for p in candidate.phones:
                        if p not in existing_phones:
                            existing_phones.append(p)
                            added_phones += 1
                    if added_phones > 0:
                        existing.phone = ", ".join(existing_phones)
                        new_phone_endpoints += added_phones
                        changed = True

                    # Merge new email endpoints
                    existing_emails = list(existing.emails)
                    added_emails = 0
                    for e in candidate.emails:
                        if e not in existing_emails:
                            existing_emails.append(e)
                            added_emails += 1
                    if added_emails > 0:
                        existing.email = ", ".join(existing_emails)
                        new_email_endpoints += added_emails
                        changed = True

                    if candidate.name and candidate.name != "there" and existing.name != candidate.name:
                        existing.name = candidate.name
                        changed = True
                    if candidate.designation and existing.designation != candidate.designation:
                        existing.designation = candidate.designation
                        changed = True

                    existing.source_reference = src_rec

                    if changed:
                        existing.updated_at = now
                        updated_contacts += 1
                    else:
                        unchanged_contacts += 1

                    self.contact_repo.save(existing)
                else:
                    # Fresh new contact
                    candidate.source_reference = src_rec
                    candidate.created_at = now
                    candidate.updated_at = now
                    self.contact_repo.save(candidate)
                    new_contacts += 1
                    new_phone_endpoints += len(candidate.phones)
                    new_email_endpoints += len(candidate.emails)

            except Exception as exc:
                errors.append(f"Row {src_rec.source_row} error: {exc}")

        self.session.commit()

        return SyncSummary(
            total_read=total_read,
            new_contacts=new_contacts,
            updated_contacts=updated_contacts,
            unchanged_contacts=unchanged_contacts,
            new_companies=new_companies,
            updated_companies=updated_companies,
            new_phone_endpoints=new_phone_endpoints,
            new_email_endpoints=new_email_endpoints,
            skipped_invalid=skipped_invalid,
            history_preserved=True,
            errors=errors,
        )

    def _parse_source_rows(
        self, rows: List[SourceRow], source_filename: str
    ) -> Tuple[List[Tuple[Contact, SourceRecord]], int]:
        parsed: List[Tuple[Contact, SourceRecord]] = []
        invalid_count = 0

        if not rows:
            return parsed, invalid_count

        first_row_vals = rows[0].raw_values
        is_csv_header_dict = any("name" in k.lower() or "company" in k.lower() for k in first_row_vals.keys())

        if is_csv_header_dict:
            # CSV with named headers (e.g. company, name, email/phone)
            for srow in rows:
                v = srow.raw_values
                comp = clean_text(v.get("company") or v.get("Company") or "")
                name = clean_text(v.get("name") or v.get("Name") or "there")
                designation = clean_text(v.get("designation") or v.get("Designation") or v.get("role") or "")
                raw_phone = clean_text(v.get("phone") or v.get("Phone") or "")
                raw_email = clean_text(v.get("email") or v.get("Email") or "")

                if not comp and not name and not raw_phone and not raw_email:
                    invalid_count += 1
                    continue

                phones = extract_phone_numbers(raw_phone, self.default_country_code) if raw_phone else []
                emails = extract_emails(raw_email) if raw_email else []

                if raw_phone and not phones:
                    invalid_count += 1
                if raw_email and not emails:
                    invalid_count += 1

                if not phones and not emails:
                    invalid_count += 1
                    continue

                phone_str = ", ".join(phones) if phones else None
                email_str = ", ".join(emails) if emails else None

                cnt = Contact(
                    contact_id="",
                    company_id=comp or "unknown",
                    name=name or "there",
                    designation=designation,
                    phone=phone_str,
                    email=email_str,
                )
                parsed.append((cnt, srow.to_source_record()))
            return parsed, invalid_count

        # Excel letter column format (A, B, C, D...)
        is_4_col = False
        if rows:
            first_vals = [str(val).lower() for val in rows[0].raw_values.values()]
            if any("phone" in val for val in first_vals) or len(rows[0].raw_values) <= 4:
                is_4_col = True

        for srow in rows:
            if srow.source_row == 1:
                continue
            v = srow.raw_values
            company = clean_text(v.get("A"))
            if not company:
                invalid_count += 1
                continue

            if is_4_col:
                name = clean_text(v.get("B")) or "there"
                raw_phones = clean_text(v.get("C"))
                raw_emails = clean_text(v.get("D"))
                phones = extract_phone_numbers(raw_phones, self.default_country_code) if raw_phones else []
                emails = extract_emails(raw_emails) if raw_emails else []

                if raw_phones and not phones:
                    invalid_count += 1
                if raw_emails and not emails:
                    invalid_count += 1

                if not phones and not emails:
                    invalid_count += 1
                    continue

                phone_str = ", ".join(phones) if phones else None
                email_str = ", ".join(emails) if emails else None

                cnt = Contact(
                    contact_id="",
                    company_id=company,
                    name=name,
                    phone=phone_str,
                    email=email_str,
                )
                parsed.append((cnt, srow.to_source_record()))
            else:
                primary_name = clean_text(v.get("B"))
                email_val = clean_text(v.get("E"))
                emails = extract_emails(email_val) if email_val else []
                email_str = ", ".join(emails) if emails else None

                for name_col, number_cols, _ in CONTACT_SLOTS:
                    name = clean_text(v.get(name_col)) or primary_name or "there"
                    slot_phones: List[str] = []
                    for number_col in number_cols:
                        raw = clean_text(v.get(number_col))
                        if raw:
                            extracted = extract_phone_numbers(raw, self.default_country_code)
                            for num in extracted:
                                if num not in slot_phones:
                                    slot_phones.append(num)
                            if not extracted:
                                invalid_count += 1

                    if not slot_phones and not email_str:
                        continue

                    phone_str = ", ".join(slot_phones) if slot_phones else None

                    cnt = Contact(
                        contact_id="",
                        company_id=company,
                        name=name,
                        phone=phone_str,
                        email=email_str,
                    )
                    parsed.append((cnt, srow.to_source_record()))

        # Deduplicate identical contacts within the parsed list
        deduped: List[Tuple[Contact, SourceRecord]] = []
        seen: Set[str] = set()
        for cnt, src in parsed:
            k = cnt.canonical_key
            if k not in seen:
                seen.add(k)
                deduped.append((cnt, src))

        return deduped, invalid_count
