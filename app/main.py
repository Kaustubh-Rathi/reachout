"""Reachout CRM Control Plane & Application Server.

FastAPI main application module mounting the clean architecture routers under /api,
serving the operational dashboard UI, and managing startup/shutdown lifecycle.
"""

from __future__ import annotations

import io
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from app.api import api_router
from app.infrastructure.database import SessionFactory, init_db
from app.infrastructure.scheduler.campaign_scheduler import get_campaign_scheduler
from app.services.crm_service import CrmService
from app.services.event_bus import event_bus
from app.services.sender_service import SenderService
from app.services.sync_service import SyncService
from app.services.template_service import TemplateService

ROOT_DIR = Path(__file__).resolve().parent.parent
UI_DIR = ROOT_DIR / "ui"
DATA_DIR = ROOT_DIR / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB tables and seed initial default templates/senders
    init_db()
    with SessionFactory() as session:
        TemplateService(session).seed_defaults_if_empty()
        sender_svc = SenderService(session)
        sender_svc.seed_defaults_if_empty()
        sender_svc.reconcile_sender_states()

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
    # Shutdown logic if needed


app = FastAPI(
    title="Reachout CRM Control Plane",
    version="2.0.0",
    description="Modern CRM Dashboard & Multi-Channel Outreach Automation Engine",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all /api endpoints
app.include_router(api_router)



@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/api/stats", deprecated=True, tags=["Deprecated"])
def get_stats():
    """Aggregated stats for backward compatibility (DEPRECATED: Use /api/crm/kpis instead).
    
    Delegates directly to canonical CrmService KPI calculation.
    """
    with SessionFactory() as session:
        svc = CrmService(session)
        kpis = svc.get_kpis()
        contacts = svc.contact_repo.list_all()
        companies = sorted(list({c.company_id for c in contacts if c.company_id}))
        resp_rate = round((kpis["interested"] / kpis["contacted"]) * 100, 1) if kpis["contacted"] > 0 else 0.0
        return {
            "workbook_name": "Reachout System",
            "total": kpis["total_contacts"],
            "sent": kpis["whatsapp_sent"] + kpis["email_sent"],
            "whatsapp_sent": kpis["whatsapp_sent"],
            "email_sent": kpis["email_sent"],
            "sent_text_only": 0,
            "delivered": kpis["contacted"],
            "not_sent": kpis["total_contacts"] - kpis["contacted"],
            "failed": kpis["failed"],
            "replies": kpis["interested"] + kpis["not_interested"],
            "response_rate": resp_rate,
            "interested": kpis["interested"],
            "no_hiring": kpis["not_interested"],
            "follow_ups": kpis["follow_up_due"],
            "companies": companies,
            "statuses": ["No Status", "Pending Reply", "Interested", "No Hiring / Not Interested", "Follow-Up Scheduled", "Interview Scheduled"],
        }


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the modern operational CRM dashboard UI."""
    html_file = UI_DIR / "crm_dashboard.html"
    if not html_file.exists():
        html_file = ROOT_DIR / "crm_dashboard.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard UI file not found.")
    return html_file.read_text(encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Start Reachout CRM Control Plane")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the dashboard on (default: 8000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host IP (default: 127.0.0.1)")
    args = parser.parse_args()

    print("\n" + "=" * 65)
    print(" [*] Reachout CRM Control Plane Starting...")
    print(f" [->] Access Dashboard: http://{args.host}:{args.port}")
    print("=" * 65 + "\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
