"""Outbound outreach application service.

Coordinates single sends, manual resends, provider dispatching, audit history,
and recovery handling.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
from app.domain.policies.resend_policy import prepare_manual_resend
from app.domain.sender_account import SenderAccount
from app.infrastructure.providers.factory import (
    MockEmailProvider,
    MockWhatsAppProvider,
    get_email_provider,
    get_whatsapp_provider,
)
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.ports.providers import EmailProvider, ProviderSendResult, WhatsAppProvider
from app.services.event_bus import event_bus


class OutreachService:
    """Application service for executing and recording outbound messaging."""

    def __init__(
        self,
        session: Session,
        whatsapp_provider: Optional[WhatsAppProvider] = None,
        email_provider: Optional[EmailProvider] = None,
    ) -> None:
        self.session = session
        self.contact_repo = SqliteContactRepository(session)
        self.outreach_repo = SqliteOutreachRepository(session)
        self.sender_repo = SqliteSenderRepository(session)
        self.template_repo = SqliteTemplateRepository(session)

        # Resolve provider from injection or explicit provider factory
        self.whatsapp_provider = whatsapp_provider if whatsapp_provider is not None else get_whatsapp_provider()
        self.email_provider = email_provider if email_provider is not None else get_email_provider()


    def send_whatsapp(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        campaign_id: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch a single WhatsApp message and record attempt."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")
        
        recipient_phone = destination or (contact.primary_phone if hasattr(contact, "primary_phone") else contact.phone)
        if not recipient_phone:
            raise ValueError(f"Contact {contact.name} has no valid phone number")

        # Resolve sender
        sender = None
        if sender_id:
            sender = self.sender_repo.get_by_id(sender_id)
        if not sender:
            active_senders = self.sender_repo.list_active(Channel.WHATSAPP)
            sender = active_senders[0] if active_senders else None
        if not sender:
            # Fallback placeholder sender
            sender = SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="playwright_whatsapp",
                identity="default_wa",
                display_name="WA Default Direct",
                account_id="WA-001",
            )
            self.sender_repo.save(sender)

        # Resolve template / body
        body = custom_body or ""
        template = None
        if template_id:
            template = self.template_repo.get_by_id(template_id)
            if template:
                rendered = template.render(contact)
                body = rendered.body
                attachment_ref = attachment_ref or template.attachment_ref

        if not body:
            body = f"Hi {contact.first_name}, I am reaching out regarding opportunities at {contact.company_id}."

        now = datetime.now(timezone.utc)
        idempotency_key = generate_idempotency_key(
            contact_id=contact.contact_id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC if campaign_id else AttemptType.MANUAL,
            campaign_id=campaign_id,
            destination=recipient_phone,
            custom_salt=str(int(now.timestamp() * 1000)),
        )

        attempt = OutreachAttempt.prepare(
            contact_id=contact.contact_id,
            sender_account_id=sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC if campaign_id else AttemptType.MANUAL,
            message_body=body,
            destination=recipient_phone,
            campaign_id=campaign_id,
            template_id=template.id if template else None,
            attachment_ref=attachment_ref,
            idempotency_key=idempotency_key,
            prepared_at=now,
        )
        self.outreach_repo.save(attempt)
        self.session.commit()

        event_bus.publish_event(
            "MESSAGE_PREPARED",
            {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "WHATSAPP",
                "recipient": recipient_phone,
                "destination": recipient_phone,
                "sender_account_id": sender.id,
                "template_id": template.id if template else None,
            },
        )
        event_bus.publish_event(
            "OUTREACH_PREPARED",
            {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "WHATSAPP",
                "destination": recipient_phone,
                "sender_account_id": sender.id,
                "template_id": template.id if template else None,
            },
        )

        # Dispatch via provider
        attempt.mark_sending(now)
        self.outreach_repo.save(attempt)

        event_bus.publish_event(
            "OUTREACH_STARTED",
            {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "WHATSAPP",
                "destination": recipient_phone,
                "sender_account_id": sender.id,
            },
        )

        res = self.whatsapp_provider.send_message(
            attempt=attempt,
            recipient_phone=recipient_phone,
            message_body=body,
            attachment_path=attachment_ref,
        )

        if res.success:
            attempt.mark_sent(provider_reference=res.provider_reference, timestamp=now)
            contact.record_outreach_success(Channel.WHATSAPP, now)
            self.contact_repo.save(contact)
            self.outreach_repo.save(attempt)
            self.session.commit()

            sent_payload = {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "WHATSAPP",
                "recipient": recipient_phone,
                "destination": recipient_phone,
                "sender_account_id": sender.id,
                "template_id": template.id if template else None,
                "provider_reference": res.provider_reference,
                "status": "SENT",
                "timestamp": now.isoformat(),
            }
            event_bus.publish_event("MESSAGE_SENT", sent_payload)
            event_bus.publish_event("OUTREACH_SENT", sent_payload)
        else:
            if res.status == OutreachStatus.RECOVERY_REQUIRED:
                attempt.mark_recovery_required(res.failure_detail or "Unknown recovery condition", now)
                self.outreach_repo.save(attempt)
                self.session.commit()
                event_bus.publish_event(
                    "OUTREACH_RECOVERY_REQUIRED",
                    {
                        "attempt_id": attempt.id,
                        "contact_id": contact.contact_id,
                        "channel": "WHATSAPP",
                        "destination": recipient_phone,
                        "reason": attempt.failure_detail,
                    },
                )
            else:
                attempt.mark_failed(res.failure_code or "ERR_SEND_FAILED", res.failure_detail or "Dispatch error", now)
                self.outreach_repo.save(attempt)
                self.session.commit()

                failed_payload = {
                    "attempt_id": attempt.id,
                    "contact_id": contact.contact_id,
                    "channel": "WHATSAPP",
                    "destination": recipient_phone,
                    "sender_account_id": sender.id,
                    "template_id": template.id if template else None,
                    "status": "FAILED",
                    "failure_code": attempt.failure_code,
                    "failure_detail": attempt.failure_detail,
                    "timestamp": now.isoformat(),
                }
                event_bus.publish_event("MESSAGE_FAILED", failed_payload)
                event_bus.publish_event("OUTREACH_FAILED", failed_payload)

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "success": res.success,
            "channel": "WHATSAPP",
            "attempt_type": attempt.attempt_type.value,
            "destination": recipient_phone,
            "sender_account_id": sender.id,
            "template_id": template.id if template else None,
            "provider_reference": res.provider_reference,
            "failure_code": attempt.failure_code,
            "failure_detail": attempt.failure_detail,
        }

    def send_email(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        subject: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        campaign_id: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch a single Email message and record attempt."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")
        
        recipient_email = destination or (contact.primary_email if hasattr(contact, "primary_email") else contact.email)
        if not recipient_email:
            raise ValueError(f"Contact {contact.name} has no valid email address")

        # Resolve sender
        sender = None
        if sender_id:
            sender = self.sender_repo.get_by_id(sender_id)
        if not sender:
            active_senders = self.sender_repo.list_active(Channel.EMAIL)
            sender = active_senders[0] if active_senders else None
        if not sender:
            sender = SenderAccount.create(
                channel=Channel.EMAIL,
                provider="smtp",
                identity="default_email@domain.com",
                display_name="Email Primary (Gmail)",
                account_id="EMAIL-001",
            )
            self.sender_repo.save(sender)

        body = custom_body or ""
        subj = subject or ""
        template = None
        if template_id:
            template = self.template_repo.get_by_id(template_id)
            if template:
                rendered = template.render(contact)
                body = rendered.body
                subj = rendered.subject or f"Exploring opportunities at {contact.company_id}"
                attachment_ref = attachment_ref or template.attachment_ref

        if not subj:
            subj = f"Exploring opportunities at {contact.company_id}"
        if not body:
            body = f"Hi {contact.first_name},\n\nI hope you are doing well. Reaching out regarding roles at {contact.company_id}.\n\nBest regards,\nCandidate"

        now = datetime.now(timezone.utc)
        idempotency_key = generate_idempotency_key(
            contact_id=contact.contact_id,
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC if campaign_id else AttemptType.MANUAL,
            campaign_id=campaign_id,
            destination=recipient_email,
            custom_salt=str(int(now.timestamp() * 1000)),
        )

        attempt = OutreachAttempt.prepare(
            contact_id=contact.contact_id,
            sender_account_id=sender.id,
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC if campaign_id else AttemptType.MANUAL,
            message_body=body,
            destination=recipient_email,
            campaign_id=campaign_id,
            template_id=template.id if template else None,
            subject=subj,
            attachment_ref=attachment_ref,
            idempotency_key=idempotency_key,
            prepared_at=now,
        )
        self.outreach_repo.save(attempt)
        self.session.commit()

        event_bus.publish_event(
            "MESSAGE_PREPARED",
            {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "EMAIL",
                "recipient": recipient_email,
                "destination": recipient_email,
                "sender_account_id": sender.id,
                "template_id": template.id if template else None,
            },
        )
        event_bus.publish_event(
            "OUTREACH_PREPARED",
            {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "EMAIL",
                "destination": recipient_email,
                "sender_account_id": sender.id,
                "template_id": template.id if template else None,
            },
        )

        # Dispatch
        attempt.mark_sending(now)
        self.outreach_repo.save(attempt)

        event_bus.publish_event(
            "OUTREACH_STARTED",
            {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "EMAIL",
                "destination": recipient_email,
                "sender_account_id": sender.id,
            },
        )

        res = self.email_provider.send_email(
            attempt=attempt,
            recipient_email=recipient_email,
            subject=subj,
            message_body=body,
            attachment_path=attachment_ref,
        )

        if res.success:
            attempt.mark_sent(provider_reference=res.provider_reference, timestamp=now)
            contact.record_outreach_success(Channel.EMAIL, now)
            self.contact_repo.save(contact)
            self.outreach_repo.save(attempt)
            self.session.commit()

            sent_payload = {
                "attempt_id": attempt.id,
                "contact_id": contact.contact_id,
                "channel": "EMAIL",
                "recipient": recipient_email,
                "destination": recipient_email,
                "sender_account_id": sender.id,
                "template_id": template.id if template else None,
                "provider_reference": res.provider_reference,
                "status": "SENT",
                "timestamp": now.isoformat(),
            }
            event_bus.publish_event("MESSAGE_SENT", sent_payload)
            event_bus.publish_event("OUTREACH_SENT", sent_payload)
        else:
            if res.status == OutreachStatus.RECOVERY_REQUIRED:
                attempt.mark_recovery_required(res.failure_detail or "Unknown recovery condition", now)
                self.outreach_repo.save(attempt)
                self.session.commit()
                event_bus.publish_event(
                    "OUTREACH_RECOVERY_REQUIRED",
                    {
                        "attempt_id": attempt.id,
                        "contact_id": contact.contact_id,
                        "channel": "EMAIL",
                        "destination": recipient_email,
                        "reason": attempt.failure_detail,
                    },
                )
            else:
                attempt.mark_failed(res.failure_code or "ERR_EMAIL_FAILED", res.failure_detail or "Dispatch error", now)
                self.outreach_repo.save(attempt)
                self.session.commit()

                failed_payload = {
                    "attempt_id": attempt.id,
                    "contact_id": contact.contact_id,
                    "channel": "EMAIL",
                    "destination": recipient_email,
                    "sender_account_id": sender.id,
                    "template_id": template.id if template else None,
                    "status": "FAILED",
                    "failure_code": attempt.failure_code,
                    "failure_detail": attempt.failure_detail,
                    "timestamp": now.isoformat(),
                }
                event_bus.publish_event("MESSAGE_FAILED", failed_payload)
                event_bus.publish_event("OUTREACH_FAILED", failed_payload)

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "success": res.success,
            "channel": "EMAIL",
            "attempt_type": attempt.attempt_type.value,
            "destination": recipient_email,
            "sender_account_id": sender.id,
            "template_id": template.id if template else None,
            "provider_reference": res.provider_reference,
            "failure_code": attempt.failure_code,
            "failure_detail": attempt.failure_detail,
        }

    def resend_whatsapp(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicit operator manual resend creating a new immutable OutreachAttempt."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")
        
        recipient_phone = destination or (contact.primary_phone if hasattr(contact, "primary_phone") else contact.phone)
        if not recipient_phone:
            raise ValueError(f"Contact {contact.name} has no valid phone number")

        sender = None
        if sender_id:
            sender = self.sender_repo.get_by_id(sender_id)
        if not sender:
            active_senders = self.sender_repo.list_active(Channel.WHATSAPP)
            sender = active_senders[0] if active_senders else None
        if not sender:
            sender = SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="playwright_whatsapp",
                identity="default_wa",
                display_name="WA Default Direct",
                account_id="WA-001",
            )
            self.sender_repo.save(sender)

        body = custom_body or ""
        template = None
        if template_id:
            template = self.template_repo.get_by_id(template_id)
            if template:
                rendered = template.render(contact)
                body = rendered.body
                attachment_ref = attachment_ref or template.attachment_ref

        if not body:
            body = f"Hi {contact.first_name}, following up regarding opportunities at {contact.company_id}."

        historical = self.outreach_repo.list_by_contact(contact.contact_id)
        now = datetime.now(timezone.utc)

        attempt = prepare_manual_resend(
            contact=contact,
            sender_account=sender,
            channel=Channel.WHATSAPP,
            rendered_body=body,
            attachment_ref=attachment_ref,
            template_id=template.id if template else None,
            destination=recipient_phone,
            historical_attempts=historical,
            resend_timestamp=now,
        )
        self.outreach_repo.save(attempt)
        self.session.commit()

        # Dispatch
        attempt.mark_sending(now)
        self.outreach_repo.save(attempt)

        res = self.whatsapp_provider.send_message(
            attempt=attempt,
            recipient_phone=recipient_phone,
            message_body=body,
            attachment_path=attachment_ref,
        )

        if res.success:
            attempt.mark_sent(provider_reference=res.provider_reference, timestamp=now)
            contact.record_outreach_success(Channel.WHATSAPP, now)
            self.contact_repo.save(contact)
            self.outreach_repo.save(attempt)
            self.session.commit()

            event_bus.publish_event(
                "MESSAGE_SENT",
                {
                    "attempt_id": attempt.id,
                    "contact_id": contact.contact_id,
                    "channel": "WHATSAPP",
                    "attempt_type": "RESEND",
                    "recipient": recipient_phone,
                    "destination": recipient_phone,
                },
            )
        else:
            attempt.mark_failed(res.failure_code or "ERR_RESEND_FAILED", res.failure_detail or "Dispatch error", now)
            self.outreach_repo.save(attempt)
            self.session.commit()

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "success": res.success,
            "destination": recipient_phone,
            "provider_reference": res.provider_reference,
            "attempt_type": "RESEND",
        }

    def resend_email(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        subject: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicit operator manual email resend creating a new immutable OutreachAttempt."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")
        
        recipient_email = destination or (contact.primary_email if hasattr(contact, "primary_email") else contact.email)
        if not recipient_email:
            raise ValueError(f"Contact {contact.name} has no valid email address")

        sender = None
        if sender_id:
            sender = self.sender_repo.get_by_id(sender_id)
        if not sender:
            active_senders = self.sender_repo.list_active(Channel.EMAIL)
            sender = active_senders[0] if active_senders else None
        if not sender:
            sender = SenderAccount.create(
                channel=Channel.EMAIL,
                provider="smtp",
                identity="default_email@domain.com",
                display_name="Email Primary",
                account_id="EMAIL-001",
            )
            self.sender_repo.save(sender)

        body = custom_body or ""
        subj = subject or ""
        template = None
        if template_id:
            template = self.template_repo.get_by_id(template_id)
            if template:
                rendered = template.render(contact)
                body = rendered.body
                subj = rendered.subject or f"Following up: Opportunities at {contact.company_id}"
                attachment_ref = attachment_ref or template.attachment_ref

        if not subj:
            subj = f"Following up: Opportunities at {contact.company_id}"
        if not body:
            body = f"Hi {contact.first_name},\n\nFollowing up on my previous note regarding technical roles at {contact.company_id}.\n\nBest regards,\nCandidate"

        historical = self.outreach_repo.list_by_contact(contact.contact_id)
        now = datetime.now(timezone.utc)

        attempt = prepare_manual_resend(
            contact=contact,
            sender_account=sender,
            channel=Channel.EMAIL,
            rendered_body=body,
            subject=subj,
            attachment_ref=attachment_ref,
            template_id=template.id if template else None,
            destination=recipient_email,
            historical_attempts=historical,
            resend_timestamp=now,
        )
        self.outreach_repo.save(attempt)
        self.session.commit()

        # Dispatch
        attempt.mark_sending(now)
        self.outreach_repo.save(attempt)

        res = self.email_provider.send_email(
            attempt=attempt,
            recipient_email=recipient_email,
            subject=subj,
            message_body=body,
            attachment_path=attachment_ref,
        )

        if res.success:
            attempt.mark_sent(provider_reference=res.provider_reference, timestamp=now)
            contact.record_outreach_success(Channel.EMAIL, now)
            self.contact_repo.save(contact)
            self.outreach_repo.save(attempt)
            self.session.commit()

            event_bus.publish_event(
                "MESSAGE_SENT",
                {
                    "attempt_id": attempt.id,
                    "contact_id": contact.contact_id,
                    "channel": "EMAIL",
                    "attempt_type": "RESEND",
                    "recipient": recipient_email,
                    "destination": recipient_email,
                },
            )
        else:
            attempt.mark_failed(res.failure_code or "ERR_EMAIL_RESEND_FAILED", res.failure_detail or "Dispatch error", now)
            self.outreach_repo.save(attempt)
            self.session.commit()

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "success": res.success,
            "destination": recipient_email,
            "provider_reference": res.provider_reference,
            "attempt_type": "RESEND",
        }

    def get_history(self, contact_id: str) -> List[Dict[str, Any]]:
        """Retrieve complete historical attempts for a contact."""
        attempts = self.outreach_repo.list_by_contact(contact_id)
        return [
            {
                "id": a.id,
                "channel": a.channel.value,
                "attempt_type": a.attempt_type.value,
                "status": a.status.value,
                "destination": a.destination,
                "sender_account_id": a.sender_account_id,
                "template_id": a.template_id,
                "subject": a.subject_snapshot,
                "message_body": a.message_body_snapshot,
                "prepared_at": a.prepared_at.isoformat() if a.prepared_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "failure_code": a.failure_code,
                "failure_detail": a.failure_detail,
                "provider_reference": a.provider_reference,
                "recovery_notes": a.recovery_notes,
            }
            for a in attempts
        ]

    def get_recovery_queue(self) -> List[Dict[str, Any]]:
        """Retrieve all outreach attempts stuck in RECOVERY_REQUIRED or UNKNOWN."""
        rec = self.outreach_repo.list_by_status(OutreachStatus.RECOVERY_REQUIRED)
        unk = self.outreach_repo.list_by_status(OutreachStatus.UNKNOWN)
        combined = rec + unk

        results = []
        for a in combined:
            cnt = self.contact_repo.get_by_id(a.contact_id)
            results.append({
                "id": a.id,
                "contact_id": a.contact_id,
                "contact_name": cnt.name if cnt else "Unknown",
                "company": cnt.company_id if cnt else "Unknown",
                "channel": a.channel.value,
                "destination": a.destination,
                "status": a.status.value,
                "failure_code": a.failure_code,
                "failure_detail": a.failure_detail,
                "prepared_at": a.prepared_at.isoformat() if a.prepared_at else None,
                "recovery_notes": a.recovery_notes,
            })
        return results

    def resolve_recovery(self, attempt_id: str, action: str, recovery_notes: Optional[str] = None) -> Dict[str, Any]:
        """Resolve a stuck recovery attempt: 'mark_sent', 'retry', or 'cancel'."""
        attempt = self.outreach_repo.get_by_id(attempt_id)
        if not attempt:
            raise ValueError(f"Attempt not found: {attempt_id}")

        now = datetime.now(timezone.utc)
        attempt.recovery_notes = recovery_notes or f"Resolved via operator action: {action}"

        if action == "mark_sent":
            attempt.status = OutreachStatus.SENT
            attempt.completed_at = now
            cnt = self.contact_repo.get_by_id(attempt.contact_id)
            if cnt:
                cnt.record_outreach_success(attempt.channel, now)
                self.contact_repo.save(cnt)
        elif action == "cancel":
            attempt.status = OutreachStatus.FAILED
            attempt.failure_code = "OPERATOR_CANCELLED"
            attempt.failure_detail = "Operator cancelled in recovery queue"
            attempt.completed_at = now
        elif action == "retry":
            attempt.status = OutreachStatus.PREPARED

        self.outreach_repo.save(attempt)
        self.session.commit()

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "recovery_notes": attempt.recovery_notes,
        }
