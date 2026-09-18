"""Shared pytest fixtures and test doubles for Reachout verification suite."""

from __future__ import annotations

import datetime
import hashlib
import os
import tempfile
from datetime import timezone
from pathlib import Path
from typing import Generator, Optional

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


def _checkpoint_sqlite(path: Path) -> None:
    """Flush any pending WAL contents into the main SQLite file.

    Merely reading a SQLite database that has a populated -wal file can trigger a
    checkpoint and change the main file's bytes. Normalizing the WAL before hashing
    keeps the production-immutability guard deterministic while still detecting any
    test that genuinely writes to the production database.
    """
    if not path.exists():
        return
    try:
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass


# Configure test environment BEFORE any database module is loaded
_TEMP_TEST_DIR = tempfile.mkdtemp(prefix="reachout_pytest_isolation_")
_TEST_DB_FILE = Path(_TEMP_TEST_DIR) / "test_reachout_isolated.db"
os.environ.setdefault("REACHOUT_LIVE_DATABASE_URL", os.environ.get("DATABASE_URL", f"sqlite:///{PROD_DB.as_posix()}"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_FILE.as_posix()}"
os.environ["OUTREACH_CHANNEL_DELAY_WA"] = "0.01"
os.environ["OUTREACH_CHANNEL_DELAY_EM"] = "0.01"


def pytest_sessionstart(session):
    """Record SHA-256 of production database and raw datasets before test suite execution."""
    global _PRE_TEST_HASHES
    _checkpoint_sqlite(PROD_DB)
    _PRE_TEST_HASHES["reachout.db"] = _calculate_file_sha256(PROD_DB)
    _PRE_TEST_HASHES["MNC_Final.xlsx"] = _calculate_file_sha256(MNC_XLSX)
    _PRE_TEST_HASHES["Reachout.xlsx"] = _calculate_file_sha256(REACHOUT_XLSX)
    _PRE_TEST_HASHES["mnc_whatsapp_send_log.csv"] = _calculate_file_sha256(SEND_LOG_CSV)

    # Initialize tables on the isolated test database
    import app.infrastructure.database as db

    # Rebind engine and sessionmaker to isolated test database
    db.DB_URL = f"sqlite:///{_TEST_DB_FILE.as_posix()}"
    db.SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=db.engine)
    db.init_db(target_engine=db.engine)

    # Configure test doubles as active provider overrides during tests
    from app.infrastructure.providers.factory import set_email_provider, set_whatsapp_provider
    from tests.doubles.fake_providers import FakeEmailProvider, FakeWhatsAppProvider

    set_whatsapp_provider(FakeWhatsAppProvider())
    set_email_provider(FakeEmailProvider())


@pytest.fixture(autouse=True)
def isolate_provider_storage(request, monkeypatch, tmp_path):
    if request.node.get_closest_marker("live_e2e"):
        return
    from app.infrastructure.providers import session_manager
    from app.infrastructure.security.credential_vault import default_credential_vault

    monkeypatch.setattr(default_credential_vault, "vault_path", tmp_path / "smtp_vault.enc")
    monkeypatch.setattr(default_credential_vault, "key_path", tmp_path / "vault_key")
    monkeypatch.setattr(default_credential_vault, "_cache", None)
    monkeypatch.setattr(session_manager, "DEFAULT_SESSIONS_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(session_manager.default_session_manager, "sessions_root", tmp_path / "sessions")


@pytest.fixture(autouse=True)
def reset_test_provider_overrides():
    """Reset provider doubles, rate limiter, scheduler, and env overrides per test."""
    from app.infrastructure.providers.factory import set_email_provider, set_whatsapp_provider
    from app.infrastructure.scheduler.campaign_scheduler import reset_campaign_scheduler
    from app.infrastructure.scheduler.rate_limiter import default_rate_limiter
    from tests.doubles.fake_providers import FakeEmailProvider, FakeWhatsAppProvider

    fake_wa = FakeWhatsAppProvider()
    fake_em = FakeEmailProvider()
    set_whatsapp_provider(fake_wa)
    set_email_provider(fake_em)
    default_rate_limiter.reset()
    yield
    set_whatsapp_provider(fake_wa)
    set_email_provider(fake_em)
    default_rate_limiter.reset()
    reset_campaign_scheduler()
    os.environ.pop("OUTREACH_MODE", None)


def pytest_sessionfinish(session, exitstatus):
    """Verify 100% byte-for-byte SHA-256 immutability of production database and raw workbooks."""
    global _PRE_TEST_HASHES
    for filename, path in [
        ("reachout.db", PROD_DB),
        ("MNC_Final.xlsx", MNC_XLSX),
        ("Reachout.xlsx", REACHOUT_XLSX),
        ("mnc_whatsapp_send_log.csv", SEND_LOG_CSV),
    ]:
        if filename == "reachout.db":
            _checkpoint_sqlite(path)
        pre_hash = _PRE_TEST_HASHES.get(filename)
        post_hash = _calculate_file_sha256(path)
        if pre_hash is not None:
            assert post_hash == pre_hash, (
                f"FATAL: Production file '{filename}' was modified during test run!\n"
                f"  Pre-test SHA256:  {pre_hash}\n"
                f"  Post-test SHA256: {post_hash}"
            )


from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    Channel,
)
from app.domain.message_template import MessageTemplate
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.ports.infrastructure import FrozenClock
from tests.doubles.fake_providers import (
    MockEmailProvider,
    MockWhatsAppProvider,
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
        identity="+919999999999",
        display_name="Primary WhatsApp Sender",
        daily_limit=50,
    )
