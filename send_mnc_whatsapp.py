#!/usr/bin/env python3
"""Clean the MNC sheet and send personalized WhatsApp messages.

The script is intentionally self-contained. Reading and cleaning .xlsx files
uses only Python's standard library. Playwright is needed only for --send:

    python3 -m pip install playwright
    python3 -m playwright install chromium

Examples:
    python3 send_mnc_whatsapp.py
    python3 send_mnc_whatsapp.py --send --limit 2
    python3 send_mnc_whatsapp.py --send

The default mode is safe: it writes mnc_cleaned_contacts.csv and previews the
messages without opening WhatsApp or sending anything.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

# Shared canonical domain & ingestion logic
from contact_ingestion import (
    CONTACT_SLOTS,
    DEFAULT_CLEAN_CSV,
    DEFAULT_LOG,
    DEFAULT_WORKBOOK,
    MESSAGE_TEMPLATE,
    Contact,
    clean_contacts,
    clean_text,
    column_name,
    extract_numbers,
    find_default_workbook,
    first_name,
    load_sent_keys,
    normalize_phone,
    read_mnc_rows,
    ready_contacts,
    render_message,
    write_clean_csv,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

import os

SESSION_DIR = ROOT / ".whatsapp_session"
DEFAULT_RESUME = Path(r"D:\Resume\Kaustubh.pdf")
RESUME_PATH = (
    Path(os.environ.get("RESUME_PATH"))
    if os.environ.get("RESUME_PATH")
    else (
        DEFAULT_RESUME
        if DEFAULT_RESUME.exists()
        else (ROOT / "Kaustubh.pdf" if (ROOT / "Kaustubh.pdf").exists() else DEFAULT_RESUME)
    )
)


def append_log(path: Path, rows: list[dict[str, str]]) -> None:
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        fields = ["time", "key", "status", "source_row", "company", "name", "phone", "detail"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def preview(contacts: list[Contact], ready: list[Contact], already_sent: int, limit: int | None) -> None:
    statuses: dict[str, int] = defaultdict(int)
    for contact in contacts:
        statuses[contact.status] += 1

    selected = ready[:limit] if limit is not None else ready
    print(f"Cleaned contact-number records: {len(contacts)}")
    print(f"Ready this run: {len(selected)} of {len(ready)}")
    print(f"Previously sent and skipped: {already_sent}")
    print(f"Duplicate records skipped: {statuses['duplicate_skipped']}")
    print(f"Unsafe duplicate conflicts blocked: {statuses['duplicate_conflict']}")
    print()

    for contact in selected:
        print(
            f"Row {contact.source_row}: {contact.name} @ {contact.company} "
            f"(+{contact.phone})"
        )
        print(render_message(contact).replace("\n", "\n  "))
        print()

    conflicts = [item for item in contacts if item.status == "duplicate_conflict"]
    if conflicts:
        print("Blocked conflicts (correct the workbook before sending these):")
        shown: set[str] = set()
        for contact in conflicts:
            if contact.phone not in shown:
                shown.add(contact.phone)
                print(f"  {contact.detail}")


def wait_for_login(page, timeout_ms: int) -> None:
    page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
    print("WhatsApp Web opened. Scan the QR code if requested.")
    page.locator(
        '#side, [data-testid="chat-list"], [aria-label="Search input textbox"]'
    ).first.wait_for(state="visible", timeout=timeout_ms)
    print("WhatsApp Web is ready.")


def send_one(page, contact: Contact, timeout_ms: int) -> tuple[str, str]:
    url = (
        "https://web.whatsapp.com/send?phone="
        + contact.phone
        + "&text="
        + urllib.parse.quote(render_message(contact))
    )
    page.goto(url, wait_until="domcontentloaded")

    invalid_text = page.get_by_text(
        re.compile(r"phone number shared via url is invalid", re.I)
    )
    send_button = page.locator(
        'button[aria-label="Send"], button[data-testid="compose-btn-send"]'
    )
    composer = page.locator(
        '[aria-label="Type a message"], [data-testid="conversation-compose-box-input"]'
    )

    # --- Step 1: Send the text message ---
    deadline = time.monotonic() + timeout_ms / 1000
    text_sent = False
    while time.monotonic() < deadline:
        if invalid_text.count() and invalid_text.first.is_visible():
            return "invalid_number", "WhatsApp rejected the number"
        if send_button.count() and send_button.first.is_visible():
            send_button.first.click()
            page.wait_for_timeout(1500)
            text_sent = True
            break
        if composer.count() and composer.first.is_visible():
            composer.first.press("Enter")
            page.wait_for_timeout(1500)
            text_sent = True
            break
        page.wait_for_timeout(500)

    if not text_sent:
        return "failed", "message composer did not become ready before timeout"

    # --- Step 2: Attach and send the resume PDF ---
    if not RESUME_PATH.exists():
        return "sent_text_only", f"Resume file not found at {RESUME_PATH}"

    try:
        # Click the attach button (WhatsApp uses a plus icon or paperclip)
        attach_btn = page.locator(
            'button[aria-label="Attach"], [title="Attach"], span[data-icon="plus-rounded"], span[data-icon="clip"], span[data-icon="plus"]'
        ).first
        attach_btn.wait_for(state="visible", timeout=10000)
        attach_btn.click()

        # Select the Document menu item and supply the file via file chooser
        doc_item = page.locator(
            'button[role="menuitem"][aria-label="Document"], [role="menuitem"]:has-text("Document"), button:has-text("Document")'
        ).first
        doc_item.wait_for(state="visible", timeout=7000)

        with page.expect_file_chooser(timeout=7000) as fc_info:
            doc_item.click()
        file_chooser = fc_info.value
        file_chooser.set_files(str(RESUME_PATH))

        # Allow WhatsApp preview composer to load and render the PDF preview
        page.wait_for_timeout(1500)

        # Locate the Send button inside the PDF preview composer
        send_btn = page.locator(
            'div[role="button"][aria-label^="Send"], div[role="button"][aria-label="Send"], span[data-icon="wds-ic-send-filled"], [data-testid="send"]'
        ).last
        send_btn.wait_for(state="visible", timeout=15000)
        send_btn.click()

        # Fallback press enter in case click needs activation
        page.keyboard.press("Enter")

        # Send verification: the preview composer/send button must close/disappear
        send_btn.wait_for(state="hidden", timeout=15000)

        # Wait for media upload to finish and chat composer to restore
        page.wait_for_timeout(2500)
        composer.first.wait_for(state="visible", timeout=10000)

    except Exception as exc:
        return "sent_text_only", f"Resume attach failed: {exc}"

    return "sent", ""


def send_messages(
    contacts: list[Contact],
    log_path: Path,
    delay: float,
    timeout_seconds: int,
    headless: bool,
) -> None:
    try:
        from playwright.sync_api import sync_playwright  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required for --send.\n"
            "Install it with:\n"
            "  python3 -m pip install playwright\n"
            "  python3 -m playwright install chromium"
        ) from exc

    timeout_ms = timeout_seconds * 1000
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(SESSION_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        wait_for_login(page, timeout_ms)

        for position, contact in enumerate(contacts, start=1):
            print(
                f"[{position}/{len(contacts)}] Sending to "
                f"{contact.name} @ {contact.company} (+{contact.phone})..."
            )
            try:
                status, detail = send_one(page, contact, timeout_ms)
            except Exception as exc:  # Keep the batch moving and record the error.
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
                        "phone": contact.phone,
                        "detail": detail,
                    }
                ],
            )
            print(f"  {status}{': ' + detail if detail else ''}")
            if position < len(contacts):
                time.sleep(delay)
        page.wait_for_timeout(3000)
        context.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean the MNC sheet and send personalized WhatsApp messages."
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--clean-csv", type=Path, default=DEFAULT_CLEAN_CSV)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--country-code", default="91")
    parser.add_argument("--limit", type=int, help="process only the first N ready contacts")
    parser.add_argument("--send", action="store_true", help="actually send messages")
    parser.add_argument("--force-resend", action="store_true", help="include contacts already logged as sent")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between messages")
    parser.add_argument("--timeout", type=int, default=90, help="seconds allowed per page/login")
    parser.add_argument("--headless", action="store_true", help="run browser invisibly (not suitable for first login)")
    parser.add_argument("--phone",   type=str, default=None, help="send to this number only, e.g. 919XXXXXXXXX")
    parser.add_argument("--name",    type=str, default="there", help="contact name when using --phone")
    parser.add_argument("--company", type=str, default="your company", help="company name when using --phone")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if not re.fullmatch(r"\d{1,3}", args.country_code):
        parser.error("--country-code must contain 1 to 3 digits")
    return args


def main() -> int:
    args = parse_args()

    # --- Single number test mode ---
    if args.phone:
        phone = re.sub(r"\D", "", args.phone)  # strip +, spaces, dashes
        if not (10 <= len(phone) <= 15):
            print(f"Invalid phone number: {args.phone}")
            return 1
        test_contact = Contact(
            source_row=0,
            company=args.company,
            name=args.name,
            phone=phone,
            email="",
            raw_number=args.phone,
            source_columns="manual",
            slot="manual",
        )
        print("--- Message Preview ---")
        print(render_message(test_contact))
        print("----------------------")
        if not args.send:
            print("\nDry run. Add --send to actually send.")
            return 0
        send_messages([test_contact], args.log.resolve(), args.delay, args.timeout, args.headless)
        return 0

    # --- Full list mode ---
    contacts = clean_contacts(
        read_mnc_rows(args.workbook.resolve()), args.country_code
    )
    write_clean_csv(args.clean_csv.resolve(), contacts)
    sent_keys = load_sent_keys(args.log.resolve())
    ready, already_sent = ready_contacts(contacts, sent_keys, args.force_resend)
    selected = ready[: args.limit] if args.limit is not None else ready

    print(f"Cleaned data written to: {args.clean_csv.resolve()}")
    preview(contacts, ready, already_sent, args.limit)

    if not args.send:
        print("Dry run only. Add --send to open WhatsApp and send these messages.")
        return 0
    if not selected:
        print("There are no ready contacts to send.")
        return 0

    print(f"Starting automatic send for {len(selected)} contact(s).")
    send_messages(
        selected,
        args.log.resolve(),
        args.delay,
        args.timeout,
        args.headless,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user. Successfully sent contacts remain in the log.")
        raise SystemExit(130)
