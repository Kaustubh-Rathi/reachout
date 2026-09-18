"""Reachout CRM Control Plane & Application Server.

FastAPI main application module mounting the clean architecture routers under /api,
serving the operational dashboard UI, and managing startup/shutdown lifecycle.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.api import api_router
from app.api.error_handlers import register_exception_handlers
from app.config import (
    DEFAULT_OUTREACH_LIMIT,
    HTML_DEFAULT_LIMIT_TOKEN,
    HTML_MAX_LIMIT_TOKEN,
    MAX_OUTREACH_LIMIT,
)
from app.domain.errors import SourceError
from app.infrastructure.database import SessionFactory, init_db
from app.infrastructure.scheduler.campaign_scheduler import get_campaign_scheduler
from app.services.crm_service import CrmService
from app.services.sender_service import SenderService
from app.services.sync_service import SyncService
from app.services.template_service import TemplateService

ROOT_DIR = Path(__file__).resolve().parent.parent
UI_DIR = ROOT_DIR / "ui"
DATA_DIR = ROOT_DIR / "data"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB tables and seed initial default templates/senders
    init_db()
    with SessionFactory() as session:
        TemplateService(session).seed_defaults_if_empty()
        sender_svc = SenderService(session)
        sender_svc.reconcile_sender_states()

        # If contacts table is empty, auto-sync initial workbook if present
        crm_svc = CrmService(session)
        if not crm_svc.has_any_contacts():
            sync_svc = SyncService(session)
            try:
                sync_svc.sync_source()
            except SourceError as exc:
                # No default workbook / unreadable source on first run is expected;
                # any other failure propagates so startup fails loudly.
                logger.info("Initial automatic sync deferred: %s", exc)

    # Startup Crash Recovery Audit: Inspect and recover any in-flight attempts & auto-pause campaigns
    scheduler = get_campaign_scheduler()
    recovered = scheduler.run_crash_recovery_audit()
    if recovered > 0:
        logger.warning("Startup crash recovery audit recovered %d stale in-flight attempt(s)", recovered)

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
    # Explicit localhost allowlist (wildcard + credentials is rejected by browsers
    # and unsafe). Matches the dashboard on any local port.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

# Mount all /api endpoints
app.include_router(api_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the modern operational CRM dashboard UI."""
    html_file = UI_DIR / "crm_dashboard.html"
    if not html_file.exists():
        html_file = ROOT_DIR / "crm_dashboard.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard UI file not found.")
    html = html_file.read_text(encoding="utf-8")
    # Inject configured limits so the frontend never hardcodes magic numbers.
    html = html.replace(HTML_DEFAULT_LIMIT_TOKEN, str(DEFAULT_OUTREACH_LIMIT))
    html = html.replace(HTML_MAX_LIMIT_TOKEN, str(MAX_OUTREACH_LIMIT))
    return html


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            logger.debug("stdout/stderr reconfigure unavailable", exc_info=True)

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
