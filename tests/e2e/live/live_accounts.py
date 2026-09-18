from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from sqlalchemy.engine import make_url


class LiveAccountError(ValueError):
    pass


@dataclass(frozen=True)
class LiveAccount:
    id: str
    identity: str
    channel: str
    provider: str


@dataclass(frozen=True)
class LivePair:
    sender: LiveAccount
    recipient: str


def normalize_identity(channel: str, value: str) -> str:
    value = value.strip()
    if channel == "WHATSAPP":
        if not re.fullmatch(r"\+?[0-9 ()-]+", value):
            raise LiveAccountError("WhatsApp identity must be an international phone number")
        value = re.sub(r"\D", "", value)
        if not 10 <= len(value) <= 15:
            raise LiveAccountError("WhatsApp identity has an invalid digit count")
        return value
    if channel != "EMAIL" or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise LiveAccountError("Invalid email identity or channel")
    return value.casefold()


def discover_accounts(database_url: str, channel: str) -> list[LiveAccount]:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise LiveAccountError("Live account discovery requires the application's SQLite database")
    path = Path(url.database).resolve()
    if not path.is_file():
        return []
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT id, identity, channel, provider FROM sender_accounts "
            "WHERE channel = ? AND status = 'ACTIVE' ORDER BY id",
            (channel,),
        ).fetchall()
    finally:
        connection.close()
    provider = {"WHATSAPP": "playwright_whatsapp", "EMAIL": "smtp"}.get(channel)
    accounts = []
    for sender_id, identity, account_channel, account_provider in rows:
        if account_provider != provider or sender_id.lower().startswith(("tmp_", "wa_session_")):
            continue
        accounts.append(
            LiveAccount(sender_id, normalize_identity(channel, identity), account_channel, account_provider)
        )
    return accounts


def select_pair(
    accounts: list[LiveAccount],
    channel: str,
    ready: Callable[[LiveAccount], bool],
    environment: Mapping[str, str] | None = None,
) -> LivePair | None:
    environment = os.environ if environment is None else environment
    sender_id = environment.get(f"LIVE_TEST_{channel}_SENDER_ID", "").strip()
    candidates = sorted((account for account in accounts if account.channel == channel), key=lambda account: account.id)
    if sender_id and not any(account.id == sender_id for account in candidates):
        raise LiveAccountError(f"Requested {channel} sender is not an ACTIVE registered account: {sender_id}")
    usable = []
    for account in candidates:
        if ready(account):
            usable.append(account)
        elif account.id == sender_id:
            raise LiveAccountError(f"Requested sender is not ready: {sender_id}")
    sender = next((account for account in usable if not sender_id or account.id == sender_id), None)
    if sender is None:
        return None
    explicit = environment.get(f"LIVE_TEST_{channel}_RECIPIENT", "").strip()
    recipient = (
        normalize_identity(channel, explicit)
        if explicit
        else next((account.identity for account in usable if account.identity != sender.identity), "")
    )
    if recipient == sender.identity:
        raise LiveAccountError("Live test sender and recipient must be different accounts")
    if not recipient:
        raise LiveAccountError(
            f"Only one usable {channel} account; set LIVE_TEST_{channel}_RECIPIENT to your recipient"
        )
    return LivePair(sender, recipient)
