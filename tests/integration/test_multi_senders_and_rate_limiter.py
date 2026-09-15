"""Integration tests for N independent senders, rate limiting, concurrency, and backoff."""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.enums import Channel
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.repositories import SqliteSenderRepository
from app.infrastructure.scheduler.rate_limiter import RateLimiter


@pytest.fixture
def sender_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'senders_test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestMultiSendersAndRateLimiter:
    def test_n_sender_accounts_registration_and_retrieval(self, sender_session):
        repo = SqliteSenderRepository(sender_session)

        # Register N WhatsApp senders
        for i in range(1, 6):
            snd = SenderAccount.create(
                sender_id=f"snd_wa_{i:03d}",
                channel=Channel.WHATSAPP,
                provider="playwright_web",
                identity=f"+91900000000{i}",
                display_name=f"WhatsApp Line {i}",
                session_ref=f".sessions/whatsapp/snd_wa_{i:03d}",
                daily_limit=100,
                hourly_limit=20,
            )
            repo.save(snd)

        # Register N Email senders
        for i in range(1, 4):
            snd = SenderAccount.create(
                sender_id=f"snd_email_{i:03d}",
                channel=Channel.EMAIL,
                provider="smtp",
                identity=f"outreach_{i}@example.com",
                display_name=f"Gmail Account {i}",
                daily_limit=500,
                hourly_limit=50,
            )
            repo.save(snd)

        sender_session.commit()

        wa_senders = repo.list_by_channel(Channel.WHATSAPP)
        assert len(wa_senders) == 5

        email_senders = repo.list_by_channel(Channel.EMAIL)
        assert len(email_senders) == 3

        all_active = repo.list_active()
        assert len(all_active) == 8

    def test_rate_limiter_concurrency_isolation(self):
        limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.5, "EMAIL": 0.2})

        # Snd 1 acquires lock
        can1, _ = limiter.can_send("snd_1", "WHATSAPP")
        assert can1 is True
        acquired1 = limiter.acquire_sender("snd_1")
        assert acquired1 is True

        # Snd 1 cannot send concurrently while busy
        can1_again, reason = limiter.can_send("snd_1", "WHATSAPP")
        assert can1_again is False
        assert reason == "SENDER_BUSY"

        # Snd 2 is independent and CAN send concurrently
        can2, _ = limiter.can_send("snd_2", "WHATSAPP")
        assert can2 is True
        acquired2 = limiter.acquire_sender("snd_2")
        assert acquired2 is True

        # Release snd 1
        limiter.record_dispatch_success("snd_1")
        # Must respect spacing delay
        can1_immediate, reason2 = limiter.can_send("snd_1", "WHATSAPP")
        assert can1_immediate is False
        assert "WAITING_CHANNEL_PACE" in reason2

        time.sleep(0.55)
        can1_after_delay, _ = limiter.can_send("snd_1", "WHATSAPP")
        assert can1_after_delay is True

    def test_rate_limiter_exponential_backoff_on_failure(self):
        limiter = RateLimiter(
            default_channel_delay={"WHATSAPP": 0.0}, base_backoff_seconds=1.0, max_backoff_seconds=10.0
        )

        # 1st failure -> 1.0s backoff
        backoff1 = limiter.record_dispatch_failure("snd_err_1")
        assert backoff1 == 1.0

        can, reason = limiter.can_send("snd_err_1", "WHATSAPP")
        assert can is False
        assert "RATE_LIMITED_BACKOFF" in reason

        time.sleep(1.05)
        can_after, _ = limiter.can_send("snd_err_1", "WHATSAPP")
        assert can_after is True

        # 2nd failure -> 2.0s backoff
        backoff2 = limiter.record_dispatch_failure("snd_err_1")
        assert backoff2 == 2.0

        # Success resets backoff
        time.sleep(2.05)
        limiter.record_dispatch_success("snd_err_1")
        assert limiter._consecutive_failures["snd_err_1"] == 0

    def test_rate_limiter_daily_and_hourly_limits(self):
        limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.0})

        # Daily limit of 3
        for _ in range(3):
            can, _ = limiter.can_send("snd_limit", "WHATSAPP", daily_limit=3)
            assert can is True
            limiter.record_dispatch_success("snd_limit")

        can_overflow, reason = limiter.can_send("snd_limit", "WHATSAPP", daily_limit=3)
        assert can_overflow is False
        assert reason == "DAILY_LIMIT_REACHED"
