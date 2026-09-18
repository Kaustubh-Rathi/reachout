"""Central application configuration and constants.

Single source of truth for cross-cutting defaults so they are never
hardcoded in multiple places across the backend and frontend.

Personal identity (name, phone, links, etc.) is loaded from a git-ignored
`.env` file so no personal data is ever committed to the repository.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.domain.errors import ConfigurationError

# ---------------------------------------------------------------------------
# .env loader (dependency-free). Loads ROOT/.env if present; existing env vars
# take precedence so real secrets are never overwritten.
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = ROOT_DIR / ".env"
if _ENV_FILE.exists():
    try:
        _lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"Unable to read .env at {_ENV_FILE}: {exc}") from exc
    for _line in _lines:
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _val = _line.partition("=")
        _key = _key.strip()
        if _key and _key not in os.environ:
            os.environ[_key] = _val.strip()


def _env(name: str, default: str = "") -> str:
    """Read an environment variable (stripped), falling back to a configured default."""
    return os.environ.get(name, default).strip()


# ---------------------------------------------------------------------------
# Outreach limits (configurable; UI must never default above DEFAULT).
# ---------------------------------------------------------------------------
# Default maximum number of contacts to outreach in a single campaign run.
DEFAULT_OUTREACH_LIMIT = 100
# Absolute ceiling enforced by the UI limit input.
MAX_OUTREACH_LIMIT = 1000

# Default follow-up reminder threshold (days) for interested contacts is defined in
# app.domain.policies.reminder_policy (single source of truth) and imported above.

# Default country code applied to bare phone numbers during Excel import.
DEFAULT_COUNTRY_CODE = _env("DEFAULT_COUNTRY_CODE", "91")

# Fallback message copy used only when a manual send has no template/body.
DEFAULT_MESSAGE_SUBJECT = _env("DEFAULT_MESSAGE_SUBJECT", "Exploring opportunities")
DEFAULT_MESSAGE_BODY = _env(
    "DEFAULT_MESSAGE_BODY",
    "Hi {first_name}, I am reaching out regarding opportunities at {company}.",
)

# ---------------------------------------------------------------------------
# Sender identity profile (loaded from git-ignored .env so no personal data is
# committed). Injected into message-template rendering via {sender_*} vars.
# ---------------------------------------------------------------------------
SENDER_PROFILE = {
    "sender_name": _env("SENDER_NAME", "Candidate"),
    "sender_phone": _env("SENDER_PHONE", ""),
    "sender_portfolio": _env("SENDER_PORTFOLIO", ""),
    "sender_linkedin": _env("SENDER_LINKEDIN", ""),
    "sender_github": _env("SENDER_GITHUB", ""),
    "sender_college": _env("SENDER_COLLEGE", ""),
    "sender_degree": _env("SENDER_DEGREE", ""),
    "sender_year": _env("SENDER_YEAR", ""),
    "sender_cgpa": _env("SENDER_CGPA", ""),
    "sender_experience": _env("SENDER_EXPERIENCE", ""),
    "sender_codeforces": _env("SENDER_CODEFORCES", ""),
    "sender_jee": _env("SENDER_JEE", ""),
    "sender_resume": _env("SENDER_RESUME", "resume.pdf"),
}

# Placeholder tokens injected into the served dashboard HTML so the frontend
# reads configuration from this module instead of duplicating magic numbers.
HTML_DEFAULT_LIMIT_TOKEN = "__DEFAULT_OUTREACH_LIMIT__"
HTML_MAX_LIMIT_TOKEN = "__MAX_OUTREACH_LIMIT__"
