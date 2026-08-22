"""Central application configuration and constants.

Single source of truth for cross-cutting defaults so they are never
hardcoded in multiple places across the backend and frontend.
"""

from __future__ import annotations

# Default maximum number of contacts to outreach in a single campaign run.
# The UI must never default above this value; custom limits may exceed it.
DEFAULT_OUTREACH_LIMIT = 100
# Absolute ceiling enforced by the UI limit input.
MAX_OUTREACH_LIMIT = 1000

# Default follow-up reminder threshold (days) for interested contacts.
DEFAULT_FOLLOW_UP_THRESHOLD_DAYS = 7

# Placeholder tokens injected into the served dashboard HTML so the frontend
# reads configuration from this module instead of duplicating magic numbers.
HTML_DEFAULT_LIMIT_TOKEN = "__DEFAULT_OUTREACH_LIMIT__"
HTML_MAX_LIMIT_TOKEN = "__MAX_OUTREACH_LIMIT__"
