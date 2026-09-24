from pathlib import Path

import pytest

from app.infrastructure.providers.session_store import SessionStore


def test_id_normalization_and_path_creation(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    assert store.normalise_id("Sender/One") == "senderone"
    assert store.normalise_id("///") == "default_sender"
    expected = store.session_dir_path("sender_one")
    assert not expected.exists()
    assert store.get_session_dir("sender_one") == expected
    assert expected.is_dir()


def test_rename_missing_source_returns_destination(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    assert store.rename_session_dir("missing", "target") == store.session_dir_path("target")


def test_rename_merges_existing_profile_and_removes_source(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    source = store.get_session_dir("temp")
    (source / "nested").mkdir()
    (source / "nested" / "cookies.sqlite").write_bytes(b"cookies")
    (source / "profile.json").write_text("profile", encoding="utf-8")
    destination = store.get_session_dir("final")
    (destination / "existing.txt").write_text("keep", encoding="utf-8")

    result = store.rename_session_dir("temp", "final")

    assert result == destination
    assert not source.exists()
    assert (destination / "nested" / "cookies.sqlite").read_bytes() == b"cookies"
    assert (destination / "profile.json").read_text(encoding="utf-8") == "profile"
    assert (destination / "existing.txt").exists()


def test_rename_retries_windows_lock_errors(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "sessions")
    source = store.get_session_dir("source")
    source_file = source / "file.txt"
    source_file.write_text("data", encoding="utf-8")
    attempts = {"count": 0}
    original_rename = Path.rename

    def flaky_rename(path, target):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("locked")
        original_rename(path, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    monkeypatch.setattr("app.infrastructure.providers.session_store.time.sleep", lambda _: None)

    result = store.rename_session_dir("source", "destination")

    assert result == store.session_dir_path("destination")
    assert attempts["count"] == 3
    assert result.is_dir()


def test_rename_raises_after_retry_limit(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "sessions")
    store.get_session_dir("source")
    monkeypatch.setattr(Path, "rename", lambda *_args: (_ for _ in ()).throw(OSError("still locked")))
    monkeypatch.setattr("app.infrastructure.providers.session_store.time.sleep", lambda _: None)

    with pytest.raises(OSError, match="still locked"):
        store.rename_session_dir("source", "destination")


def test_remove_missing_and_failed_removal_are_non_fatal(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    store.remove_session_dir("missing")
    store.get_session_dir("temporary")

    def fail_remove(_path):
        raise OSError("in use")

    monkeypatch.setattr("app.infrastructure.providers.session_store.shutil.rmtree", fail_remove)
    store.remove_session_dir("temporary")
    assert store.session_dir_path("temporary").exists()


def test_temp_ids_and_cookie_backed_session_detection(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    assert store.is_temp_id("tmp_auth_123") is True
    assert store.is_temp_id("sender_123") is False
    session = store.get_session_dir("sender")
    assert store.has_persisted_session("sender") is False
    (session / "cookies.sqlite").write_bytes(b"cookies")
    assert store.has_persisted_session("sender") is True
