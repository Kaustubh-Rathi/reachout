#!/usr/bin/env python3
"""Reachout CRM Dashboard & Tracker Backend Server.

A local FastAPI application for tracking WhatsApp outreach, managing candidate
responses, updating notes, follow-up scheduling, and visualizing outreach metrics.

Run locally:
    python crm_server.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import threading
import shutil
import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from contact_ingestion import (
    ALL_CRM_STATUSES,
    CRM_DB_FILE,
    DATA_DIR,
    DEFAULT_LOG,
    DEFAULT_WORKBOOK,
    FOLLOW_UP_STATUSES,
    LOGS_DIR,
    NO_HIRING_STATUSES,
    POSITIVE_STATUSES,
    REPLY_STATUSES,
    ROOT,
    UI_DIR,
    UNREPLIED_STATUSES,
    Contact,
    clean_contacts,
    read_mnc_rows,
    sanitize_for_csv,
)
from app.infrastructure.database import SessionFactory, init_db
from app.infrastructure.scheduler.campaign_scheduler import get_campaign_scheduler
from app.services.crm_service import CrmService
from app.services.event_bus import event_bus
from app.services.sender_service import SenderService
from app.services.sync_service import SyncService
from app.services.template_service import TemplateService

# Process-level thread lock for safe atomic read-modify-write on crm_data.json
crm_db_lock = threading.RLock()
CRM_BACKUP_FILE = DATA_DIR / "crm_data.json.bak"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB tables and seed initial default templates/senders
    init_db()
    with SessionFactory() as session:
        TemplateService(session).seed_defaults_if_empty()
        SenderService(session).seed_defaults_if_empty()

        # If contacts table is empty, auto-sync initial workbook if present
        crm_svc = CrmService(session)
        if crm_svc.contact_repo.list_all() == []:
            sync_svc = SyncService(session)
            try:
                sync_svc.sync_source()
            except Exception as exc:
                print(f"[Notice] Initial automatic sync deferred: {exc}")

    # Startup Crash Recovery Audit: Inspect and recover any in-flight attempts & auto-pause campaigns
    scheduler = get_campaign_scheduler()
    recovered = scheduler.run_crash_recovery_audit()
    if recovered > 0:
        print(f"[Crash Recovery] Startup audit recovered {recovered} stale in-flight attempt(s).")

    yield


app = FastAPI(
    title="Reachout CRM Dashboard",
    version="1.2.0",
    description="Local CRM Tracker & WhatsApp Outreach Management API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_root_events(websocket: WebSocket):
    """Stream live domain events over root WebSocket."""
    await websocket.accept()
    await websocket.send_json({"event_type": "connected", "payload": {"status": "connected"}})
    try:
        async for event in event_bus.subscribe():
            data_payload = {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "occurred_at": event.occurred_at.isoformat(),
            }
            await websocket.send_json(data_payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# --- Pydantic Request & Response Schemas ---

class ContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=3, max_length=255, description="Canonical contact key (company|phone)")
    crm_status: Optional[str] = Field(None, description="CRM response outcome")
    notes: Optional[str] = Field(None, max_length=5000, description="Conversation notes & details")
    follow_up_date: Optional[str] = Field(None, description="Follow-up date (YYYY-MM-DD)")
    tags: Optional[List[str]] = Field(None, max_length=50, description="List of category tags")

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        s = v.strip()
        if not s or "|" not in s:
            raise ValueError("Contact key must be in the format 'company|phone'")
        parts = s.split("|")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError("Contact key must contain non-empty company and phone components")
        return s

    @field_validator("crm_status")
    @classmethod
    def validate_crm_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip()
            if v_clean not in ALL_CRM_STATUSES:
                raise ValueError(f"Invalid CRM status '{v}'. Allowed statuses: {ALL_CRM_STATUSES}")
            return v_clean
        return v

    @field_validator("follow_up_date")
    @classmethod
    def validate_follow_up_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.strip():
            v_clean = v.strip()
            try:
                datetime.strptime(v_clean, "%Y-%m-%d")
            except ValueError:
                raise ValueError("follow_up_date must be in valid YYYY-MM-DD calendar format")
            return v_clean
        return "" if v == "" else v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            clean_tags = []
            for tag in v:
                t = str(tag).strip()
                if t and len(t) <= 50:
                    clean_tags.append(t)
            return list(dict.fromkeys(clean_tags))
        return v


class BulkContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: List[str] = Field(..., min_length=1, max_length=500, description="List of contact keys to update")
    crm_status: Optional[str] = Field(None, description="CRM response outcome")
    tags: Optional[List[str]] = Field(None, max_length=50, description="List of category tags")

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("keys list cannot be empty")
        clean_keys = []
        for k in v:
            s = str(k).strip()
            if not s or "|" not in s:
                raise ValueError(f"Invalid contact key '{k}'. Must be in format 'company|phone'")
            clean_keys.append(s)
        return list(dict.fromkeys(clean_keys))

    @field_validator("crm_status")
    @classmethod
    def validate_crm_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip()
            if v_clean not in ALL_CRM_STATUSES:
                raise ValueError(f"Invalid CRM status '{v}'. Allowed statuses: {ALL_CRM_STATUSES}")
            return v_clean
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            clean_tags = []
            for tag in v:
                t = str(tag).strip()
                if t and len(t) <= 50:
                    clean_tags.append(t)
            return list(dict.fromkeys(clean_tags))
        return v


class ContactRecord(BaseModel):
    key: str
    source_file: str = "MNC_Final.xlsx"
    source_row: int
    company: str
    name: str
    phone: str
    email: str = ""
    raw_number: str
    source_columns: str
    slot: str
    dedup_status: str
    dedup_detail: str = ""
    send_status: str
    send_time: str = ""
    send_detail: str = ""
    crm_status: str
    notes: str = ""
    follow_up_date: str = ""
    tags: List[str] = []
    updated_at: str = ""


class ContactsListResponse(BaseModel):
    count: int
    contacts: List[ContactRecord]


class StatsResponse(BaseModel):
    workbook_name: str
    total: int
    sent: int
    sent_text_only: int
    delivered: int
    not_sent: int
    failed: int
    replies: int
    response_rate: float
    interested: int
    no_hiring: int
    follow_ups: int
    companies: List[str]
    statuses: List[str]


class ContactUpdateResponse(BaseModel):
    status: str
    key: str
    record: Dict[str, Any]


class BulkUpdateResponse(BaseModel):
    status: str
    updated_count: int


# --- Persistence Layer ---

def load_crm_db() -> Dict[str, Dict[str, Any]]:
    """Thread-safe read of crm_data.json with backup fallback recovery."""
    with crm_db_lock:
        if not CRM_DB_FILE.exists():
            return {}
        try:
            with CRM_DB_FILE.open("r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except Exception as exc:
            print(f"[Warning] Failed to read primary CRM DB ({exc}). Attempting backup recovery...")
            if CRM_BACKUP_FILE.exists():
                try:
                    with CRM_BACKUP_FILE.open("r", encoding="utf-8") as f_bak:
                        bak_content = f_bak.read().strip()
                        if bak_content:
                            data = json.loads(bak_content)
                            print("[Info] Successfully recovered CRM DB from backup.")
                            return data
                except Exception as bak_exc:
                    print(f"[Error] Failed to read CRM DB backup: {bak_exc}")
            # Quarantine corrupted file if recovery is impossible to avoid silent wipe
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            corrupt_file = DATA_DIR / f"crm_data.json.corrupt.{timestamp}"
            try:
                shutil.copy(CRM_DB_FILE, corrupt_file)
                print(f"[Alert] Corrupt CRM database preserved at: {corrupt_file}")
            except Exception:
                pass
            return {}


def save_crm_db(data: Dict[str, Dict[str, Any]]) -> None:
    """Thread-safe atomic write of crm_data.json with backup rotation and retry."""
    with crm_db_lock:
        temp_file = DATA_DIR / "crm_data.json.tmp"
        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # Maintain backup of previous valid database before replacing
        if CRM_DB_FILE.exists():
            try:
                shutil.copy(CRM_DB_FILE, CRM_BACKUP_FILE)
            except Exception as e:
                print(f"[Warning] Could not create backup before save: {e}")

        # Windows filesystem replace with retry loop
        max_retries = 5
        for attempt in range(max_retries):
            try:
                temp_file.replace(CRM_DB_FILE)
                break
            except PermissionError as pe:
                if attempt == max_retries - 1:
                    raise pe
                time.sleep(0.05)


def load_send_logs() -> Dict[str, Dict[str, Any]]:
    """Loads latest send log status per key from mnc_whatsapp_send_log.csv."""
    if not DEFAULT_LOG.exists():
        return {}

    logs_by_key: Dict[str, Dict[str, Any]] = {}
    try:
        with DEFAULT_LOG.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get("key", "").strip()
                if key:
                    logs_by_key[key] = {
                        "time": row.get("time", ""),
                        "status": row.get("status", ""),
                        "company": row.get("company", ""),
                        "name": row.get("name", ""),
                        "phone": row.get("phone", ""),
                        "detail": row.get("detail", ""),
                    }
    except Exception as exc:
        print(f"[Warning] Error reading send log: {exc}")

    return logs_by_key


def get_merged_contacts() -> List[Dict[str, Any]]:
    """Merges canonical workbook contacts with WhatsApp send logs and CRM metadata.
    
    Guarantees:
    - Exactly 1 record per unique key (duplicate_skipped records excluded)
    - Full email fidelity preserved from source workbook
    - Full traceability (source_file, source_row, slot, raw_number, phone)
    - Complete duplicate conflict detail surfaced
    """
    crm_data = load_crm_db()
    send_logs = load_send_logs()

    raw_contacts = []
    if DEFAULT_WORKBOOK.exists():
        try:
            rows = read_mnc_rows(DEFAULT_WORKBOOK)
            raw_contacts = clean_contacts(rows, "91")
        except Exception as exc:
            print(f"[Error] Error reading workbook: {exc}")

    merged: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()

    for c in raw_contacts:
        # Exclude duplicate_skipped records to maintain strictly unique keys
        if c.status == "duplicate_skipped":
            continue

        k = c.key
        if k in seen_keys:
            continue
        seen_keys.add(k)

        crm_record = crm_data.get(k, {})
        send_info = send_logs.get(k, {})

        send_status = send_info.get("status", "not_sent")
        send_time = send_info.get("time", "")

        default_crm_status = "No Status" if send_status == "not_sent" else "Pending Reply"
        current_crm_status = crm_record.get("crm_status", default_crm_status)

        merged.append(
            {
                "key": k,
                "source_file": DEFAULT_WORKBOOK.name,
                "source_row": c.source_row,
                "company": c.company,
                "name": c.name,
                "phone": c.phone,
                "email": c.email,
                "raw_number": c.raw_number,
                "source_columns": c.source_columns,
                "slot": c.slot,
                "dedup_status": c.status,
                "dedup_detail": c.detail,
                "send_status": send_status,
                "send_time": send_time,
                "send_detail": send_info.get("detail", ""),
                "crm_status": current_crm_status,
                "notes": crm_record.get("notes", ""),
                "follow_up_date": crm_record.get("follow_up_date", ""),
                "tags": crm_record.get("tags", []),
                "updated_at": crm_record.get("updated_at", ""),
            }
        )

    # Include any entries in send logs or CRM that were manual or not in sheet
    for k, info in send_logs.items():
        if k not in seen_keys:
            seen_keys.add(k)
            crm_record = crm_data.get(k, {})
            parts = k.split("|")
            comp = info.get("company") or (parts[0].title() if len(parts) > 0 else "Unknown")
            name = info.get("name") or "Manual Contact"
            ph = info.get("phone") or (parts[1] if len(parts) > 1 else "")

            merged.append(
                {
                    "key": k,
                    "source_file": "manual",
                    "source_row": 0,
                    "company": comp,
                    "name": name,
                    "phone": ph,
                    "email": "",
                    "raw_number": ph,
                    "source_columns": "manual",
                    "slot": "manual",
                    "dedup_status": "ready",
                    "dedup_detail": "",
                    "send_status": info.get("status", "sent"),
                    "send_time": info.get("time", ""),
                    "send_detail": info.get("detail", ""),
                    "crm_status": crm_record.get("crm_status", "Pending Reply"),
                    "notes": crm_record.get("notes", ""),
                    "follow_up_date": crm_record.get("follow_up_date", ""),
                    "tags": crm_record.get("tags", []),
                    "updated_at": crm_record.get("updated_at", ""),
                }
            )

    # Sort order: Contacts with send_time or updated_at appear at the top (newest first),
    # followed by unsent contacts in source_row order.
    sent_or_active = [c for c in merged if c.get("send_time") or c.get("updated_at")]
    sent_or_active.sort(key=lambda c: c.get("send_time") or c.get("updated_at") or "", reverse=True)

    unsent = [c for c in merged if not (c.get("send_time") or c.get("updated_at"))]
    unsent.sort(key=lambda c: c.get("source_row", 0))

    return sent_or_active + unsent


# --- REST API Endpoints ---

@app.get("/api/contacts", response_model=ContactsListResponse)
def list_contacts(
    search: Optional[str] = None,
    company: Optional[str] = None,
    send_status: Optional[str] = None,
    crm_status: Optional[str] = None,
):
    """Retrieve all contacts with optional server-side filtering."""
    contacts = get_merged_contacts()

    if search:
        s = search.lower().strip()
        contacts = [
            c
            for c in contacts
            if s in c["name"].lower()
            or s in c["company"].lower()
            or s in c["phone"].lower()
            or s in c.get("email", "").lower()
            or s in c.get("notes", "").lower()
        ]

    if company and company != "ALL":
        contacts = [c for c in contacts if c["company"].lower() == company.lower()]

    if send_status and send_status != "ALL":
        contacts = [c for c in contacts if c["send_status"].lower() == send_status.lower()]

    if crm_status and crm_status != "ALL":
        contacts = [c for c in contacts if c["crm_status"].lower() == crm_status.lower()]

    return {"count": len(contacts), "contacts": contacts}


@app.get("/api/stats", response_model=StatsResponse)
def get_stats():
    """Retrieve mathematically verified KPI statistics and status taxonomy."""
    contacts = get_merged_contacts()
    total = len(contacts)

    sent_count = sum(1 for c in contacts if c["send_status"] == "sent")
    text_only = sum(1 for c in contacts if c["send_status"] == "sent_text_only")
    delivered = sent_count + text_only
    not_sent = sum(1 for c in contacts if c["send_status"] == "not_sent")
    failed = sum(1 for c in contacts if c["send_status"] in ("failed", "invalid_number"))

    # Canonical status aggregations
    replies_count = sum(1 for c in contacts if c["crm_status"] in REPLY_STATUSES)
    response_rate = round((replies_count / delivered) * 100, 1) if delivered > 0 else 0.0

    interested = sum(1 for c in contacts if c["crm_status"] in POSITIVE_STATUSES)
    no_hiring = sum(1 for c in contacts if c["crm_status"] in NO_HIRING_STATUSES)
    follow_ups = sum(
        1 for c in contacts if c["crm_status"] in FOLLOW_UP_STATUSES or c.get("follow_up_date")
    )

    companies = sorted(list({c["company"] for c in contacts if c["company"]}))

    return {
        "workbook_name": DEFAULT_WORKBOOK.name,
        "total": total,
        "sent": sent_count,
        "sent_text_only": text_only,
        "delivered": delivered,
        "not_sent": not_sent,
        "failed": failed,
        "replies": replies_count,
        "response_rate": response_rate,
        "interested": interested,
        "no_hiring": no_hiring,
        "follow_ups": follow_ups,
        "companies": companies,
        "statuses": ALL_CRM_STATUSES,
    }


@app.post("/api/contacts/update", response_model=ContactUpdateResponse)
def update_contact(update: ContactUpdate):
    """Thread-safe update of CRM status, notes, follow-up date, or tags for a contact."""
    with crm_db_lock:
        crm_data = load_crm_db()
        current = crm_data.get(update.key, {})

        if update.crm_status is not None:
            current["crm_status"] = update.crm_status
        if update.notes is not None:
            current["notes"] = update.notes
        if update.follow_up_date is not None:
            current["follow_up_date"] = update.follow_up_date
        if update.tags is not None:
            current["tags"] = update.tags

        current["updated_at"] = datetime.now().isoformat(timespec="seconds")
        crm_data[update.key] = current
        save_crm_db(crm_data)

    return {"status": "success", "key": update.key, "record": current}


@app.post("/api/contacts/bulk-update", response_model=BulkUpdateResponse)
def bulk_update_contacts(update: BulkContactUpdate):
    """Thread-safe bulk update of status or tags across multiple contacts."""
    with crm_db_lock:
        crm_data = load_crm_db()
        now_iso = datetime.now().isoformat(timespec="seconds")

        for k in update.keys:
            current = crm_data.get(k, {})
            if update.crm_status is not None:
                current["crm_status"] = update.crm_status
            if update.tags is not None:
                current["tags"] = list(set(current.get("tags", []) + update.tags))
            current["updated_at"] = now_iso
            crm_data[k] = current

        save_crm_db(crm_data)
    return {"status": "success", "updated_count": len(update.keys)}


@app.get("/api/export")
def export_crm_csv():
    """Stream full contact and CRM state as downloadable CSV with formula injection protection."""
    contacts = get_merged_contacts()
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "key",
            "company",
            "name",
            "phone",
            "email",
            "send_status",
            "send_time",
            "crm_status",
            "notes",
            "follow_up_date",
            "tags",
            "source_row",
            "slot",
            "updated_at",
        ],
    )
    writer.writeheader()
    for c in contacts:
        writer.writerow(
            {
                "key": sanitize_for_csv(c["key"]),
                "company": sanitize_for_csv(c["company"]),
                "name": sanitize_for_csv(c["name"]),
                "phone": sanitize_for_csv(c["phone"]),
                "email": sanitize_for_csv(c.get("email", "")),
                "send_status": sanitize_for_csv(c["send_status"]),
                "send_time": sanitize_for_csv(c["send_time"]),
                "crm_status": sanitize_for_csv(c["crm_status"]),
                "notes": sanitize_for_csv(c.get("notes", "")),
                "follow_up_date": sanitize_for_csv(c.get("follow_up_date", "")),
                "tags": sanitize_for_csv("; ".join(c.get("tags", []))),
                "source_row": c.get("source_row", ""),
                "slot": sanitize_for_csv(c.get("slot", "")),
                "updated_at": sanitize_for_csv(c.get("updated_at", "")),
            }
        )
    output.seek(0)
    filename = f"reachout_crm_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Empty favicon response to prevent browser console 404s."""
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the single-page CRM dashboard UI."""
    html_file = UI_DIR / "crm_dashboard.html"
    if not html_file.exists():
        html_file = ROOT / "crm_dashboard.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard UI file not found.")
    return html_file.read_text(encoding="utf-8")


from app.api import api_router
app.include_router(api_router)


def main():
    # Configure UTF-8 encoding safely for Windows PowerShell / CMD consoles
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    import argparse

    parser = argparse.ArgumentParser(description="Start Reachout CRM Dashboard")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the dashboard on (default: 8000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host IP (default: 127.0.0.1)")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print(" [*] Reachout CRM Dashboard Server Starting...")
    print(f" [->] Access URL: http://{args.host}:{args.port}")
    print("=" * 60 + "\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
