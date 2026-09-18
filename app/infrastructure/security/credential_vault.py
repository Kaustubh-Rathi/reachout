"""Secure Local Encrypted Credential Vault.

Provides persistent, encrypted storage for SMTP and provider secrets without storing
plaintext credentials in database rows, environment dumps, or ordinary application logs.
Uses HMAC-SHA256 authenticated encryption with PBKDF2 key derivation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Dict, List, Optional

from app.domain.errors import ConfigurationError, DataIntegrityError

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_VAULT_PATH = ROOT_DIR / ".sessions" / "credentials" / "smtp_vault.enc"
DEFAULT_KEY_PATH = ROOT_DIR / ".sessions" / ".vault_key"


def _derive_keys(master_key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive independent encryption and HMAC authentication keys using PBKDF2."""
    enc_key = hashlib.pbkdf2_hmac("sha256", master_key, salt + b"_enc", 100000, dklen=32)
    mac_key = hashlib.pbkdf2_hmac("sha256", master_key, salt + b"_mac", 100000, dklen=32)
    return enc_key, mac_key


def _keystream(key: bytes, iv: bytes, length: int) -> bytes:
    """Generate deterministic pseudo-random keystream using HMAC-SHA256 in counter mode."""
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        block = hmac.new(key, iv + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        stream.extend(block)
        counter += 1
    return bytes(stream[:length])


def _encrypt_payload(data: bytes, master_key: bytes) -> bytes:
    """Encrypt and MAC data: salt (16B) || iv (16B) || ciphertext (NB) || mac (32B)."""
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(16)
    enc_key, mac_key = _derive_keys(master_key, salt)
    ks = _keystream(enc_key, iv, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, ks, strict=False))
    tag = hmac.new(mac_key, salt + iv + ciphertext, hashlib.sha256).digest()
    return salt + iv + ciphertext + tag


def _decrypt_payload(blob: bytes, master_key: bytes) -> bytes:
    """Verify MAC and decrypt data."""
    if len(blob) < 16 + 16 + 32:
        raise ConfigurationError("Invalid encrypted payload: too short")
    salt = blob[:16]
    iv = blob[16:32]
    tag = blob[-32:]
    ciphertext = blob[32:-32]
    enc_key, mac_key = _derive_keys(master_key, salt)
    expected_tag = hmac.new(mac_key, salt + iv + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ConfigurationError("Decryption error: MAC integrity verification failed")
    ks = _keystream(enc_key, iv, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, ks, strict=False))


class CredentialVault:
    """Thread-safe encrypted local credential storage."""

    def __init__(
        self,
        vault_path: Optional[Path] = None,
        key_path: Optional[Path] = None,
        master_secret: Optional[str] = None,
    ) -> None:
        self.vault_path = vault_path or DEFAULT_VAULT_PATH
        self.key_path = key_path or DEFAULT_KEY_PATH
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, Dict[str, str]]] = None
        self._master_secret = master_secret

    def _get_master_key(self) -> bytes:
        """Resolve or generate machine/environment master key."""
        if self._master_secret:
            return hashlib.sha256(self._master_secret.encode("utf-8")).digest()

        env_secret = os.environ.get("REACHOUT_VAULT_SECRET")
        if env_secret:
            return hashlib.sha256(env_secret.strip().encode("utf-8")).digest()

        # Load or generate local key file
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return self.key_path.read_bytes()

        new_key = secrets.token_bytes(32)
        # Persist the generated key BEFORE returning it. If it cannot be written we
        # MUST raise: returning an unpersisted key would make every stored credential
        # permanently undecryptable after a restart (silent data loss).
        self.key_path.write_bytes(new_key)
        if hasattr(os, "chmod"):
            try:
                os.chmod(self.key_path, 0o600)
            except OSError as exc:
                # Permissions are best-effort on some platforms; not data-loss critical.
                logger.warning("Could not chmod key file %s: %s", self.key_path, exc)
        return new_key

    def _load_vault(self) -> Dict[str, Dict[str, str]]:
        """Load and decrypt vault contents into memory cache."""
        if not self.vault_path.exists():
            return {}
        try:
            raw_blob = self.vault_path.read_bytes()
            master_key = self._get_master_key()
            decrypted = _decrypt_payload(raw_blob, master_key)
            return json.loads(decrypted.decode("utf-8"))
        except (ConfigurationError, DataIntegrityError):
            raise
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            # A corrupt/undecryptable vault is an integrity violation, NOT "no
            # credentials". Returning {} silently would let a subsequent save overwrite
            # the vault with an empty cache (data loss). Surface it instead.
            raise DataIntegrityError(
                f"Credential vault is corrupt or undecryptable at {self.vault_path}: {exc}"
            ) from exc

    def _save_vault(self, data: Dict[str, Dict[str, str]]) -> None:
        """Encrypt and persist vault contents to disk atomically."""
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        master_key = self._get_master_key()
        serialized = json.dumps(data).encode("utf-8")
        encrypted = _encrypt_payload(serialized, master_key)

        tmp_file = self.vault_path.with_suffix(".tmp")
        tmp_file.write_bytes(encrypted)
        tmp_file.replace(self.vault_path)

    def save_credentials(self, sender_account_id: str, creds: Dict[str, str]) -> None:
        """Securely store credentials for a sender identity."""
        with self._lock:
            if self._cache is None:
                self._cache = self._load_vault()
            self._cache[sender_account_id] = {k: str(v) for k, v in creds.items()}
            self._save_vault(self._cache)

    def get_credentials(self, sender_account_id: str) -> Optional[Dict[str, str]]:
        """Retrieve credentials for a sender identity."""
        with self._lock:
            if self._cache is None:
                self._cache = self._load_vault()
            res = self._cache.get(sender_account_id)
            return dict(res) if res is not None else None

    def list_senders_with_credentials(self) -> List[str]:
        """List all sender IDs that have stored credentials."""
        with self._lock:
            if self._cache is None:
                self._cache = self._load_vault()
            return list(self._cache.keys())


# Canonical singleton vault instance
default_credential_vault = CredentialVault()
