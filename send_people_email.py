#!/usr/bin/env python3
"""Send personalized job-outreach emails from contacts_people.csv.

Uses only the Python standard library. Rows without a usable email are skipped.

Setup (Gmail example — use an App Password, not your normal password):
    export EMAIL_USER="you@gmail.com"
    export EMAIL_PASSWORD="your-16-char-app-password"
    # optional overrides:
    # export SMTP_HOST="smtp.gmail.com"
    # export SMTP_PORT="587"
    # export EMAIL_FROM="you@gmail.com"   # defaults to EMAIL_USER

Examples:
    python3 send_people_email.py
    python3 send_people_email.py --send --limit 2 --test-to you@gmail.com
    python3 send_people_email.py --send

Default mode is safe: it writes people_email_cleaned.csv and previews messages
without sending anything.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import smtplib
import ssl
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT / "contacts_people.csv"
DEFAULT_CLEAN_CSV = ROOT / "people_email_cleaned.csv"
DEFAULT_LOG = ROOT / "people_email_send_log.csv"

SUBJECT_TEMPLATE = "Exploring opportunities at {company}"
BODY_TEMPLATE = """Hi {first_name},

I hope you're doing well. I'm reaching out regarding job opportunities at {company}.

Would you be open to a quick chat, or a referral if there's a relevant opening on your team?

