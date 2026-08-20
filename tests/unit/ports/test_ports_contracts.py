"""Unit tests verifying Port Contracts and Protocol conformance with in-memory adapters."""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Sequence
import pytest

from app.domain import (
    AttemptType,
    Campaign,
    CampaignStatus,
    Channel,
    Company,
    Contact,
    CRMOutcome,
    FollowUpReminder,
    InterviewState,
    MessageTemplate,
    OutreachAttempt,
    OutreachStatus,
    SenderAccount,
    SourceRecord,
)
from app.ports import (
    CampaignRepository,
    Clock,
    CompanyRepository,
    ContactRepository,
    DomainEvent,
    EmailProvider,
    EventPublisher,
    FrozenClock,
    OutreachRepository,
    ProviderSendResult,
    ProviderStatusResult,
    ReminderRepository,
    Scheduler,
    SenderRepository,
    SourceReader,
    SourceRow,
    SourceSynchronizer,
    SyncSummary,
    SystemClock,
    TemplateRepository,
    WhatsAppProvider,
)


# --- In-Memory Implementations for Contract Testing ---

class InMemoryContactRepository:
    def __init__(self) -> None:
        self._store: Dict[str, Contact] = {}

    def get_by_id(self, contact_id: str) -> Optional[Contact]:
        return self._store.get(contact_id)

    def get_by_key(self, canonical_key: str) -> Optional[Contact]:
        for contact in self._store.values():
            if contact.canonical_key == canonical_key:
                return contact
        return None

    def list_all(self) -> List[Contact]:
        return list(self._store.values())

    def find_by_company(self, company_id: str) -> List[Contact]:
        return [c for c in self._store.values() if c.company_id == company_id]

    def save(self, contact: Contact) -> Contact:
        self._store[contact.contact_id] = contact
        return contact

    def save_bulk(self, contacts: Sequence[Contact]) -> List[Contact]:
        for c in contacts:
            self._store[c.contact_id] = c
        return list(contacts)


class InMemoryCompanyRepository:
    def __init__(self) -> None:
        self._store: Dict[str, Company] = {}

    def get_by_id(self, company_id: str) -> Optional[Company]:
        return self._store.get(company_id)

    def get_by_normalized_name(self, normalized_name: str) -> Optional[Company]:
        for comp in self._store.values():
            if comp.normalized_name == normalized_name:
                return comp
        return None

    def list_all(self) -> List[Company]:
        return list(self._store.values())

    def save(self, company: Company) -> Company:
        self._store[company.id] = company
        return company


class InMemoryOutreachRepository:
    def __init__(self) -> None:
        self._store: Dict[str, OutreachAttempt] = {}

    def get_by_id(self, attempt_id: str) -> Optional[OutreachAttempt]:
        return self._store.get(attempt_id)

    def get_by_idempotency_key(self, key: str) -> Optional[OutreachAttempt]:
        for a in self._store.values():
            if a.idempotency_key == key:
                return a
        return None

    def list_by_contact(self, contact_id: str) -> List[OutreachAttempt]:
        return [a for a in self._store.values() if a.contact_id == contact_id]

    def list_by_campaign(self, campaign_id: str) -> List[OutreachAttempt]:
        return [a for a in self._store.values() if a.campaign_id == campaign_id]

    def list_by_status(self, status: OutreachStatus) -> List[OutreachAttempt]:
        return [a for a in self._store.values() if a.status == status]

    def save(self, attempt: OutreachAttempt) -> OutreachAttempt:
        self._store[attempt.id] = attempt
        return attempt


class MockWhatsAppProvider:
    def send_message(
        self,
        attempt: OutreachAttempt,
        recipient_phone: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        if not recipient_phone:
            return ProviderSendResult.failed("ERR_INVALID_PHONE", "Missing phone number")
        return ProviderSendResult.sent(provider_reference="wa_mock_ref_123")

    def check_status(self, provider_reference: str) -> ProviderStatusResult:
        return ProviderStatusResult(status=OutreachStatus.SENT, detail="Delivered to recipient")


class MockEmailProvider:
    def send_email(
        self,
        attempt: OutreachAttempt,
        recipient_email: str,
        subject: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        if not recipient_email or "@" not in recipient_email:
            return ProviderSendResult.failed("ERR_INVALID_EMAIL", "Invalid email address format")
        return ProviderSendResult.sent(provider_reference="email_mock_ref_456")


class MockEventPublisher:
    def __init__(self) -> None:
        self.published: List[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        self.published.append(event)

    def publish_batch(self, events: Sequence[DomainEvent]) -> None:
        self.published.extend(events)


# --- Tests ---

class TestPortContracts:
    def test_contact_repository_protocol_conformance(self):
        repo = InMemoryContactRepository()
        assert isinstance(repo, ContactRepository)

        contact = Contact(contact_id="c1", company_id="amazon", name="Alice", phone="919999999999")
        repo.save(contact)
        assert repo.get_by_id("c1") == contact
        assert repo.get_by_key("amazon|919999999999") == contact
        assert len(repo.find_by_company("amazon")) == 1

    def test_company_repository_protocol_conformance(self):
        repo = InMemoryCompanyRepository()
        assert isinstance(repo, CompanyRepository)

        company = Company.create(name="Google LLC", company_id="google")
        repo.save(company)
        assert repo.get_by_id("google") == company
        assert repo.get_by_normalized_name("google llc") == company

    def test_outreach_repository_protocol_conformance(self):
        repo = InMemoryOutreachRepository()
        assert isinstance(repo, OutreachRepository)

        attempt = OutreachAttempt.prepare(
            contact_id="c1",
            sender_account_id="snd_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello",
        )
        repo.save(attempt)
        assert repo.get_by_id(attempt.id) == attempt
        assert repo.get_by_idempotency_key(attempt.idempotency_key) == attempt
        assert len(repo.list_by_contact("c1")) == 1

    def test_whatsapp_provider_protocol_conformance(self):
        provider = MockWhatsAppProvider()
        assert isinstance(provider, WhatsAppProvider)

        attempt = OutreachAttempt.prepare(
            contact_id="c1", sender_account_id="s1", channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC, message_body="Hello"
        )
        res = provider.send_message(attempt, recipient_phone="919876543210", message_body="Hello")
        assert res.success
        assert res.status == OutreachStatus.SENT
        assert res.provider_reference == "wa_mock_ref_123"

    def test_email_provider_protocol_conformance(self):
        provider = MockEmailProvider()
        assert isinstance(provider, EmailProvider)

        attempt = OutreachAttempt.prepare(
            contact_id="c1", sender_account_id="s1", channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC, message_body="Hello", subject="Hi"
        )
        res = provider.send_email(attempt, recipient_email="alice@amazon.com", subject="Hi", message_body="Hello")
        assert res.success
        assert res.status == OutreachStatus.SENT
        assert res.provider_reference == "email_mock_ref_456"

    def test_clock_implementations(self):
        sys_clock = SystemClock()
        assert isinstance(sys_clock, Clock)
        assert sys_clock.now().tzinfo is not None

        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        frozen = FrozenClock(t0)
        assert isinstance(frozen, Clock)
        assert frozen.now() == t0

        frozen.advance(days=3)
        assert frozen.now() == t0 + timedelta(days=3)

    def test_event_publisher_conformance(self):
        pub = MockEventPublisher()
        assert isinstance(pub, EventPublisher)

        evt = DomainEvent(event_type="OUTREACH_ATTEMPT_SENT", payload={"attempt_id": "att_1"})
        pub.publish(evt)
        assert len(pub.published) == 1
        assert pub.published[0].event_type == "OUTREACH_ATTEMPT_SENT"
