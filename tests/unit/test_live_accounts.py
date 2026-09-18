import sqlite3

import pytest

from tests.e2e.live.live_accounts import LiveAccount, LiveAccountError, discover_accounts, select_pair


def account(sender_id="EMAIL_1", identity="first@example.com"):
    return LiveAccount(sender_id, identity, "EMAIL", "smtp")


def test_pair_uses_registered_ids_and_distinct_ready_accounts():
    first, second = account(), account("EMAIL_2", "second@example.com")
    pair = select_pair([second, first], "EMAIL", lambda _: True, {})
    assert pair.sender == first
    assert pair.recipient == second.identity


def test_explicit_sender_is_not_silently_replaced():
    with pytest.raises(LiveAccountError, match="not an ACTIVE"):
        select_pair([account()], "EMAIL", lambda _: True, {"LIVE_TEST_EMAIL_SENDER_ID": "missing"})
    with pytest.raises(LiveAccountError, match="not ready"):
        select_pair([account()], "EMAIL", lambda _: False, {"LIVE_TEST_EMAIL_SENDER_ID": "EMAIL_1"})


def test_no_usable_accounts_returns_none():
    assert select_pair([account()], "EMAIL", lambda _: False, {}) is None


def test_one_account_requires_recipient():
    with pytest.raises(LiveAccountError, match="LIVE_TEST_EMAIL_RECIPIENT"):
        select_pair([account()], "EMAIL", lambda _: True, {})
    pair = select_pair([account()], "EMAIL", lambda _: True, {"LIVE_TEST_EMAIL_RECIPIENT": "other@example.com"})
    assert pair.recipient == "other@example.com"


def test_self_send_is_rejected_after_normalization():
    with pytest.raises(LiveAccountError, match="different accounts"):
        select_pair([account()], "EMAIL", lambda _: True, {"LIVE_TEST_EMAIL_RECIPIENT": "FIRST@example.com"})


def test_unready_account_is_not_selected_as_recipient():
    with pytest.raises(LiveAccountError, match="Only one"):
        select_pair([account(), account("EMAIL_2", "second@example.com")], "EMAIL", lambda a: a.id == "EMAIL_1", {})


def test_discovery_is_read_only_and_filters_registry(tmp_path):
    path = tmp_path / "accounts.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE sender_accounts (id TEXT, identity TEXT, channel TEXT, provider TEXT, status TEXT)"
    )
    connection.executemany(
        "INSERT INTO sender_accounts VALUES (?, ?, ?, ?, ?)",
        [
            ("EMAIL_1", "first@example.com", "EMAIL", "smtp", "ACTIVE"),
            ("EMAIL_2", "second@example.com", "EMAIL", "smtp", "INACTIVE"),
            ("tmp_auth_test", "third@example.com", "EMAIL", "smtp", "ACTIVE"),
            ("fake", "fake@example.com", "EMAIL", "mock", "ACTIVE"),
        ],
    )
    connection.commit()
    connection.close()
    original = path.read_bytes()
    assert discover_accounts(f"sqlite:///{path.as_posix()}", "EMAIL") == [account()]
    assert path.read_bytes() == original


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    assert discover_accounts(f"sqlite:///{path.as_posix()}", "EMAIL") == []
    assert not path.exists()
