"""Passkeys: unlock a journal with fingerprint, face or device PIN instead of
typing the passphrase.

A passkey can't simply *be* the key to the journal: what it proves (that
you're you) is known to the server, so anything derived from it would be too.
Instead we use the passkey's PRF extension: given a salt, the passkey
produces a 32-byte secret that only that passkey can produce (and only after
your fingerprint, face or PIN). The browser sends that secret when you
unlock; the server uses it to unwrap your data key, then forgets it, exactly
as it does with your passphrase. The server stores only the salt and the
wrapped data key, so nobody who reads the database can unlock with it.

Each passkey is one more wrapping of the same data key, alongside the
passphrase and the recovery key. Removing a passkey deletes its wrapping.

Because the secret itself proves you hold the passkey (a wrong one simply
fails to unwrap the key), the server doesn't need to check WebAuthn
signatures for this: you're already signed in with Google, and the passkey
only stands in for the passphrase.
"""
import base64
import binascii
import json
from datetime import datetime

from . import crypto, db
from .config import TZ

MAX_PER_PERSON = 10
SECRET_BYTES = 32


class PasskeyProblem(Exception):
    """Something about the request, safe to show the person."""


def b64url_decode(text):
    text = (text or "").strip()
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise PasskeyProblem("That passkey reply wasn't readable. Please try again.") from None


def _now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def list_for(user_id):
    """This person's passkeys, newest first (for Settings)."""
    con = db.connect()
    try:
        rows = con.execute("SELECT id, label, created_at, last_used_at FROM passkeys WHERE user_id = ? "
                           "ORDER BY created_at DESC", (user_id,)).fetchall()
    finally:
        con.close()
    return [{"id": r[0], "label": r[1], "created_at": r[2], "last_used_at": r[3]} for r in rows]


def unlock_options(user_id):
    """What the browser needs to ask for each passkey's secret: its ID and salt."""
    con = db.connect()
    try:
        rows = con.execute("SELECT id, prf_salt FROM passkeys WHERE user_id = ?", (user_id,)).fetchall()
    finally:
        con.close()
    return [{"id": r[0], "salt": r[1]} for r in rows]


def add(user_id, data_key, credential_id, prf_salt, prf_secret, label):
    """Store a new passkey's wrapping of the data key."""
    if len(b64url_decode(credential_id)) < 16 or len(credential_id) > 1400:
        raise PasskeyProblem("That passkey's ID wasn't valid. Please try again.")
    if len(b64url_decode(prf_salt)) != SECRET_BYTES:
        raise PasskeyProblem("That passkey reply wasn't complete. Please try again.")
    secret = b64url_decode(prf_secret)
    if len(secret) != SECRET_BYTES:
        raise PasskeyProblem("This device didn't give the passkey's secret, so it can't unlock the journal.")
    label = " ".join((label or "").split())[:60] or "Passkey"
    wrapped = json.dumps(crypto.wrap_data_key_with_secret(data_key, secret))
    with db.write_lock:
        con = db.connect()
        try:
            if con.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_PER_PERSON:
                raise PasskeyProblem(f"You already have {MAX_PER_PERSON} passkeys. Remove one first.")
            with con:
                con.execute("INSERT OR REPLACE INTO passkeys (id, user_id, label, prf_salt, key_wrapped, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)", (credential_id, user_id, label, prf_salt, wrapped, _now()))
        finally:
            con.close()


def unwrap(user_id, credential_id, prf_secret):
    """The data key, if this passkey's secret opens it. Raises crypto.WrongKey
    (wrong secret) or KeyError (no such passkey for this person)."""
    con = db.connect()
    try:
        row = con.execute("SELECT key_wrapped FROM passkeys WHERE id = ? AND user_id = ?",
                          (credential_id or "", user_id)).fetchone()
    finally:
        con.close()
    if not row:
        raise KeyError(credential_id)
    key = crypto.unwrap_data_key_with_secret(json.loads(row[0]), b64url_decode(prf_secret))
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("UPDATE passkeys SET last_used_at = ? WHERE id = ?", (_now(), credential_id))
        finally:
            con.close()
    return key


def remove(user_id, credential_id):
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                changed = con.execute("DELETE FROM passkeys WHERE id = ? AND user_id = ?",
                                      (credential_id, user_id)).rowcount
        finally:
            con.close()
    if not changed:
        raise KeyError(credential_id)
