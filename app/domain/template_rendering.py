"""Template rendering and validation engine.

Pure functions that interpolate contact/company/sender variables into a message
template. Kept separate from the ``MessageTemplate`` entity so rendering rules can
change without touching the entity, and vice versa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.domain.company import Company
    from app.domain.contact import Contact
    from app.domain.message_template import MessageTemplate


@dataclass(frozen=True)
class RenderedMessage:
    """Immutable snapshot of a fully interpolated message ready for dispatch."""

    body: str
    subject: Optional[str] = None
    attachment_ref: Optional[str] = None


def _resolve_context(
    template: "MessageTemplate",
    contact: "Contact",
    company: Optional["Company"],
    extra_vars: Optional[Dict[str, Any]],
    sender_profile: Optional[Dict[str, str]],
) -> Dict[str, str]:
    company_name = company.name if company and company.name else (contact.company_id or "")
    first_name = contact.first_name or contact.name or ""
    full_name = contact.name or first_name
    context: Dict[str, str] = {
        "name": full_name,
        "first_name": first_name,
        "company": company_name,
        "company_name": company_name,
        "designation": contact.designation or "",
        "phone": contact.phone or "",
        "email": contact.email or "",
        **(sender_profile or {}),
    }
    if extra_vars:
        for key, value in extra_vars.items():
            context[key] = str(value)
    return context


def render_template(
    template: "MessageTemplate",
    contact: "Contact",
    company: Optional["Company"] = None,
    extra_vars: Optional[Dict[str, Any]] = None,
    sender_profile: Optional[Dict[str, str]] = None,
) -> RenderedMessage:
    """Interpolate variables into the template body and subject safely.

    ``sender_profile`` supplies the ``{sender_*}`` identity variables; callers in
    the application/infrastructure layers pass the configured profile.
    """
    company_name = company.name if company and company.name else (contact.company_id or "")
    first_name = contact.first_name or contact.name or ""
    context = _resolve_context(template, contact, company, extra_vars, sender_profile)

    def _format_str(template_str: str) -> str:
        result = template_str
        if first_name:
            result = result.replace("[Name]", first_name)
        if company_name:
            result = result.replace("[Company Name]", company_name)
            result = result.replace("[Company]", company_name)
        if contact.designation:
            result = result.replace("[Designation]", contact.designation)
        if contact.phone:
            result = result.replace("[Phone]", contact.phone)
        if contact.email:
            result = result.replace("[Email]", contact.email)

        def replacer(match: re.Match) -> str:
            key = match.group(1).strip()
            return context.get(key, match.group(0))

        return re.sub(r"\{([a-zA-Z0-9_]+)\}", replacer, result)

    rendered_body = _format_str(template.body)
    rendered_subject = _format_str(template.subject) if template.subject else None

    return RenderedMessage(
        body=rendered_body,
        subject=rendered_subject,
        # Attachments are explicit and template-driven. There is deliberately no
        # implicit default resume here: a silently-substituted, non-existent path
        # used to cause text-only sends that still reported success.
        attachment_ref=template.attachment_ref,
    )


def validate_template(
    template: "MessageTemplate",
    contact: "Contact",
    company: Optional["Company"] = None,
) -> List[str]:
    """Validate that contact/company data satisfy the template's placeholders.

    Returns a list of error messages (empty if valid).
    """
    errors: List[str] = []
    raw_name = (contact.name or "").strip()
    name_val = "" if raw_name.lower() in ("there", "na", "n/a", "null", "none", "") else contact.first_name

    comp_name = (company.name if company and company.name else (contact.company_id or "")).strip()
    if comp_name.lower() in ("na", "n/a", "null", "none", "unknown", ""):
        comp_name = ""

    subject = template.subject
    needs_name = (
        "[Name]" in template.body
        or "{first_name}" in template.body
        or "{name}" in template.body
        or (subject and ("[Name]" in subject or "{first_name}" in subject or "{name}" in subject))
    )
    if needs_name and not name_val:
        errors.append("Contact name is missing or cannot be resolved")

    needs_comp = (
        "[Company Name]" in template.body
        or "[Company]" in template.body
        or "{company}" in template.body
        or "{company_name}" in template.body
        or (
            subject
            and (
                "[Company Name]" in subject
                or "[Company]" in subject
                or "{company}" in subject
                or "{company_name}" in subject
            )
        )
    )
    if needs_comp and not comp_name:
        errors.append("Company name is missing or cannot be resolved")

    rendered = render_template(template, contact=contact, company=company)
    for token in ["[Name]", "[Company Name]", "[Company]"]:
        if token in rendered.body or (rendered.subject and token in rendered.subject):
            errors.append(f"Unresolved placeholder '{token}' remaining in rendered message")

    return errors
