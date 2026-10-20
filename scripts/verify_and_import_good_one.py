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

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl

from app.config import DEFAULT_COUNTRY_CODE
from app.domain.company import Company, normalize_company_name
from app.domain.contact import Contact
from app.domain.source_record import SourceRecord
from app.domain.source_record import compute_source_fingerprint
from app.infrastructure.database import SessionFactory
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
from app.infrastructure.source.synchronizer import extract_emails, normalize_phone_number

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
    print(f"=== Starting Email Verification & Ingestion for 'good one ' ===")
    print(f"Excel Source : {EXCEL_PATH}")
    print(f"Hunter API Key: {HUNTER_API_KEY[:6]}...{HUNTER_API_KEY[-4:] if len(HUNTER_API_KEY) > 10 else 'None'}")

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

    # 2. Score and classify contacts
    deliverable_count = sum(1 for r in verification_results.values() if r.get("result") == "deliverable")
    risky_count = sum(1 for r in verification_results.values() if r.get("result") == "risky")
    undeliverable_count = sum(1 for r in verification_results.values() if r.get("result") == "undeliverable")
    error_count = sum(1 for r in verification_results.values() if r.get("result") in ("error", "unknown"))

    print("\n--- Verification Summary ---")
    print(f"  Deliverable   : {deliverable_count}")
    print(f"  Risky/CatchAll: {risky_count}")
    print(f"  Undeliverable : {undeliverable_count} (Will be excluded from database)")
    print(f"  Errors        : {error_count}")

    # 3. Consolidate and deduplicate candidate contacts
    # We group by (canonical_company, normalized_email) or (canonical_company, normalized_name)
    candidates_by_key: Dict[str, Dict[str, Any]] = {}

    for row in raw_rows:
        em = row["email_raw"].strip().lower() if row["email_raw"] else ""
        ver = verification_results.get(em, {})
        res_status = ver.get("result", "unknown")

        # Exclude undeliverable mailboxes
        if res_status == "undeliverable" or ver.get("score", 0) == 0:
            print(f"  [EXCLUDING] {row['name']} ({em}) -> Marked {res_status.upper()} (Score: {ver.get('score')})")
            continue

        raw_c = row["company_raw"].strip().lower()
        canonical_comp = COMPANY_CANONICAL_MAP.get(raw_c, row["company_raw"].strip().title())

        # Normalize phone
        norm_phone = None
        if row["phone_raw"]:
            norm_phone = normalize_phone_number(row["phone_raw"], DEFAULT_COUNTRY_CODE)

        # Primary deduplication key
        group_key = f"{canonical_comp.casefold()}|{em}" if em else f"{canonical_comp.casefold()}|{row['name'].casefold()}"

        if group_key not in candidates_by_key:
            candidates_by_key[group_key] = {
                "company": canonical_comp,
                "name": row["name"],
                "designation": row["designation"],
                "emails": [em] if em else [],
                "phones": [norm_phone] if norm_phone else [],
                "source_rows": [row["source_row"]],
                "verification": ver,
            }
        else:
            # Merge fields
            existing = candidates_by_key[group_key]
            if not existing["designation"] and row["designation"]:
                existing["designation"] = row["designation"]
            if norm_phone and norm_phone not in existing["phones"]:
                existing["phones"].append(norm_phone)
            if em and em not in existing["emails"]:
                existing["emails"].append(em)
            existing["source_rows"].append(row["source_row"])

    print(f"\nConsolidated {len(raw_rows)} raw rows into {len(candidates_by_key)} approved unique recruiter contacts.")

    # 4. Ingest into reachout.db using SQLAlchemy repositories
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
        for key, cand in candidates_by_key.items():
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

            ver_status = cand["verification"].get("result", "verified")
            tags = ["verified_hunter"]
            if ver_status == "risky":
                tags.append("risky_catchall")

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
        print(f"\n=== Database Ingestion Complete ===")
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
            "deliverable": deliverable_count,
            "risky": risky_count,
            "undeliverable": undeliverable_count,
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

    print(f"\nReports saved successfully:")
    print(f"  JSON Audit: {REPORT_JSON_PATH}")
    print(f"  CSV Export: {OUTPUT_CSV_PATH}")


if __name__ == "__main__":
    main()
