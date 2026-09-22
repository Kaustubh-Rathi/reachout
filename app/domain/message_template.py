"""Message Template domain entity and rendering engine.

Supports multi-template variants, rotational outreach, channel-specific
template schemas with stable IDs, and variable validation.

Templates reference the sender's personal identity via {sender_*} placeholders.
The profile is supplied by the caller (from the git-ignored .env/config), so the
domain layer stays free of configuration/IO coupling and no personal data is
committed to the repository.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import Channel
from app.domain.template_rendering import RenderedMessage, render_template, validate_template

__all__ = ["MessageTemplate", "RenderedMessage", "normalize_attachment_ref"]


def normalize_attachment_ref(ref: Optional[str]) -> Optional[str]:
    """Normalize a user-supplied attachment reference without touching the filesystem.

    Strips surrounding whitespace and one pair of matching surrounding quotes
    (template refs are often pasted from spreadsheet cells as
    ``"D:\\Resume\\cv.pdf"``). Pure string handling, so it is safe to call
    from the domain layer and from validators.
    """
    if ref is None:
        return None
    text = ref.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    return text


@dataclass
class MessageTemplate:
    """Represents a parameterized outreach copy template.

    Attributes:
        id: Stable unique template identifier (e.g. 'WA-01' or 'EMAIL-01').
        name: Human-readable template description.
        channel: Applicable channel (WHATSAPP or EMAIL).
        body: Message body text containing placeholders (e.g. [Name], [Company Name], {first_name}, {company}, {sender_name}).
        subject: Email subject line containing placeholders (optional for WhatsApp).
        attachment_ref: Identifier or relative path to resume/attachment.
        phone_number: Default phone number associated with template sender identity.
        active: Boolean flag indicating if template is enabled for rotation.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    id: str
    name: str
    channel: Channel
    body: str
    subject: Optional[str] = None
    attachment_ref: Optional[str] = None
    phone_number: Optional[str] = None
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"tmpl_{self.channel.value.lower()}_{uuid.uuid4().hex[:8]}"
        if self.channel == Channel.EMAIL and not self.subject:
            self.subject = "Exploring opportunities at [Company Name]"
        self.attachment_ref = normalize_attachment_ref(self.attachment_ref)

    @classmethod
    def create(
        cls,
        name: str,
        channel: Channel,
        body: str,
        template_id: Optional[str] = None,
        subject: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        phone_number: Optional[str] = None,
        active: bool = True,
    ) -> MessageTemplate:
        tid = template_id or f"tmpl_{channel.value.lower()}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        return cls(
            id=tid,
            name=name.strip(),
            channel=channel,
            body=body,
            subject=subject,
            attachment_ref=attachment_ref,
            phone_number=phone_number,
            active=active,
            created_at=now,
            updated_at=now,
        )

    def validate(self, contact: Contact, company: Optional[Company] = None) -> List[str]:
        """Validate that contact/company data satisfy this template's placeholders."""
        return validate_template(self, contact, company)

    def render(
        self,
        contact: Contact,
        company: Optional[Company] = None,
        extra_vars: Optional[Dict[str, Any]] = None,
        sender_profile: Optional[Dict[str, str]] = None,
    ) -> RenderedMessage:
        """Interpolate variables into the template body and subject safely."""
        return render_template(
            self,
            contact=contact,
            company=company,
            extra_vars=extra_vars,
            sender_profile=sender_profile,
        )
