"""Real WhatsApp Provider End-to-End Test.

Connects to REAL WhatsApp Web via Playwright, requires interactive QR scan if not authenticated,
and dispatches a real message to a verified test recipient.
Never uses FakeWhatsAppProvider or simulated references.
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
from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository


@pytest.mark.live_e2e
class TestWhatsAppLiveProviderE2E:
    """Live WhatsApp end-to-end test against real WhatsApp Web infrastructure."""

    def test_real_whatsapp_message_dispatch(self, live_whatsapp_pair):
        # 1. Environment and credential guard
        is_live = os.environ.get("LIVE_E2E") == "1" or os.environ.get("LIVE_WHATSAPP_E2E") == "1"
        if not is_live:
            pytest.skip("LIVE WHATSAPP: NOT RUN (LIVE_E2E=1 or LIVE_WHATSAPP_E2E=1 not set)")

        pair = live_whatsapp_pair
        recipient_phone = pair.recipient
        sender_id = pair.sender.id

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
            channel=Channel.WHATSAPP,
            provider="playwright_whatsapp",
            identity=pair.sender.identity,
            display_name="Live Test WhatsApp Sender",
            sender_id=sender_id,
        )
        sender_repo.save(sender)

        template = MessageTemplate.create(
            name="Live WhatsApp Test Template",
            channel=Channel.WHATSAPP,
            body="Reachout CRM Automated Live Delivery Verification at {timestamp}",
        )
        session.add(MessageTemplateModel.from_domain(template))
        # The attempt row has FKs to companies/contacts: seed the recipient rows.
        session.add(CompanyModel(id="comp_live_test", name="Live Test Company", normalized_name="live test company"))
        session.add(
            ContactModel(
                contact_id="cnt_live_test_recip",
                company_id="comp_live_test",
                name="Live Test Recipient",
                phone=recipient_phone,
            )
        )
        session.commit()

        # 4. Instantiate REAL Playwright Provider
        session_manager = WhatsAppSessionManager()
        provider = PlaywrightWhatsAppProvider(
            session_manager=session_manager,
            headless=os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() == "true",
            timeout_seconds=180,
            expected_identity=pair.sender.identity,
        )

        now = datetime.now(timezone.utc)
        test_body = f"Reachout CRM Live WhatsApp Delivery Test — {now.isoformat()}"

        attempt = OutreachAttempt.prepare(
            contact_id="cnt_live_test_recip",
            sender_account_id=sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.MANUAL,
            message_body=test_body,
            destination=recipient_phone,
            template_id=template.id,
            prepared_at=now,
        )
        attempt.mark_sending()
        outreach_repo.save(attempt)
        session.commit()

        # 5. Execute Real Dispatch
        result = provider.send_message(
            attempt=attempt,
            recipient_phone=recipient_phone,
            message_body=attempt.message_body_snapshot,
        )

        # 6. Verify Provider Result & DB Attempt
        if not result.success:
            if result.failure_code == "ERR_AUTH_REQUIRED":
                pytest.skip("LIVE WHATSAPP: BLOCKED — QR REQUIRED (Scan QR to link test device session)")
            pytest.fail(f"LIVE WHATSAPP FAIL: {result.failure_code} - {result.failure_detail}")

        assert result.success is True
        assert result.status == OutreachStatus.SENT
        assert result.provider_reference is not None
        assert not result.provider_reference.startswith("mock_")
        assert not result.provider_reference.startswith("fake_")

        # 7. Verify Attempt persistence
        attempt.mark_sent(provider_reference=result.provider_reference, timestamp=datetime.now(timezone.utc))
        outreach_repo.save(attempt)
        session.commit()

        db_attempt = outreach_repo.get_by_id(attempt.id)
        assert db_attempt is not None
        assert db_attempt.status == OutreachStatus.SENT
        assert db_attempt.destination == recipient_phone
        assert db_attempt.sender_account_id == sender.id
        assert db_attempt.template_id == template.id

        session.close()
