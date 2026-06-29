"""Optional passphrase encryption for backups.

A backup is plain JSON by default. With a passphrase it's wrapped in an envelope: the JSON is
encrypted with Fernet (AES-128-CBC + HMAC, authenticated) under a key derived from the passphrase
via PBKDF2-HMAC-SHA256 with a random per-backup salt. Restore detects the envelope and decrypts;
a wrong passphrase or tampering fails cleanly with ``BackupDecryptError``.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 600_000


class BackupDecryptError(ValueError):
    """Raised when an encrypted backup can't be decrypted (wrong passphrase or corrupt file)."""


def is_encrypted(data) -> bool:
    return isinstance(data, dict) and bool(data.get("scryme_encrypted"))


def _derive_key(passphrase: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_backup(data: dict, passphrase: str) -> dict:
    """Wrap a backup dict in an encrypted envelope."""
    salt = os.urandom(16)
    token = Fernet(_derive_key(passphrase, salt)).encrypt(
        json.dumps(data, separators=(",", ":")).encode("utf-8")
    )
    return {
        "scryme_encrypted": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": token.decode("ascii"),
    }


def decrypt_backup(envelope: dict, passphrase: str) -> dict:
    """Decrypt an envelope back to the backup dict, or raise BackupDecryptError."""
    if not passphrase:
        raise BackupDecryptError("This backup is encrypted — a passphrase is required.")
    try:
        salt = base64.b64decode(envelope["salt"])
        key = _derive_key(passphrase, salt, int(envelope.get("iterations", ITERATIONS)))
        raw = Fernet(key).decrypt(envelope["ciphertext"].encode("ascii"))
    except (InvalidToken, KeyError, ValueError) as exc:
        raise BackupDecryptError("Wrong passphrase, or the backup file is corrupt.") from exc
    return json.loads(raw)
