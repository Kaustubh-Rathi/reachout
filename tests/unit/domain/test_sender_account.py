"""Unit tests for SenderAccount domain entity and cardinality independence."""

from datetime import datetime, timezone

from app.domain.enums import Channel, SenderStatus
from app.domain.sender_account import SenderAccount


class TestSenderAccountModel:
    def test_sender_creation_and_defaults(self):
        sender = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="whatsapp_web",
            identity="+919876543210",
            display_name="Primary SIM",
            credential_ref="SESSION_DIR_1",
            daily_limit=50,
        )
        assert sender.channel == Channel.WHATSAPP
        assert sender.provider == "whatsapp_web"
        assert sender.identity == "+919876543210"
        assert sender.display_name == "Primary SIM"
        assert sender.status == SenderStatus.ACTIVE
        assert sender.is_available()
        assert sender.last_used_at is None
        assert sender.daily_limit == 50

    def test_cardinality_independence_arbitrary_senders(self):
        """Verify the architecture supports arbitrarily many independent sender accounts."""
        senders = [
            SenderAccount.create(
                channel=Channel.EMAIL,
                provider="smtp_gmail",
                identity=f"outreach_agent_{i}@example.com",
                display_name=f"Gmail Agent #{i}",
            )
            for i in range(1, 101)  # 100 independent sender identities
        ]
        assert len(senders) == 100
        # All IDs must be unique
        unique_ids = {s.id for s in senders}
        assert len(unique_ids) == 100

    def test_record_usage(self):
        sender = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="whatsapp_web",
            identity="+919000000000",
            display_name="SIM A",
        )
        t0 = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
        sender.record_usage(timestamp=t0)
        assert sender.last_used_at == t0

    def test_mark_status(self):
        sender = SenderAccount.create(
            channel=Channel.EMAIL,
            provider="smtp_gmail",
            identity="agent@example.com",
            display_name="Agent",
        )
        assert sender.is_available()

        sender.mark_status(SenderStatus.RATE_LIMITED)
        assert sender.status == SenderStatus.RATE_LIMITED
        assert not sender.is_available()
