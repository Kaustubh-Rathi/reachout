"""Verification and Ingestion Script for 'good one ' contacts.

Reads contacts from Reachout_downloaded.xlsx ('good one ' sheet), verifies each
corporate email using Hunter.io's Email Verifier API, filters out undeliverable
or deactivated mailboxes, consolidates duplicates, and safely imports verified
recruiters into reachout.db with company profiles, phone endpoints, and source lineage.
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from app.config import DEFAULT_COUNTRY_CODE
from app.domain.company import Company, normalize_company_name
from app.domain.contact import Contact
from app.domain.source_record import SourceRecord, compute_source_fingerprint
from app.infrastructure.database import SessionFactory
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
from app.infrastructure.source.synchronizer import normalize_phone_number

# Paths
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = WORKSPACE_DIR / "data"
EXCEL_PATH = DATA_DIR / "Reachout_downloaded.xlsx"
CACHE_PATH = DATA_DIR / "hunter_verification_cache.json"
REPORT_JSON_PATH = DATA_DIR / "good_one_verification_report.json"
OUTPUT_CSV_PATH = DATA_DIR / "good_one_verified_contacts.csv"

# Load .env manually if not in os.environ
ENV_FILE = WORKSPACE_DIR / ".env"
if ENV_FILE.exists():
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")

# Company normalization map for variations in the sheet
COMPANY_CANONICAL_MAP = {
    "alphonso inc.": "Alphonso",
    "alphonso": "Alphonso",
    "apple inc": "Apple",
    "apple inc.": "Apple",
    "cisco mena": "Cisco",
    "cisco systems india pvt. ltd.": "Cisco",
    "cisco": "Cisco",
    "microsoft - mena": "Microsoft",
    "microsoft corporation": "Microsoft",
    "microsoft": "Microsoft",
    "arista networks": "Arista Networks",
    "american express": "American Express",
    "akamai technologies": "Akamai Technologies",
    "arcesium": "Arcesium",
    "aqr capital": "AQR Capital",
    "atlassian": "Atlassian",
    "bank of new york mellon": "Bank of New York Mellon",
    "amazon": "Amazon",
}


def load_hunter_cache() -> Dict[str, Any]:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_hunter_cache(cache: Dict[str, Any]) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def verify_email_with_hunter(email: str, api_key: str, cache: Dict[str, Any], ctx: ssl.SSLContext) -> Dict[str, Any]:
    """Verify an email address using Hunter.io API with persistent disk caching."""
    clean_email = email.strip().lower()
    if clean_email in cache:
        return cache[clean_email]

    if not api_key:
        # Fallback if no key
        res = {
            "result": "unverified",
            "score": 50,
            "status": "unverified",
            "smtp_check": None,
            "accept_all": None,
            "cached": False,
        }
        cache[clean_email] = res
        save_hunter_cache(cache)
        return res

    url = f"https://api.hunter.io/v2/email-verifier?email={clean_email}&api_key={api_key}"
    req = urllib.request.Request(url, headers={"User-Agent": "ReachoutVerifier/1.0"})

    try:
        time.sleep(0.35)  # Rate pacing to avoid triggering 429
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            raw_data = json.loads(resp.read().decode())
            data = raw_data.get("data", {})
            result = {
                "result": data.get("result", "unknown"),
                "score": data.get("score", 0),
                "status": data.get("status", "unknown"),
                "smtp_check": data.get("smtp_check"),
                "accept_all": data.get("accept_all"),
                "regexp": data.get("regexp"),
                "mx_records": data.get("mx_records"),
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
            cache[clean_email] = result
            save_hunter_cache(cache)
            return result
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode()[:200]
        result = {
            "result": "error",
            "score": 0,
            "status": f"HTTP {e.code}",
            "error": err_msg,
        }
        cache[clean_email] = result
        save_hunter_cache(cache)
        return result
    except Exception as e:
        return {
            "result": "error",
            "score": 0,
            "status": "exception",
            "error": str(e),
        }


def is_safe_deliverable(ver: Dict[str, Any]) -> Tuple[bool, str]:
    """Strict zero-tolerance email deliverability policy for cold outreach.

    To protect sender domain reputation and prevent spam blacklisting,
    an email is approved ONLY when affirmative proof of individual mailbox
    existence is verified.

    Rules:
    - result must be 'deliverable'
    - status must be 'valid'
    - smtp_check must be True (target server explicitly verified recipient mailbox)
    - accept_all must be False (rejects catch-all domains where valid aliases cannot be proved)
    - score must be >= 85
    - mx_records must be True
    """
    if not ver:
        return False, "NO_VERIFICATION_DATA"
    if ver.get("result") != "deliverable":
        return False, f"RESULT_{ver.get('result', 'unknown').upper()}"
    if ver.get("status") != "valid":
        return False, f"STATUS_{ver.get('status', 'unknown').upper()}"
    if ver.get("smtp_check") is not True:
        return False, "SMTP_CHECK_FAILED"
    if ver.get("accept_all") is True:
        return False, "CATCH_ALL_DOMAIN_UNVERIFIED_ALIAS"
    if (ver.get("score") or 0) < 85:
        return False, f"LOW_CONFIDENCE_{ver.get('score', 0)}"
    if ver.get("mx_records") is not True:
        return False, "NO_MX_RECORDS"
    return True, "VERIFIED_SAFE"


def read_good_one_rows() -> List[Dict[str, Any]]:
    wb = openpyxl.load_workbook(str(EXCEL_PATH), data_only=True)
    if "good one " not in wb.sheetnames:
        raise ValueError(f"Sheet 'good one ' not found in {EXCEL_PATH}")
    ws = wb["good one "]

    rows: List[Dict[str, Any]] = []
    for r_idx, r in enumerate(ws.iter_rows(values_only=True), start=1):
        if not any(r):
            continue
        raw_comp = str(r[0]).strip() if r[0] is not None else ""
        raw_name = str(r[1]).strip() if r[1] is not None else ""
        raw_desig = str(r[2]).strip() if r[2] is not None and str(r[2]).strip().lower() not in {"none", "null"} else ""
        raw_email = str(r[3]).strip() if r[3] is not None else ""
        raw_phone = ""
        if r[4] is not None:
            if isinstance(r[4], (int, float)):
                raw_phone = str(int(r[4]))
            else:
                raw_phone = str(r[4]).strip()

        rows.append({
            "source_row": r_idx,
            "company_raw": raw_comp,
            "name": raw_name,
            "designation": raw_desig,
            "email_raw": raw_email,
            "phone_raw": raw_phone,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Strict Recruiter Email Verification & Ingestion")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes to reachout.db. If omitted, runs in 100% read-only audit mode (dry-run).",
    )
    args = parser.parse_args()

    print("=== Starting Email Verification & Ingestion for 'good one ' ===")
    print(f"Excel Source : {EXCEL_PATH}")
    print(f"Hunter API Key: {HUNTER_API_KEY[:6]}...{HUNTER_API_KEY[-4:] if len(HUNTER_API_KEY) > 10 else 'None'}")
    print(f"Mode         : {'LIVE DATABASE WRITE (--apply)' if args.apply else 'READ-ONLY AUDIT (DRY-RUN)'}\n")

    raw_rows = read_good_one_rows()
    print(f"Loaded {len(raw_rows)} rows from 'good one ' sheet.\n")

    ctx = ssl.create_default_context()
    cache = load_hunter_cache()
    print(f"Loaded {len(cache)} cached verification results from disk.")

    # 1. Verify all unique emails
    unique_emails = sorted(list(set(r["email_raw"].strip().lower() for r in raw_rows if r["email_raw"] and "@" in r["email_raw"])))
    print(f"Found {len(unique_emails)} unique email addresses to verify.\n")

    verification_results: Dict[str, Dict[str, Any]] = {}
    for idx, em in enumerate(unique_emails, start=1):
        cached_before = em in cache
        res = verify_email_with_hunter(em, HUNTER_API_KEY, cache, ctx)
        verification_results[em] = res
        tag = "[CACHED]" if cached_before else "[LIVE API]"
        status_code = res.get("result", "unknown").upper()
        score = res.get("score", 0)
        smtp = res.get("smtp_check")
        print(f"[{idx:2d}/{len(unique_emails)}] {tag:<10} {em:<32} -> {status_code:<14} (Score: {score:>3}, SMTP: {str(smtp)})")

    # 2. Score and classify contacts using strict deliverability rules
    strictly_deliverable: List[str] = []
    rejected_catchall: List[Tuple[str, int]] = []
    rejected_smtp: List[Tuple[str, int]] = []
    rejected_undeliverable: List[Tuple[str, int]] = []
    rejected_other: List[Tuple[str, str]] = []

    for em, ver in verification_results.items():
        is_safe, reason = is_safe_deliverable(ver)
        if is_safe:
            strictly_deliverable.append(em)
        elif reason == "CATCH_ALL_DOMAIN_UNVERIFIED_ALIAS":
            rejected_catchall.append((em, ver.get("score", 0)))
        elif reason == "SMTP_CHECK_FAILED":
            rejected_smtp.append((em, ver.get("score", 0)))
        elif ver.get("result") == "undeliverable":
            rejected_undeliverable.append((em, ver.get("score", 0)))
        else:
            rejected_other.append((em, reason))

    print("\n--- Strict Deliverability Breakdown ---")
    print(f"  Strictly Deliverable (100% Verified) : {len(strictly_deliverable)}")
    print(f"  Rejected - Catch-All Domain (Risky)   : {len(rejected_catchall)} (Amazon, Cisco, Atlassian, etc.)")
    print(f"  Rejected - SMTP Check Failed (Bounce) : {len(rejected_smtp)} (Amex, AQR, etc.)")
    print(f"  Rejected - Explicit Undeliverable     : {len(rejected_undeliverable)}")
    print(f"  Rejected - Other / Domain Errors      : {len(rejected_other)}")

    # 3. Consolidate and deduplicate candidate contacts
    candidates_by_key: Dict[str, Dict[str, Any]] = {}

    for row in raw_rows:
        em = row["email_raw"].strip().lower() if row["email_raw"] else ""
        ver = verification_results.get(em, {})

        is_safe, reason = is_safe_deliverable(ver) if em else (False, "NO_EMAIL_PROVIDED")

        # Normalize phone
        norm_phone = None
        if row["phone_raw"]:
            norm_phone = normalize_phone_number(row["phone_raw"], DEFAULT_COUNTRY_CODE)

        # Exclude contacts with NEITHER safe email NOR valid phone
        if not is_safe and not norm_phone:
            print(f"  [EXCLUDING CONTACT] {row['name']} ({em or 'No Email'}) -> Unsafe email ({reason}) and no valid phone.")
            continue

        if em and not is_safe:
            print(f"  [REJECTING EMAIL] {row['name']} ({em}) -> {reason}. Contact kept for phone outreach only.")

        raw_c = row["company_raw"].strip().lower()
        canonical_comp = COMPANY_CANONICAL_MAP.get(raw_c, row["company_raw"].strip().title())

        # If email is safe, keep it; if not safe, strip it completely so zero emails will ever be dispatched
        safe_emails = [em] if is_safe else []
        safe_phones = [norm_phone] if norm_phone else []

        # Deduplication key: company + safe_email (if safe) or company + name
        group_key = f"{canonical_comp.casefold()}|{em}" if is_safe else f"{canonical_comp.casefold()}|{row['name'].casefold()}"

        if group_key not in candidates_by_key:
            candidates_by_key[group_key] = {
                "company": canonical_comp,
                "name": row["name"],
                "designation": row["designation"],
                "emails": safe_emails,
                "phones": safe_phones,
                "source_rows": [row["source_row"]],
                "verification": ver if is_safe else {},
            }
        else:
            # Merge fields
            existing = candidates_by_key[group_key]
            if not existing["designation"] and row["designation"]:
                existing["designation"] = row["designation"]
            if norm_phone and norm_phone not in existing["phones"]:
                existing["phones"].append(norm_phone)
            if is_safe and em not in existing["emails"]:
                existing["emails"].append(em)
            existing["source_rows"].append(row["source_row"])

    total_safe_emails = sum(len(c["emails"]) for c in candidates_by_key.values())
    total_safe_phones = sum(len(c["phones"]) for c in candidates_by_key.values())
    print(f"\nConsolidated into {len(candidates_by_key)} approved contacts:")
    print(f"  Contacts with Verified Safe Emails : {total_safe_emails}")
    print(f"  Contacts with Valid Phone Numbers  : {total_safe_phones}")

    # 4. Dry-run safety gate
    if not args.apply:
        print("\n" + "=" * 65)
        print("[DRY-RUN COMPLETE] --apply flag was not specified.")
        print("  Zero changes were written to reachout.db.")
        print(f"  Ready to import: {len(candidates_by_key)} contacts ({total_safe_emails} safe emails).")
        print("  To execute live database write, re-run with: --apply")
        print("=" * 65)
        return

    # 5. Ingest into reachout.db using SQLAlchemy repositories (Only when --apply is passed)
    session = SessionFactory()
    comp_repo = SqliteCompanyRepository(session)
    contact_repo = SqliteContactRepository(session)
    supp_repo = SqliteSuppressionRepository(session)

    now = datetime.now(timezone.utc)
    new_companies = 0
    new_contacts = 0
    updated_contacts = 0
    imported_records: List[Dict[str, Any]] = []

    try:
        for cand in candidates_by_key.values():
            comp_name = cand["company"]
            norm_comp = normalize_company_name(comp_name)

            # Ensure Company entity exists
            comp = comp_repo.get_by_normalized_name(norm_comp)
            if not comp:
                comp = Company(
                    id="",
                    name=comp_name,
                    normalized_name=norm_comp,
                )
                comp_repo.save(comp)
                session.commit()
                new_companies += 1

            # Format phone and email strings
            phone_str = ", ".join(cand["phones"]) if cand["phones"] else None
            email_str = ", ".join(cand["emails"]) if cand["emails"] else None

            # Check suppression
            if supp_repo.is_suppressed(phone=phone_str, email=email_str, canonical_key=None):
                print(f"  [SUPPRESSED] Contact {cand['name']} at {comp_name} matches tombstone list.")
                continue

            # Build SourceRecord provenance
            src_rec = SourceRecord(
                source_file="Reachout_downloaded.xlsx",
                source_sheet="good one ",
                source_row=cand["source_rows"][0],
                source_fingerprint=compute_source_fingerprint(
                    "Reachout_downloaded.xlsx",
                    "good one ",
                    cand["source_rows"][0],
                    {"name": cand["name"], "email": email_str, "phone": phone_str},
                ),
                first_seen_at=now,
                last_seen_at=now,
            )

            # Check if contact already exists in company
            existing_contacts = contact_repo.find_by_company(comp.id)
            matched_contact: Optional[Contact] = None

            for ec in existing_contacts:
                if any(e in ec.emails for e in cand["emails"]) or (cand["phones"] and any(p in ec.phones for p in cand["phones"])):
                    matched_contact = ec
                    break

            is_email_verified = bool(cand["emails"])
            ver_status = "deliverable" if is_email_verified else "phone_only"
            tags = ["verified_lead"]
            if is_email_verified:
                tags.append("strict_deliverable")

            if matched_contact:
                # Merge into existing contact
                for p in cand["phones"]:
                    if p not in matched_contact.phones:
                        curr = list(matched_contact.phones)
                        curr.append(p)
                        matched_contact.phone = ", ".join(curr)
                for e in cand["emails"]:
                    if e not in matched_contact.emails:
                        curr = list(matched_contact.emails)
                        curr.append(e)
                        matched_contact.email = ", ".join(curr)
                if cand["designation"] and not matched_contact.designation:
                    matched_contact.designation = cand["designation"]
                matched_contact.updated_at = now
                for t in tags:
                    if t not in matched_contact.tags:
                        matched_contact.tags.append(t)
                contact_repo.save(matched_contact)
                updated_contacts += 1
                imported_records.append({
                    "id": matched_contact.contact_id,
                    "company": comp.name,
                    "name": matched_contact.name,
                    "designation": matched_contact.designation,
                    "email": matched_contact.email,
                    "phone": matched_contact.phone,
                    "status": "UPDATED",
                    "ver_result": ver_status,
                })
            else:
                # Create brand-new contact
                new_c = Contact(
                    contact_id="",
                    company_id=comp.id,
                    name=cand["name"] or "there",
                    designation=cand["designation"] or "",
                    phone=phone_str,
                    email=email_str,
                    source_reference=src_rec,
                    created_at=now,
                    updated_at=now,
                    notes=f"Hunter.io verified: {ver_status} (Score: {cand['verification'].get('score', 0)})",
                    tags=tags,
                )
                contact_repo.save(new_c)
                new_contacts += 1
                imported_records.append({
                    "id": new_c.contact_id,
                    "company": comp.name,
                    "name": new_c.name,
                    "designation": new_c.designation,
                    "email": new_c.email,
                    "phone": new_c.phone,
                    "status": "CREATED",
                    "ver_result": ver_status,
                })

        session.commit()
        print("\n=== Database Ingestion Complete ===")
        print(f"  New Companies Created : {new_companies}")
        print(f"  New Contacts Added    : {new_contacts}")
        print(f"  Contacts Updated      : {updated_contacts}")

    except Exception as e:
        session.rollback()
        print(f"\n[ERROR] Ingestion failed: {e}")
        raise
    finally:
        session.close()

    # 5. Export clean CSV and JSON reports
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": now.isoformat(),
            "total_source_rows": len(raw_rows),
            "unique_emails": len(unique_emails),
            "strictly_deliverable": len(strictly_deliverable),
            "rejected_catchall": len(rejected_catchall),
            "rejected_smtp_check_failed": len(rejected_smtp),
            "rejected_undeliverable": len(rejected_undeliverable),
            "rejected_other": len(rejected_other),
            "imported_contacts_count": len(imported_records),
            "new_companies_count": new_companies,
            "records": imported_records,
            "raw_verification": verification_results,
        }, f, indent=2)

    with open(OUTPUT_CSV_PATH, "w", encoding="utf-8") as f:
        f.write("company,name,designation,email,phone,verification_result,status\n")
        for rec in imported_records:
            comp_clean = f'"{rec["company"]}"'
            name_clean = f'"{rec["name"]}"'
            desig_clean = f'"{rec["designation"]}"'
            em_clean = rec["email"] or ""
            ph_clean = rec["phone"] or ""
            res_clean = rec["ver_result"]
            stat_clean = rec["status"]
            f.write(f"{comp_clean},{name_clean},{desig_clean},{em_clean},{ph_clean},{res_clean},{stat_clean}\n")

    print("\nReports saved successfully:")
    print(f"  JSON Audit: {REPORT_JSON_PATH}")
    print(f"  CSV Export: {OUTPUT_CSV_PATH}")


if __name__ == "__main__":
    main()
