"""Outgoing message composition: recipient, body, subject, and attachment.

Resolves the concrete outbound message for a contact/channel, applying template
rendering and channel defaults. Kept separate from dispatch and recovery.
"""

from __future__ import annotations

from typing import Optional

from app.config import SENDER_PROFILE
from app.domain.contact import Contact
from app.domain.enums import Channel
from app.domain.message_template import MessageTemplate
from app.ports.repositories import TemplateRepository
from app.services.channel_defaults import default_body, default_subject, recipient_for


class MessageComposer:
    """Resolves the recipient and rendered message for a manual/campaign send."""

    def __init__(self, template_repo: TemplateRepository) -> None:
        self.template_repo = template_repo

    def resolve_recipient(self, contact: Contact, channel: Channel, destination: Optional[str]) -> str:
        recipient = destination or recipient_for(contact, channel)
        if not recipient:
            what = "phone number" if channel == Channel.WHATSAPP else "email address"
            from app.domain.errors import ValidationError

            raise ValidationError(f"Contact {contact.name} has no valid {what}")
        return recipient

    def resolve_message(
        self,
        contact: Contact,
        channel: Channel,
        template_id: Optional[str],
        custom_body: Optional[str],
        subject: Optional[str],
        attachment_ref: Optional[str],
        *,
        is_resend: bool,
    ) -> tuple[str, str, Optional[str], Optional[MessageTemplate]]:
        """Resolve the outgoing body/subject/attachment, applying channel defaults."""
        body = custom_body or ""
        subj = subject or ""
        template = None
        if template_id:
            template = self.template_repo.get_by_id(template_id)
            if template:
                rendered = template.render(contact, sender_profile=SENDER_PROFILE)
                body = rendered.body
                if channel == Channel.EMAIL:
                    subj = rendered.subject or default_subject(contact, is_resend=is_resend)
                attachment_ref = attachment_ref or rendered.attachment_ref

        if not body:
            body = default_body(contact, channel, is_resend=is_resend)
        if channel == Channel.EMAIL and not subj:
            subj = default_subject(contact, is_resend=is_resend)
        return body, subj, attachment_ref, template
