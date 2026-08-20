"""Shared pytest fixtures and test doubles for Reachout verification suite."""

from __future__ import annotations

import datetime
from datetime import timezone
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Generator, List, Optional
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# --- TEST DATABASE ISOLATION ---
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
PROD_DB = DATA_DIR / "reachout.db"
MNC_XLSX = DATA_DIR / "MNC_Final.xlsx"
REACHOUT_XLSX = DATA_DIR / "Reachout.xlsx"
SEND_LOG_CSV = LOGS_DIR / "mnc_whatsapp_send_log.csv"

# Global dictionary to track SHA-256 before and after the test run
_PRE_TEST_HASHES = {}


def _calculate_file_sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


# Configure test environment BEFORE any database module is loaded
_TEMP_TEST_DIR = tempfile.mkdtemp(prefix="reachout_pytest_isolation_")
_TEST_DB_FILE = Path(_TEMP_TEST_DIR) / "test_reachout_isolated.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_FILE.as_posix()}")
os.environ.setdefault("OUTREACH_MODE", "mock")


def pytest_sessionstart(session):
    """Record SHA-256 of production database and raw datasets before test suite execution."""
    global _PRE_TEST_HASHES
    _PRE_TEST_HASHES["reachout.db"] = _calculate_file_sha256(PROD_DB)
    _PRE_TEST_HASHES["MNC_Final.xlsx"] = _calculate_file_sha256(MNC_XLSX)
    _PRE_TEST_HASHES["Reachout.xlsx"] = _calculate_file_sha256(REACHOUT_XLSX)
    _PRE_TEST_HASHES["mnc_whatsapp_send_log.csv"] = _calculate_file_sha256(SEND_LOG_CSV)

    # Initialize tables on the isolated test database
    import app.infrastructure.database as db
    # Rebind engine and sessionmaker to isolated test database
    db.DB_URL = f"sqlite:///{_TEST_DB_FILE.as_posix()}"
    db.engine = db.create_db_engine(db.DB_URL)
    db.SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=db.engine)
    db.init_db(target_engine=db.engine)


def pytest_sessionfinish(session, exitstatus):
    """Verify 100% byte-for-byte SHA-256 immutability of production database and raw workbooks."""
    global _PRE_TEST_HASHES
    for filename, path in [
        ("reachout.db", PROD_DB),
        ("MNC_Final.xlsx", MNC_XLSX),
        ("Reachout.xlsx", REACHOUT_XLSX),
        ("mnc_whatsapp_send_log.csv", SEND_LOG_CSV),
    ]:
        pre_hash = _PRE_TEST_HASHES.get(filename)
        post_hash = _calculate_file_sha256(path)
        if pre_hash is not None:
            assert post_hash == pre_hash, (
                f"FATAL: Production file '{filename}' was modified during test run!\n"
                f"  Pre-test SHA256:  {pre_hash}\n"
                f"  Post-test SHA256: {post_hash}"
            )


from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    AttemptType,
    CampaignStatus,
    Channel,
    CRMOutcome,
    InterviewState,
    OutreachStatus,
    ReminderStatus,
    SenderStatus,
)
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.reminder import FollowUpReminder
from app.domain.sender_account import SenderAccount
from app.domain.source_record import SourceRecord
from app.infrastructure.database import Base
import app.infrastructure.models  # ensure models are loaded
from app.ports.infrastructure import FrozenClock
from app.ports.providers import (
    EmailProvider,
    ProviderSendResult,
    ProviderStatusResult,
    WhatsAppProvider,
)


@pytest.fixture
def db_engine():
    """In-memory SQLite engine for fast, isolated test execution."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    """Yields a transactional DB session for an isolated in-memory DB."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """Deterministic frozen clock set to August 17, 2026 12:00:00 UTC."""
    fixed_time = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    return FrozenClock(fixed_time)


class MockWhatsAppProvider(WhatsAppProvider):
    """Test double for WhatsApp provider port."""

    def __init__(self, default_success: bool = True):
        self.default_success = default_success
        self.sent_calls: List[dict] = []
        self.fail_next_with: Optional[tuple[str, str]] = None
        self.unknown_next: bool = False
        self.recovery_required_next: bool = False

    def send_message(
        self,
        attempt: OutreachAttempt,
        recipient_phone: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        self.sent_calls.append({
            "attempt_id": attempt.id,
            "phone": recipient_phone,
            "body": message_body,
            "attachment": attachment_path,
        })
        if self.unknown_next:
            self.unknown_next = False
            return ProviderSendResult.unknown("Simulated network drop before ACK")
        if self.recovery_required_next:
            self.recovery_required_next = False
            return ProviderSendResult.recovery_required("Text sent but PDF attachment crashed")
        if self.fail_next_with:
            code, detail = self.fail_next_with
            self.fail_next_with = None
            return ProviderSendResult.failed(code, detail)
        if self.default_success:
            return ProviderSendResult.sent(provider_reference=f"wa_ref_{len(self.sent_calls)}")
        return ProviderSendResult.failed("ERR_SEND_FAILED", "Default mock failure")

    def check_status(self, provider_reference: str) -> ProviderStatusResult:
        return ProviderStatusResult(status=OutreachStatus.SENT, detail="Verified delivery")


class MockEmailProvider(EmailProvider):
    """Test double for Email provider port."""

    def __init__(self, default_success: bool = True):
        self.default_success = default_success
        self.sent_calls: List[dict] = []
        self.fail_next_with: Optional[tuple[str, str]] = None

    def send_email(
        self,
        attempt: OutreachAttempt,
        recipient_email: str,
        subject: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        self.sent_calls.append({
            "attempt_id": attempt.id,
            "email": recipient_email,
            "subject": subject,
            "body": message_body,
            "attachment": attachment_path,
        })
        if self.fail_next_with:
            code, detail = self.fail_next_with
            self.fail_next_with = None
            return ProviderSendResult.failed(code, detail)
        if self.default_success:
            return ProviderSendResult.sent(provider_reference=f"em_ref_{len(self.sent_calls)}")
        return ProviderSendResult.failed("ERR_SMTP_AUTH", "SMTP Auth Failed")


@pytest.fixture
def mock_wa_provider() -> MockWhatsAppProvider:
    return MockWhatsAppProvider()


@pytest.fixture
def mock_email_provider() -> MockEmailProvider:
    return MockEmailProvider()


@pytest.fixture
def sample_company() -> Company:
    return Company.create(name="Google India", domain="google.com")


@pytest.fixture
def sample_contact(sample_company: Company) -> Contact:
    return Contact(
        contact_id="cnt_test_google_1",
        company_id=sample_company.id,
        name="Sundar Pichai",
        phone="919876543210",
        email="sundar@google.com",
        designation="CEO",
    )


@pytest.fixture
def sample_template() -> MessageTemplate:
    return MessageTemplate.create(
        name="Default WhatsApp Template",
        channel=Channel.WHATSAPP,
        body="Hi {first_name}, I'm reaching out regarding opportunities at {company}.",
    )


@pytest.fixture
def sample_sender() -> SenderAccount:
    return SenderAccount.create(
        channel=Channel.WHATSAPP,
        provider="MOCK_PLAYWRIGHT",
        identity="+917499718082",
        display_name="Primary WhatsApp Sender",
        daily_limit=50,
    )
