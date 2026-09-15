"""Rebuild the Reachout database from scratch.

1. Delete the existing reachout.db (after backing it up).
2. Recreate the schema from the corrected models via init_db().
3. Sync contacts from the source Excel workbook.
4. Replay logs/mnc_whatsapp_send_log.csv to record WhatsApp outreach attempts
   (status sent/sent_text_only -> SENT, failed -> FAILED) and set last_whatsapp_at.

Run:  python scripts/rebuild_db.py
"""

from __future__ import annotations

import csv
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "reachout.db"
LOG_PATH = ROOT / "logs" / "mnc_whatsapp_send_log.csv"
SOURCE = DATA_DIR / "MNC_Final.xlsx"
SHEET = "MNC_Cleaned"


def parse_ts(raw: str) -> datetime:
    """Parse log timestamp (naive ISO) as UTC-aware."""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def main() -> None:
    if not SOURCE.exists():
        print(f"[rebuild] Missing source workbook: {SOURCE}")
        sys.exit(1)
    if not LOG_PATH.exists():
        print(f"[rebuild] Missing log: {LOG_PATH}")
        sys.exit(1)

    # 1. Backup + delete old DB
    if DB_PATH.exists():
        backup_dir = DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                shutil.copy2(p, backup_dir / f"reachout_pre_rebuild_{stamp}{suffix}")
        print(f"[rebuild] Backed up old DB to {backup_dir}")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink()

    sys.path.insert(0, str(ROOT))
    from app.domain.enums import AttemptType, Channel, OutreachStatus
    from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
    from app.infrastructure.database import SessionFactory, init_db
    from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
    from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
    from app.infrastructure.source.synchronizer import DatabaseSourceSynchronizer

    # 2. Create schema from corrected models
    init_db()
    print("[rebuild] Schema created.")

    # 3. Sync contacts from Excel
    with SessionFactory() as session:
        syncer = DatabaseSourceSynchronizer(session)
        summary = syncer.sync_source(str(SOURCE), SHEET)
        print(
            f"[rebuild] Sync done: new={summary.new_contacts} updated={summary.updated_contacts} "
            f"companies={summary.new_companies} phones={summary.new_phone_endpoints} emails={summary.new_email_endpoints} "
            f"skipped={summary.skipped_invalid} errors={len(summary.errors)}"
        )
        for e in summary.errors[:20]:
            print(f"    ! {e}")

        contact_repo = SqliteContactRepository(session)
        outreach_repo = SqliteOutreachRepository(session)

        # Build phone -> contact index
        phone_index = {}
        for c in contact_repo.list_all():
            for p in c.phones:
                phone_index.setdefault(p, c)

        # 4. Replay log
        created = 0
        matched = 0
        unmatched = 0
        last_seen = {}
        with LOG_PATH.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                phone = (row.get("phone") or "").strip()
                status_raw = (row.get("status") or "").strip().lower()
                ts = parse_ts(row.get("time"))
                if not phone:
                    unmatched += 1
                    continue

                contact = phone_index.get(phone)
                if contact is None:
                    # Try matching after stripping non-digits
                    digits = "".join(ch for ch in phone if ch.isdigit())
                    contact = phone_index.get(digits)
                if contact is None:
                    unmatched += 1
                    continue

                matched += 1
                sent = status_raw in ("sent", "sent_text_only")
                st = OutreachStatus.SENT if sent else OutreachStatus.FAILED
                salt = hashlib_hex(f"{phone}|{row.get('time')}|{status_raw}")
                key = generate_idempotency_key(
                    contact_id=contact.contact_id,
                    channel=Channel.WHATSAPP,
                    attempt_type=AttemptType.MANUAL,
                    destination=phone,
                    custom_salt=salt,
                )
                attempt = OutreachAttempt(
                    id=f"att_wa_{uuid.uuid4().hex[:14]}",
                    contact_id=contact.contact_id,
                    sender_account_id=None,
                    channel=Channel.WHATSAPP,
                    attempt_type=AttemptType.MANUAL,
                    status=st,
                    idempotency_key=key,
                    message_body_snapshot="",
                    destination=phone,
                    prepared_at=ts,
                    started_at=ts,
                    completed_at=ts,
                    failure_code=None if sent else "LOG_FAILED",
                    failure_detail=None if sent else (row.get("detail") or "Logged send failure"),
                )
                outreach_repo.save(attempt)
                created += 1
                if contact.contact_id not in last_seen or ts > last_seen[contact.contact_id]:
                    last_seen[contact.contact_id] = ts

        # Set last_whatsapp_at on matched contacts
        updated_contacts = 0
        for cid, ts in last_seen.items():
            c = contact_repo.get_by_id(cid)
            if c:
                c.last_whatsapp_at = ts
                c.last_activity_at = ts
                contact_repo.save(c)
                updated_contacts += 1

        session.commit()
        print(
            f"[rebuild] Log replay: matched={matched} attempts_created={created} "
            f"unmatched={unmatched} contacts_touched={updated_contacts}"
        )


def hashlib_hex(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


if __name__ == "__main__":
    main()
