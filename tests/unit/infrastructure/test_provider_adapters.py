"""Integration tests for Provider Adapters (WhatsApp & Email) with mocks and failure taxonomy."""

from unittest.mock import MagicMock, patch

from app.composition import get_credential_vault
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.ports.providers import EmailProvider, WhatsAppProvider


class TestProviderAdapters:
    def test_whatsapp_provider_protocol_and_invalid_number_handling(self, tmp_path):
        manager = WhatsAppSessionManager(sessions_root=tmp_path / "sessions")
        provider = PlaywrightWhatsAppProvider(session_manager=manager)
        assert isinstance(provider, WhatsAppProvider)

        attempt = OutreachAttempt.prepare(
            contact_id="cnt_test_1",
            sender_account_id="snd_wa_test",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello",
        )

        # Empty phone
        res_empty = provider.send_message(attempt, recipient_phone="", message_body="Hello")
        assert res_empty.success is False
        assert res_empty.failure_code == "ERR_INVALID_PHONE"

        # Invalid phone format (3 digits)
        res_bad = provider.send_message(attempt, recipient_phone="123", message_body="Hello")
        assert res_bad.success is False
        assert res_bad.failure_code == "ERR_INVALID_PHONE_FORMAT"

    def test_email_provider_protocol_and_smtp_dispatch(self, tmp_path):
        attachment_file = tmp_path / "resume.pdf"
        attachment_file.write_bytes(b"%PDF-1.4 dummy pdf content")

        provider = SmtpEmailProvider(
            credential_vault=get_credential_vault(),
            credential_store={
                "snd_email_test": {
                    "user": "tester@example.com",
                    "password": "app-password-1234",
                    "host": "smtp.example.com",
                    "port": "587",
                    "from_address": "tester@example.com",
                }
            },
        )
        assert isinstance(provider, EmailProvider)

        attempt = OutreachAttempt.prepare(
            contact_id="cnt_test_2",
            sender_account_id="snd_email_test",
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello email",
            subject="Job Inquiry",
            attachment_ref=str(attachment_file),
        )

        # Invalid recipient
        res_bad = provider.send_email(attempt, recipient_email="invalid-email", subject="Hi", message_body="Hello")
        assert res_bad.success is False
        assert res_bad.failure_code == "ERR_INVALID_EMAIL"

        # Mock successful SMTP send
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

            res_sent = provider.send_email(
                attempt,
                recipient_email="recruiter@company.com",
                subject="Job Inquiry",
                message_body="Hello email",
                attachment_path=str(attachment_file),
            )

            assert res_sent.success is True
            assert res_sent.status == OutreachStatus.SENT
            assert res_sent.provider_reference is not None
            assert mock_smtp.send_message.called
