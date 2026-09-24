from unittest.mock import MagicMock, patch

from app.domain.enums import AttemptType, Channel
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider


def make_attempt(sender_id="sender-1"):
    return OutreachAttempt.prepare(
        contact_id="contact-1",
        sender_account_id=sender_id,
        channel=Channel.EMAIL,
        attempt_type=AttemptType.AUTOMATIC,
        message_body="Body",
    )


def make_provider(**kwargs):
    vault = MagicMock()
    vault.get_credentials.return_value = None
    return SmtpEmailProvider(credential_vault=vault, **kwargs)


def test_credentials_precedence_vault_cache_and_environment(monkeypatch):
    provider = make_provider(default_smtp_host="fallback.example", default_smtp_port=587)
    vault_creds = {"user": "vault@example.com", "password": "vault-pass", "host": "vault.example", "port": "465"}
    provider.credential_vault.get_credentials.return_value = vault_creds
    assert provider.get_sender_credentials("sender-1") == vault_creds
    assert provider.get_sender_credentials("sender-1") == vault_creds

    provider.credential_store.clear()
    provider.credential_vault.get_credentials.return_value = None
    monkeypatch.setenv("EMAIL_USER", "env@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "env-pass")
    monkeypatch.setenv("SMTP_PORT", "not-a-port")
    credentials = provider.get_sender_credentials("sender-2")
    assert credentials["user"] == "env@example.com"
    assert credentials["password"] == "env-pass"
    assert credentials["port"] == "not-a-port"


def test_set_credentials_trims_and_persists():
    provider = make_provider()
    provider.set_sender_credentials("sender-1", " user@example.com ", " secret ", host=" smtp.example ", port=465)
    assert provider.credential_store["sender-1"] == {
        "user": "user@example.com",
        "password": "secret",
        "host": "smtp.example",
        "port": "465",
        "from_address": "user@example.com",
    }
    provider.credential_vault.save_credentials.assert_called_once_with(
        "sender-1", provider.credential_store["sender-1"]
    )


def test_verify_credentials_uses_ssl_starttls_and_reports_failures():
    provider = make_provider()
    provider.credential_store["ssl"] = {"user": "u", "password": "p", "host": "smtp", "port": "465"}
    provider.credential_store["tls"] = {"user": "u", "password": "p", "host": "smtp", "port": "587"}
    with patch("smtplib.SMTP_SSL"), patch("smtplib.SMTP") as smtp_cls:
        smtp_server = smtp_cls.return_value.__enter__.return_value
        assert provider.verify_credentials("ssl") == (True, None)
        assert smtp_server.starttls.called is False
        assert provider.verify_credentials("tls") == (True, None)
        smtp_server.starttls.assert_called_once()

    provider.credential_store["bad-port"] = {"user": "u", "password": "p", "host": "smtp", "port": "bad"}
    with patch("smtplib.SMTP") as smtp_cls:
        assert provider.verify_credentials("bad-port")[0] is True
        assert smtp_cls.call_args.args[1] == 587

    provider.credential_store["missing"] = {"user": "", "password": ""}
    missing = provider.verify_credentials("missing")
    assert missing[0] is False
    assert "Missing SMTP username" in missing[1]

    provider.credential_store["auth"] = {"user": "u", "password": "p", "host": "smtp", "port": "587"}
    with patch("smtplib.SMTP", side_effect=__import__("smtplib").SMTPAuthenticationError(535, b"bad")):
        auth_error = provider.verify_credentials("auth")
    assert auth_error[0] is False
    assert "authentication rejected" in auth_error[1]


def test_send_email_builds_redirected_message_and_attachment(tmp_path):
    provider = make_provider(test_redirect_to="capture@example.com")
    provider.credential_store["sender"] = {
        "user": "sender@example.com",
        "password": "pass",
        "host": "smtp.example",
        "port": "587",
        "from_address": "sender@example.com",
    }
    attachment = tmp_path / "resume.txt"
    attachment.write_text("resume", encoding="utf-8")
    with patch("smtplib.SMTP") as smtp_cls:
        server = smtp_cls.return_value.__enter__.return_value
        result = provider.send_email(
            make_attempt("sender"),
            "recipient@example.com",
            "Subject",
            "Body",
            str(attachment),
        )
    assert result.success is True
    message = server.send_message.call_args.args[0]
    assert message["To"] == "capture@example.com"
    assert message["X-Original-To"] == "recipient@example.com"
    assert message.get_filename() is None
    assert any(part.get_filename() == "resume.txt" for part in message.walk())


def test_send_email_failure_taxonomy_and_validation(tmp_path):
    provider = make_provider()
    attempt = make_attempt()
    assert provider.send_email(attempt, "invalid", "s", "b").failure_code == "ERR_INVALID_EMAIL"
    assert provider.send_email(attempt, "to@example.com", "s", "b").failure_code == "ERR_MISSING_CREDENTIALS"

    provider.credential_store["sender"] = {
        "user": "u",
        "password": "p",
        "host": "smtp",
        "port": "587",
        "from_address": "u",
    }
    sender_attempt = make_attempt("sender")
    missing = provider.send_email(sender_attempt, "to@example.com", "s", "b", "missing.txt")
    assert missing.failure_code == "ERR_ATTACHMENT_NOT_FOUND"

    with patch("smtplib.SMTP", side_effect=__import__("smtplib").SMTPRecipientsRefused({})):
        refused = provider.send_email(sender_attempt, "to@example.com", "s", "b")
    assert refused.failure_code == "ERR_RECIPIENT_REFUSED"

    with patch("smtplib.SMTP", side_effect=TimeoutError("timed out")):
        unknown = provider.send_email(sender_attempt, "to@example.com", "s", "b")
    assert unknown.status.value == "UNKNOWN"
