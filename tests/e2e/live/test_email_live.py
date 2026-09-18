"""Real Email Provider End-to-End Test.

Connects to REAL SMTP server via TLS/SSL, authenticates credentials from environment,
and dispatches a real test email to an actual test recipient mailbox.
Never uses FakeEmailProvider or simulated references.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.models import (
    CompanyModel,
    ContactModel,
    MessageTemplateModel,
)
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository


@pytest.mark.live_e2e
class TestEmailLiveProviderE2E:
    """Live Email end-to-end test against real SMTP server infrastructure."""

    def test_real_smtp_email_dispatch(self, live_email_pair):
        # 1. Environment and credential guard
        is_live = os.environ.get("LIVE_E2E") == "1" or os.environ.get("LIVE_EMAIL_E2E") == "1"
        if not is_live:
            pytest.skip("LIVE EMAIL: NOT RUN (LIVE_E2E=1 or LIVE_EMAIL_E2E=1 not set)")

        pair, provider = live_email_pair
        recipient_email = pair.recipient

        # 2. Database setup
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionLocal()

        outreach_repo = SqliteOutreachRepository(session)
        sender_repo = SqliteSenderRepository(session)

        # 3. Domain entities
        sender = SenderAccount.create(
            channel=Channel.EMAIL,
            provider="smtp",
            identity=pair.sender.identity,
            display_name="Live Test SMTP Sender",
            sender_id=pair.sender.id,
        )
        sender_repo.save(sender)

        template = MessageTemplate.create(
            name="Live Email Test Template",
            channel=Channel.EMAIL,
            subject="Reachout CRM Live Delivery Verification",
            body="This is an automated live delivery verification email dispatched by Reachout CRM at {timestamp}.",
        )
        session.add(MessageTemplateModel.from_domain(template))
        # The attempt row has FKs to companies/contacts: seed the recipient rows.
        session.add(
            CompanyModel(
                id="comp_live_email_test", name="Live Email Test Company", normalized_name="live email test company"
            )
        )
        session.add(
            ContactModel(
                contact_id="cnt_live_email_recip",
                company_id="comp_live_email_test",
                name="Live Email Test Recipient",
                email=recipient_email,
            )
        )
        session.commit()

        # 5. Verify SMTP credentials against live server
        verified, err = provider.verify_credentials(sender.id)
        if not verified:
            pytest.fail(f"LIVE EMAIL FAIL: SMTP Authentication failed - {err}")

        # 6. Prepare attempt
        now = datetime.now(timezone.utc)
        test_body = f"Reachout CRM Live Delivery Verification Email dispatched at {now.isoformat()}"

        attempt = OutreachAttempt.prepare(
            contact_id="cnt_live_email_recip",
            sender_account_id=sender.id,
            channel=Channel.EMAIL,
            attempt_type=AttemptType.MANUAL,
            subject=template.subject,
            message_body=test_body,
            destination=recipient_email,
            template_id=template.id,
            prepared_at=now,
        )
        attempt.mark_sending()
        outreach_repo.save(attempt)
        session.commit()

        # 7. Execute Real SMTP Dispatch
        result = provider.send_email(
            attempt=attempt,
            recipient_email=recipient_email,
            subject=attempt.subject_snapshot or template.subject or "Live Test",
            message_body=attempt.message_body_snapshot,
        )

        if not result.success:
            pytest.fail(f"LIVE EMAIL FAIL: {result.failure_code} - {result.failure_detail}")

        assert result.success is True
        assert result.status == OutreachStatus.SENT
        assert result.provider_reference is not None
        assert not result.provider_reference.startswith("mock_")
        assert not result.provider_reference.startswith("fake_")

        # 8. Verify Attempt persistence
        attempt.mark_sent(provider_reference=result.provider_reference, timestamp=datetime.now(timezone.utc))
        outreach_repo.save(attempt)
        session.commit()

        db_attempt = outreach_repo.get_by_id(attempt.id)
        assert db_attempt is not None
        assert db_attempt.status == OutreachStatus.SENT
        assert db_attempt.destination == recipient_email
        assert db_attempt.sender_account_id == sender.id
        assert db_attempt.template_id == template.id

        session.close()
