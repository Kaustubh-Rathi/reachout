"""SMTP Email Provider Adapter.

Implements the EmailProvider port supporting N independent email sender accounts,
TLS/SSL authentication, MIME multi-part attachments, and test email redirection.
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
import time
import uuid
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, Optional

from app.domain.enums import OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.security.credential_vault import default_credential_vault
from app.ports.providers import EmailProvider, ProviderSendResult


class SmtpEmailProvider:
    """EmailProvider adapter communicating via SMTP."""

    def __init__(
        self,
        default_smtp_host: str = "smtp.gmail.com",
        default_smtp_port: int = 587,
        credential_store: Optional[Dict[str, Dict[str, str]]] = None,
        test_redirect_to: Optional[str] = None,
    ) -> None:
        self.default_smtp_host = default_smtp_host
        self.default_smtp_port = default_smtp_port
        self.credential_store = credential_store or {}
        self.test_redirect_to = test_redirect_to

    def get_sender_credentials(self, sender_account_id: str) -> Dict[str, str]:
        """Resolve SMTP user and password for a sender identity."""
        if sender_account_id in self.credential_store:
            return self.credential_store[sender_account_id]

        # Check persistent credential vault
        vault_creds = default_credential_vault.get_credentials(sender_account_id)
        if vault_creds and vault_creds.get("user") and vault_creds.get("password"):
            self.credential_store[sender_account_id] = vault_creds
            return vault_creds

        # Check environment variables: EMAIL_USER_<sender_id> or global EMAIL_USER / EMAIL_PASSWORD
        clean_id = sender_account_id.replace("-", "_").upper()
        user = os.environ.get(f"EMAIL_USER_{clean_id}") or os.environ.get("EMAIL_USER", "")
        pwd = os.environ.get(f"EMAIL_PASSWORD_{clean_id}") or os.environ.get("EMAIL_PASSWORD", "")
        host = os.environ.get(f"SMTP_HOST_{clean_id}") or os.environ.get("SMTP_HOST", self.default_smtp_host)
        port_str = os.environ.get(f"SMTP_PORT_{clean_id}") or os.environ.get("SMTP_PORT", str(self.default_smtp_port))

        return {
            "user": user.strip(),
            "password": pwd.strip(),
            "host": host.strip(),
            "port": port_str.strip(),
            "from_address": user.strip(),
        }

    def set_sender_credentials(
        self,
        sender_account_id: str,
        user: str,
        password: str,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        """Store credentials for a sender account in memory and secure persistent vault."""
        creds = {
            "user": user.strip(),
            "password": password.strip(),
            "host": (host or self.default_smtp_host).strip(),
            "port": str(port or self.default_smtp_port),
            "from_address": user.strip(),
        }
        self.credential_store[sender_account_id] = creds
        default_credential_vault.save_credentials(sender_account_id, creds)

    def verify_credentials(self, sender_account_id: str) -> tuple[bool, Optional[str]]:
        """Probe SMTP host and authenticate user/password to verify credentials."""
        creds = self.get_sender_credentials(sender_account_id)
        user = creds.get("user")
        pwd = creds.get("password")
        host = creds.get("host", self.default_smtp_host)
        try:
            port = int(creds.get("port", str(self.default_smtp_port)))
        except ValueError:
            port = self.default_smtp_port

        if not user or not pwd:
            return False, f"Missing SMTP username or password for sender '{sender_account_id}'"

        try:
            context = ssl.create_default_context()
            if port == 465:
                # Implicit TLS (SMTPS) — no STARTTLS handshake.
                with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as server:
                    server.login(user, pwd)
            else:
                # Explicit TLS via STARTTLS (e.g. 587).
                with smtplib.SMTP(host, port, timeout=15) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(user, pwd)
            return True, None
        except smtplib.SMTPAuthenticationError as auth_err:
            return False, f"SMTP authentication rejected: {auth_err}"
        except Exception as exc:
            return False, f"SMTP connection error: {exc}"

    def send_email(
        self,
        attempt: OutreachAttempt,
        recipient_email: str,
        subject: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        """Dispatch a single email over SMTP."""
        if not recipient_email or "@" not in recipient_email:
            return ProviderSendResult.failed(
                failure_code="ERR_INVALID_EMAIL",
                failure_detail=f"Invalid recipient email: '{recipient_email}'",
            )

        creds = self.get_sender_credentials(attempt.sender_account_id)
        user = creds.get("user")
        pwd = creds.get("password")
        host = creds.get("host", self.default_smtp_host)
        try:
            port = int(creds.get("port", str(self.default_smtp_port)))
        except ValueError:
            port = self.default_smtp_port

        if not user or not pwd:
            return ProviderSendResult.failed(
                failure_code="ERR_MISSING_CREDENTIALS",
                failure_detail=f"No SMTP credentials found for sender '{attempt.sender_account_id}'",
            )

        destination = self.test_redirect_to or recipient_email

        # Build MIME Message
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = creds.get("from_address") or user
        msg["To"] = destination
        if self.test_redirect_to:
            msg["X-Original-To"] = recipient_email

        msg.set_content(message_body)

        # Add attachment if provided
        if attachment_path:
            att_path = Path(attachment_path)
            if att_path.exists():
                ctype, encoding = mimetypes.guess_type(str(att_path))
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)

                with att_path.open("rb") as f:
                    file_data = f.read()
                    msg.add_attachment(
                        file_data,
                        maintype=maintype,
                        subtype=subtype,
                        filename=att_path.name,
                    )

        try:
            context = ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
                    server.login(user, pwd)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=30) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(user, pwd)
                    server.send_message(msg)

            ref_id = f"email_{uuid.uuid4().hex[:16]}"
            return ProviderSendResult.sent(provider_reference=ref_id)

        except smtplib.SMTPAuthenticationError as auth_err:
            return ProviderSendResult.failed(
                failure_code="ERR_SMTP_AUTH_FAILED",
                failure_detail=f"SMTP authentication rejected for {user}: {auth_err}",
            )
        except smtplib.SMTPRecipientsRefused as recip_err:
            return ProviderSendResult.failed(
                failure_code="ERR_RECIPIENT_REFUSED",
                failure_detail=f"Recipient {destination} refused by server: {recip_err}",
            )
        except (smtplib.SMTPServerDisconnected, TimeoutError, OSError) as conn_err:
            # Network drop or server timeout mid-send is ambiguous -> UNKNOWN
            return ProviderSendResult.unknown(
                reason=f"SMTP connection timeout or interruption: {conn_err}"
            )
        except Exception as exc:
            return ProviderSendResult.unknown(
                reason=f"Unexpected error during SMTP transmission: {exc}"
            )
