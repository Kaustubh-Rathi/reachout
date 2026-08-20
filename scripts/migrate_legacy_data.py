"""Legacy Data Migration Pipeline.

Migrates existing contacts from Excel/CSV workbooks, WhatsApp send logs,
and CRM JSON databases into SQLite with full historical fidelity, idempotency,
and relational integrity.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select

from app.domain.company import normalize_company_name
from app.domain.enums import CRMOutcome
from app.infrastructure.database import SessionFactory, init_db
from app.infrastructure.models import (
    CompanyModel,
    ContactModel,
    MessageTemplateModel,
    OutreachAttemptModel,
    SenderAccountModel,
    SourceRecordModel,
)
from app.infrastructure.source.excel_reader import TabularSourceReader, clean_text
from scripts.backup_source_data import create_source_backup

DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"

LEGACY_CRM_STATUS_MAP = {
    "No Status": CRMOutcome.NONE.value,
    "Pending Reply": CRMOutcome.PENDING_REPLY.value,
    "Replied - Interested": CRMOutcome.INTERESTED.value,
    "Replied - No Openings": CRMOutcome.REPLIED_NO_OPENINGS.value,
    "Referral Given": CRMOutcome.REFERRAL_GIVEN.value,
    "Not Hiring Freshers": CRMOutcome.NOT_HIRING_FRESHERS.value,
    "Ghosted / No Reply": CRMOutcome.GHOSTED.value,
    "Got Reply": CRMOutcome.PENDING_REPLY.value,
    "Follow-up Needed": CRMOutcome.PENDING_REPLY.value,
    "Interview Scheduled": CRMOutcome.INTERESTED.value,
    "Call Scheduled": CRMOutcome.INTERESTED.value,
}


def map_legacy_crm_status(raw_status: Optional[str]) -> str:
    """Map legacy human-readable CRM status string to domain CRMOutcome value."""
    if not raw_status:
        return CRMOutcome.NONE.value
    clean = raw_status.strip()
    return LEGACY_CRM_STATUS_MAP.get(clean, CRMOutcome.NONE.value)


def parse_iso_datetime(val: Optional[str]) -> datetime:
    if not val:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(val.strip())
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def migrate_all(target_db_session=None) -> Dict[str, Any]:
    """Execute complete idempotent legacy data migration."""
    print("=" * 60)
    print("[*] Starting Legacy Data Migration...")
    print("=" * 60)

    # 1. Ensure source data is backed up before running
    print("[1/5] Creating pre-migration safety backup...")
    backup_dir = create_source_backup("pre_migration_snapshot")
    print(f"      Safety backup secured at: {backup_dir}")

    # 2. Initialize Database Tables
    init_db()

    session_cm = target_db_session or SessionFactory()
    session = session_cm if hasattr(session_cm, "add") else session_cm.__enter__()

    stats = {
        "companies_upserted": 0,
        "contacts_upserted": 0,
        "whatsapp_attempts_migrated": 0,
        "email_attempts_migrated": 0,
        "crm_records_merged": 0,
        "templates_seeded": 0,
        "senders_seeded": 0,
    }

    try:
        # 3. Seed Default Templates & Sender Accounts
        print("[2/5] Seeding Default Templates & Senders...")
        wa_body = (
            "Hey {first_name},\n\n"
            "I'm Kaustubh Rathi, B.Tech 2026 grad from IIIT Allahabad (CGPA 8.52). "
            "Recently completed my SDE Internship at Amazon (Grocery Ordering team). "
            "I have strong experience in AI/ML, statistics, and competitive programming (Codeforces 1751, JEE AIR 4800).\n\n"
            "Would love to explore opportunities at *{company}*. Please find my resume attached.\n\n"
            "Portfolio: https://kaustubh-rathi.github.io/Kaustubh_Rathi_Portfolio/\n"
            "LinkedIn: https://www.linkedin.com/in/kaustubh-rathi-9228ab255\n"
            "GitHub: https://github.com/Kaustubh-Rathi\n\n"
            "Thank you for your time!"
        )
        wa_tmpl = session.get(MessageTemplateModel, "tmpl_wa_default")
        if not wa_tmpl:
            wa_tmpl = MessageTemplateModel(
                id="tmpl_wa_default",
                name="Default SDE Outreach (WhatsApp)",
                channel="WHATSAPP",
                body=wa_body,
                attachment_ref=r"D:\Resume\Kaustubh.pdf",
            )
            session.add(wa_tmpl)
            stats["templates_seeded"] += 1

        email_body = (
            "Hi {first_name},\n\n"
            "I hope you're doing well. I'm reaching out regarding job opportunities at {company}.\n\n"
            "Would you be open to a quick chat, or a referral if there's a relevant opening on your team?\n\n"
            "Thank you!"
        )
        email_tmpl = session.get(MessageTemplateModel, "tmpl_email_default")
        if not email_tmpl:
            email_tmpl = MessageTemplateModel(
                id="tmpl_email_default",
                name="Default Job Outreach (Email)",
                channel="EMAIL",
                subject="Exploring opportunities at {company}",
                body=email_body,
                attachment_ref=r"D:\Resume\Kaustubh.pdf",
            )
            session.add(email_tmpl)
            stats["templates_seeded"] += 1

        wa_sender = session.get(SenderAccountModel, "snd_wa_primary")
        if not wa_sender:
            wa_sender = SenderAccountModel(
                id="snd_wa_primary",
                channel="WHATSAPP",
                provider="playwright_web",
                identity="Primary WhatsApp Account",
                display_name="Primary SIM / WhatsApp Web",
                status="ACTIVE",
                session_ref=".sessions/whatsapp/snd_wa_primary",
                daily_limit=200,
                hourly_limit=30,
            )
            session.add(wa_sender)
            stats["senders_seeded"] += 1

        email_sender = session.get(SenderAccountModel, "snd_email_primary")
        if not email_sender:
            email_sender = SenderAccountModel(
                id="snd_email_primary",
                channel="EMAIL",
                provider="smtp",
                identity="you@gmail.com",
                display_name="Primary Outreach Gmail",
                status="ACTIVE",
                credential_ref="EMAIL_USER",
                daily_limit=500,
                hourly_limit=50,
            )
            session.add(email_sender)
            stats["senders_seeded"] += 1

        session.flush()

        # Cache existing contacts & companies from DB
        existing_companies = {c.id: c for c in session.scalars(select(CompanyModel)).all()}
        existing_contacts_by_key = {}
        for cnt in session.scalars(select(ContactModel)).all():
            k = f"{cnt.company_id}|{cnt.phone}" if cnt.phone else f"{cnt.company_id}|{cnt.email}"
            existing_contacts_by_key[k] = cnt

        # 4. Ingest Primary Source Workbook
        print("[3/5] Ingesting Contacts from Source Workbook...")
        reader = TabularSourceReader()
        workbook_candidates = [
            DATA_DIR / "MNC_Final.xlsx",
            DATA_DIR / "Reachout.xlsx",
            DATA_DIR / "mnc_cleaned_contacts.csv",
        ]
        source_file = next((wb for wb in workbook_candidates if wb.exists()), None)
        if not source_file:
            raise FileNotFoundError("No source workbook or CSV found in data directory.")

        from contact_ingestion import clean_contacts, read_mnc_rows

        if source_file.suffix.lower() in (".xlsx", ".xlsm"):
            sheet_tuples = read_mnc_rows(source_file)
            cleaned_contacts = clean_contacts(sheet_tuples, "91")

            for c in cleaned_contacts:
                if c.status == "duplicate_skipped":
                    continue

                norm_comp = normalize_company_name(c.company)
                if norm_comp not in existing_companies:
                    comp_model = CompanyModel(
                        id=norm_comp,
                        name=c.company.strip() or "Unknown",
                        normalized_name=norm_comp,
                    )
                    session.add(comp_model)
                    existing_companies[norm_comp] = comp_model
                    stats["companies_upserted"] += 1

                k = f"{norm_comp}|{c.phone}"
                existing_cnt = existing_contacts_by_key.get(k)
                if not existing_cnt:
                    cnt_id = f"cnt_{len(existing_contacts_by_key) + 1:04d}"
                    existing_cnt = ContactModel(
                        contact_id=cnt_id,
                        company_id=norm_comp,
                        name=c.name,
                        phone=c.phone,
                        email=c.email if c.email else None,
                        notes="",
                        tags_json="[]",
                    )
                    session.add(existing_cnt)
                    session.flush()
                    existing_contacts_by_key[k] = existing_cnt
                    stats["contacts_upserted"] += 1
                else:
                    # Update name/email if missing
                    if c.email and not existing_cnt.email:
                        existing_cnt.email = c.email

                # Add SourceRecord if not exists
                src_rec = session.get(SourceRecordModel, f"src_{existing_cnt.contact_id}")
                if not src_rec:
                    src_rec = SourceRecordModel(
                        id=f"src_{existing_cnt.contact_id}",
                        contact_id=existing_cnt.contact_id,
                        source_file=source_file.name,
                        source_sheet="MNC_Cleaned",
                        source_row=c.source_row,
                        source_fingerprint=f"fp_{source_file.name}_{c.source_row}_{c.phone}",
                        first_seen_at=datetime.now(timezone.utc),
                        last_seen_at=datetime.now(timezone.utc),
                    )
                    session.add(src_rec)

        # 5. Ingest WhatsApp Send Logs
        print("[4/5] Migrating Historical WhatsApp Send Logs...")
        wa_log_path = LOGS_DIR / "mnc_whatsapp_send_log.csv"
        existing_attempt_keys = {
            a.idempotency_key for a in session.scalars(select(OutreachAttemptModel)).all()
        }

        if wa_log_path.exists():
            with wa_log_path.open(newline="", encoding="utf-8") as f:
                reader_log = csv.DictReader(f)
                for idx, row in enumerate(reader_log, start=1):
                    key = row.get("key", "").strip()
                    if not key:
                        continue

                    parts = key.split("|")
                    comp_name = parts[0].strip().lower()
                    phone = parts[1].strip() if len(parts) > 1 else ""

                    contact_match = existing_contacts_by_key.get(key)
                    if not contact_match:
                        norm_c = normalize_company_name(comp_name)
                        if norm_c not in existing_companies:
                            comp_model = CompanyModel(
                                id=norm_c,
                                name=row.get("company") or comp_name.title(),
                                normalized_name=norm_c,
                            )
                            session.add(comp_model)
                            existing_companies[norm_c] = comp_model
                            stats["companies_upserted"] += 1

                        cnt_id = f"cnt_log_{len(existing_contacts_by_key) + 1:04d}"
                        contact_match = ContactModel(
                            contact_id=cnt_id,
                            company_id=norm_c,
                            name=row.get("name") or "there",
                            phone=phone,
                            email=None,
                        )
                        session.add(contact_match)
                        session.flush()
                        existing_contacts_by_key[key] = contact_match
                        stats["contacts_upserted"] += 1

                    log_time = parse_iso_datetime(row.get("time"))
                    raw_status = row.get("status", "").strip().lower()

                    if raw_status in ("sent", "sent_text_only"):
                        attempt_status = "SENT"
                        contact_match.last_whatsapp_at = log_time
                        contact_match.last_activity_at = log_time
                    elif raw_status in ("failed", "invalid_number"):
                        attempt_status = "FAILED"
                    else:
                        attempt_status = "UNKNOWN"

                    idemp = f"hist_wa_{contact_match.contact_id}_{idx}_{int(log_time.timestamp())}"
                    if idemp not in existing_attempt_keys:
                        att_model = OutreachAttemptModel(
                            id=f"att_wa_hist_{idx:05d}",
                            contact_id=contact_match.contact_id,
                            sender_account_id="snd_wa_primary",
                            channel="WHATSAPP",
                            attempt_type="AUTOMATIC",
                            status=attempt_status,
                            idempotency_key=idemp,
                            message_body_snapshot=wa_body,
                            attachment_snapshot=r"D:\Resume\Kaustubh.pdf",
                            prepared_at=log_time,
                            started_at=log_time,
                            completed_at=log_time,
                            failure_code="ERR_LOG_FAILED" if attempt_status == "FAILED" else None,
                            failure_detail=row.get("detail") or "",
                        )
                        session.add(att_model)
                        existing_attempt_keys.add(idemp)
                        stats["whatsapp_attempts_migrated"] += 1

        # 6. Ingest CRM Metadata from crm_data.json
        print("[5/5] Merging CRM Metadata and Outcomes...")
        crm_json_path = DATA_DIR / "crm_data.json"
        if crm_json_path.exists():
            try:
                with crm_json_path.open("r", encoding="utf-8") as f:
                    crm_db = json.load(f)
                    for k, data in crm_db.items():
                        contact_match = existing_contacts_by_key.get(k)
                        if contact_match:
                            contact_match.crm_outcome = map_legacy_crm_status(data.get("crm_status"))
                            contact_match.notes = data.get("notes") or ""
                            contact_match.tags_json = json.dumps(data.get("tags") or [])
                            if data.get("updated_at"):
                                contact_match.updated_at = parse_iso_datetime(data.get("updated_at"))
                            stats["crm_records_merged"] += 1
            except Exception as e:
                print(f"[Warning] Failed to read crm_data.json: {e}")

        session.commit()
        print("\n" + "=" * 60)
        print("[+] Migration COMPLETED Successfully!")
        print("=" * 60)
        for k, v in stats.items():
            print(f"  - {k}: {v}")
        return stats

    except Exception as exc:
        session.rollback()
        print(f"\n[!] Migration FAILED: {exc}")
        raise
    finally:
        if hasattr(session_cm, "close"):
            session_cm.close()


if __name__ == "__main__":
    migrate_all()
