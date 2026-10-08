"""Encryption building blocks.

Two layers of keys:
  - a random 256-bit *data key* encrypts every entry (AES-256-GCM);
  - the data key itself is stored only in wrapped (encrypted) form: once
    under a key derived from the password, and once under a key derived
    from the recovery key.

So changing the password re-wraps one small key, nothing else. Later, for a
web version with Google sign-in, the data key could be wrapped by a
server-side key service instead, again without touching the entries.
"""
import base64
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# scrypt cost: ~0.3 s per attempt here, which makes guessing expensive.
# (OWASP's recommendation: N=2^17, r=8, p=1.) Stored with each wrapped key,
# so it can be raised later without breaking existing journals.
DEFAULT_KDF = {"name": "scrypt", "n": 2 ** 17, "r": 8, "p": 1}

NONCE_BYTES = 12
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L, U


class WrongKey(Exception):
    """The password or recovery key didn't open the journal."""


def new_data_key():
    return AESGCM.generate_key(bit_length=256)


def derive_key(secret, salt, kdf=DEFAULT_KDF):
    if kdf.get("name") != "scrypt":
        raise ValueError(f"Unknown key derivation: {kdf.get('name')}")
    return Scrypt(salt=salt, length=32, n=kdf["n"], r=kdf["r"], p=kdf["p"]).derive(secret.encode("utf-8"))


def encrypt(key, plaintext: bytes, context: bytes = b"") -> bytes:
    """AES-256-GCM. `context` is authenticated but not stored (e.g. the entry ID,
    so an encrypted entry can't be swapped into another entry's row)."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, context)


def decrypt(key, blob: bytes, context: bytes = b"") -> bytes:
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], context)
    except InvalidTag:
        raise WrongKey() from None


def wrap_data_key(data_key, secret, kdf=DEFAULT_KDF):
    """Encrypt the data key under a password or recovery key. Returns a dict to store."""
    salt = os.urandom(16)
    wrapping_key = derive_key(secret, salt, kdf)
    return {
        "kdf": kdf,
        "salt": base64.b64encode(salt).decode(),
        "wrapped": base64.b64encode(encrypt(wrapping_key, data_key, b"data-key")).decode(),
    }


def unwrap_data_key(stored, secret):
    """Raises WrongKey if the password / recovery key is wrong."""
    wrapping_key = derive_key(secret, base64.b64decode(stored["salt"]), stored["kdf"])
    return decrypt(wrapping_key, base64.b64decode(stored["wrapped"]), b"data-key")


def new_recovery_key():
    """24 random characters (~118 bits) in groups of four: 7KQ2-M9XD-…"""
    chars = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(24))
    return "-".join(chars[i:i + 4] for i in range(0, 24, 4))


def normalise_recovery_key(text):
    """Accept the recovery key however it's typed: any case, with or without
    spaces or dashes. (Its alphabet has no easily confused characters.)"""
    cleaned = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    return "-".join(cleaned[i:i + 4] for i in range(0, len(cleaned), 4))
