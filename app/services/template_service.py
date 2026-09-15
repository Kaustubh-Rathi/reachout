"""Template application service.

Manages message template inventory, validation, personalization previews, and rotation.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from app.domain.enums import Channel
from app.domain.message_template import ALL_OFFICIAL_TEMPLATES, MessageTemplate
from app.services.context import ServiceContext, build_service_context

DEFAULT_TEMPLATES = ALL_OFFICIAL_TEMPLATES


class TemplateService:
    """Application service for message templates."""

    def __init__(self, session: Session, context: Optional[ServiceContext] = None) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.repo = ctx.template_repo
        self.event_publisher = ctx.event_publisher

    def seed_defaults_if_empty(self) -> None:
        """Seed default templates if table is empty or missing official templates."""
        wa_templates = self.repo.list_by_channel(Channel.WHATSAPP)
        email_templates = self.repo.list_by_channel(Channel.EMAIL)
        existing_ids = {t.id for t in (wa_templates + email_templates)}

        # Ensure all official templates exist
        for t in DEFAULT_TEMPLATES:
            if t.id not in existing_ids:
                self.repo.save(t)
        self.session.commit()

    def list_templates(self, channel: Optional[str] = None, active_only: bool = False) -> List[MessageTemplate]:
        """List templates, optionally filtered by channel."""
        self.seed_defaults_if_empty()
        if channel:
            ch = Channel(channel.upper())
            return self.repo.list_by_channel(ch, active_only=active_only)

        wa = self.repo.list_by_channel(Channel.WHATSAPP, active_only=active_only)
        em = self.repo.list_by_channel(Channel.EMAIL, active_only=active_only)
        return wa + em

    def get_template(self, template_id: str) -> Optional[MessageTemplate]:
        self.seed_defaults_if_empty()
        return self.repo.get_by_id(template_id)

    def create_template(
        self,
        id: str,
        name: str,
        channel: Channel,
        body: str,
        subject: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        phone_number: Optional[str] = None,
        active: bool = True,
    ) -> MessageTemplate:
        template = MessageTemplate(
            id=id.strip(),
            name=name.strip(),
            channel=channel,
            body=body,
            subject=subject,
            attachment_ref=attachment_ref,
            phone_number=phone_number,
            active=active,
        )
        saved = self.repo.save(template)
        self.session.commit()
        return saved

    def update_template(
        self,
        template_id: str,
        name: Optional[str] = None,
        body: Optional[str] = None,
        subject: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        phone_number: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> Optional[MessageTemplate]:
        template = self.repo.get_by_id(template_id)
        if not template:
            return None
        if name is not None:
            template.name = name.strip()
        if body is not None:
            template.body = body
        if subject is not None:
            template.subject = subject
        if attachment_ref is not None:
            template.attachment_ref = attachment_ref
        if phone_number is not None:
            template.phone_number = phone_number
        if active is not None:
            template.active = active
        saved = self.repo.save(template)
        self.session.commit()
        return saved
