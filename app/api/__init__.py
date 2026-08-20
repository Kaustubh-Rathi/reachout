"""API Routers package.

Exports all modular API routers under the /api hierarchy.
"""

from fastapi import APIRouter

from app.api.campaigns import router as campaigns_router
from app.api.companies import router as companies_router
from app.api.contacts import router as contacts_router
from app.api.crm import router as crm_router
from app.api.events import router as events_router
from app.api.outreach import router as outreach_router
from app.api.senders import router as senders_router
from app.api.sync import router as sync_router
from app.api.templates import router as templates_router

api_router = APIRouter()
api_router.include_router(contacts_router)
api_router.include_router(companies_router)
api_router.include_router(campaigns_router)
api_router.include_router(outreach_router)
api_router.include_router(senders_router)
api_router.include_router(templates_router)
api_router.include_router(crm_router)
api_router.include_router(sync_router)
api_router.include_router(events_router)

__all__ = [
    "api_router",
    "contacts_router",
    "companies_router",
    "campaigns_router",
    "outreach_router",
    "senders_router",
    "templates_router",
    "crm_router",
    "sync_router",
    "events_router",
]