Thank you!
"""

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class Contact:
    source_row: int
    company: str
    name: str
    email: str
    raw_email: str
    source_section: str
    status: str = "ready"
    detail: str = ""

    @property
    def key(self) -> str:
        return self.email.casefold()


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def first_name(full_name: str) -> str:
    name = re.sub(r"\([^)]*\)", "", clean_text(full_name))
    name = re.sub(r"^(?:mr|mrs|ms|dr)\.?\s+", "", name, flags=re.I)
    if not name or name.casefold() in {"there", "na", "n/a", "check with sir"}:
        return "there"
    return name.split()[0].title()


def extract_emails(raw: str) -> list[str]:
    value = clean_text(raw)
    if not value:
        return []
    found: list[str] = []
    for chunk in re.split(r"\s*(?:,|;|/|\||\n|\band\b)\s*", value, flags=re.I):
        email = chunk.strip().strip("<>").strip()
        if not email:
            continue
        if EMAIL_RE.match(email) and email.casefold() not in {
            item.casefold() for item in found
        }:
            found.append(email)
    return found


def load_contacts(path: Path) -> list[Contact]:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")

    contacts: list[Contact] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"company", "name", "email"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise SystemExit(
                f"{path} must contain columns: company, name, email"
            )

        for csv_row, row in enumerate(reader, start=2):
            company = clean_text(row.get("company"))
            name = clean_text(row.get("name")) or "there"
            raw_email = clean_text(row.get("email"))
            section = clean_text(row.get("source_section"))

            if not raw_email:
                contacts.append(
                    Contact(
                        source_row=csv_row,
                        company=company,
                        name=name,
                        email="",
                        raw_email="",
                        source_section=section,
                        status="skipped_no_email",
                        detail="empty email cell",
                    )
                )
                continue

            emails = extract_emails(raw_email)
            if not emails:
                contacts.append(
                    Contact(
                        source_row=csv_row,
                        company=company,
                        name=name,
                        email="",
                        raw_email=raw_email,
                        source_section=section,
                        status="skipped_invalid_email",
                        detail=f"unusable email: {raw_email}",
                    )
                )
                continue

            for email in emails:
                contacts.append(
                    Contact(
                        source_row=csv_row,
                        company=company or "your company",
                        name=name,
                        email=email,
                        raw_email=raw_email,
                        source_section=section,
                    )
                )

    # One message per mailbox. Prefer the first ready row for that address.
    by_email: dict[str, list[int]] = defaultdict(list)
    for index, contact in enumerate(contacts):
        if contact.email:
            by_email[contact.email.casefold()].append(index)

    cleaned = list(contacts)
    for indexes in by_email.values():
        if len(indexes) < 2:
            continue
        first = contacts[indexes[0]]
        for index in indexes[1:]:
            other = contacts[index]
            cleaned[index] = replace(
                other,
                status="duplicate_skipped",
                detail=(
                    f"same email already queued from row {first.source_row} "
                    f"({first.name} @ {first.company})"
                ),
            )
    return cleaned


def write_clean_csv(path: Path, contacts: list[Contact]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source_row",
                "company",
                "name",
                "email",
                "raw_email",
                "source_section",
                "status",
                "detail",
            ],
        )
        writer.writeheader()
        for contact in contacts:
            writer.writerow(contact.__dict__)


def render_subject(contact: Contact) -> str:
    return SUBJECT_TEMPLATE.format(company=contact.company)


def render_body(contact: Contact) -> str:
    return BODY_TEMPLATE.format(
        first_name=first_name(contact.name),
        name=contact.name,
        company=contact.company,
    )


def load_sent_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (row.get("key") or "").casefold()
            for row in csv.DictReader(handle)
            if row.get("status") == "sent"
        }


def append_log(path: Path, rows: list[dict[str, str]]) -> None:
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        fields = [
            "time",
            "key",
            "status",
            "source_row",
            "company",
            "name",
            "email",
            "detail",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def ready_contacts(
    contacts: list[Contact], sent_keys: set[str], force_resend: bool
) -> tuple[list[Contact], int]:
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


def preview(
    contacts: list[Contact],
    ready: list[Contact],
    already_sent: int,
    limit: int | None,
    test_to: str | None,
) -> None:
    statuses: dict[str, int] = defaultdict(int)
    for contact in contacts:
        statuses[contact.status] += 1

    selected = ready[:limit] if limit is not None else ready
    print(f"Cleaned contact records: {len(contacts)}")
    print(f"Ready this run: {len(selected)} of {len(ready)}")
    print(f"Previously sent and skipped: {already_sent}")
    print(f"Skipped (no email): {statuses['skipped_no_email']}")
    print(f"Skipped (invalid email): {statuses['skipped_invalid_email']}")
    print(f"Duplicate emails skipped: {statuses['duplicate_skipped']}")
    if test_to:
        print(f"TEST MODE: every message will be redirected to {test_to}")
    print()

    for contact in selected:
        destination = test_to or contact.email
        print(
            f"Row {contact.source_row}: {contact.name} @ {contact.company} "
            f"<{contact.email}>"
            + (f"  -> test inbox {destination}" if test_to else "")
        )
        print(f"  Subject: {render_subject(contact)}")
        print("  " + render_body(contact).replace("\n", "\n  "))
        print()


def smtp_settings(args: argparse.Namespace) -> dict[str, str | int]:
    user = args.email_user or os.environ.get("EMAIL_USER", "").strip()
    password = args.email_password or os.environ.get("EMAIL_PASSWORD", "").strip()
    mail_from = (
        args.email_from
        or os.environ.get("EMAIL_FROM", "").strip()
        or user
    )
    host = args.smtp_host or os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    port_raw = args.smtp_port or os.environ.get("SMTP_PORT", "587").strip()

    if not user or not password:
        raise SystemExit(
            "Missing SMTP credentials.\n"
            "Set them before --send, for example:\n"
            '  export EMAIL_USER="you@gmail.com"\n'
            '  export EMAIL_PASSWORD="your-app-password"\n'
            "Or pass --email-user / --email-password."
        )
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid SMTP port: {port_raw}") from exc

    return {
        "user": user,
        "password": password,
        "mail_from": mail_from,
        "host": host,
        "port": port,
    }


def build_message(
    contact: Contact,
    mail_from: str,
    test_to: str | None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = render_subject(contact)
    message["From"] = mail_from
    message["To"] = test_to or contact.email
    if test_to:
        message["X-Original-To"] = contact.email
    message.set_content(render_body(contact))
    return message


def send_messages(
    contacts: list[Contact],
    settings: dict[str, str | int],
    log_path: Path,
    delay: float,
    test_to: str | None,
) -> None:
    host = str(settings["host"])
    port = int(settings["port"])
    user = str(settings["user"])
    password = str(settings["password"])
    mail_from = str(settings["mail_from"])
    context = ssl.create_default_context()

    with smtplib.SMTP(host, port, timeout=60) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(user, password)

        for position, contact in enumerate(contacts, start=1):
            destination = test_to or contact.email
            print(
                f"[{position}/{len(contacts)}] Sending to "
                f"{contact.name} @ {contact.company} <{contact.email}>"
                + (f" via test inbox {destination}" if test_to else "")
                + "..."
            )
            try:
                server.send_message(
                    build_message(contact, mail_from, test_to)
                )
                status, detail = "sent", "" if not test_to else f"redirected to {test_to}"
            except Exception as exc:  # Keep the batch moving; log the failure.
                status, detail = "failed", str(exc)

            append_log(
                log_path,
                [
                    {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "key": contact.key,
                        "status": status,
                        "source_row": str(contact.source_row),
                        "company": contact.company,
                        "name": contact.name,
                        "email": contact.email,
                        "detail": detail,
                    }
                ],
            )
            print(f"  {status}{': ' + detail if detail else ''}")
            if position < len(contacts) and delay > 0:
                time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send personalized emails from contacts_people.csv."
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--clean-csv", type=Path, default=DEFAULT_CLEAN_CSV)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--limit", type=int, help="send only the first N ready contacts")
    parser.add_argument("--send", action="store_true", help="actually send emails")
    parser.add_argument(
        "--force-resend",
        action="store_true",
        help="include addresses already logged as sent",
    )
    parser.add_argument(
        "--test-to",
        help="redirect every message to this inbox (safe end-to-end test)",
    )
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between emails")
    parser.add_argument("--email-user", help="SMTP username / login email")
    parser.add_argument("--email-password", help="SMTP password or app password")
    parser.add_argument("--email-from", help="From: address (defaults to email-user)")
    parser.add_argument("--smtp-host", help="SMTP host (default smtp.gmail.com)")
    parser.add_argument("--smtp-port", help="SMTP port (default 587)")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if args.test_to and not EMAIL_RE.match(args.test_to.strip()):
        parser.error("--test-to must be a valid email address")
    return args


def main() -> int:
    args = parse_args()
    contacts = load_contacts(args.csv.resolve())
    write_clean_csv(args.clean_csv.resolve(), contacts)
    sent_keys = load_sent_keys(args.log.resolve())
    ready, already_sent = ready_contacts(contacts, sent_keys, args.force_resend)
    selected = ready[: args.limit] if args.limit is not None else ready
    test_to = args.test_to.strip() if args.test_to else None

    print(f"Cleaned data written to: {args.clean_csv.resolve()}")
    preview(contacts, ready, already_sent, args.limit, test_to)

    if not args.send:
        print("Dry run only. Add --send to deliver these emails over SMTP.")
        return 0
    if not selected:
        print("There are no ready contacts to email.")
        return 0

    settings = smtp_settings(args)
    print(
        f"Starting SMTP send via {settings['host']}:{settings['port']} "
        f"as {settings['mail_from']} for {len(selected)} contact(s)."
    )
    send_messages(selected, settings, args.log.resolve(), args.delay, test_to)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user. Successfully sent emails remain in the log.")
        raise SystemExit(130)
