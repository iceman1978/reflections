"""Each person's lock: passphrase, recovery key, and unlocked sessions.

While a journal is locked, its data key exists nowhere in memory. Unlocking
(with the passphrase or the recovery key) unwraps the key into a *session*
for that browser; locking, signing out, or a period without activity forgets
it. Several people (on the web) can have sessions at once, each holding only
their own key.

`use(session)` marks which person's journal the current request is working
on; store.py etc. read it through `current()`.
"""
import contextvars
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from . import crypto, users

log = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8


class Locked(Exception):
    """This journal is locked (or this browser hasn't unlocked it)."""


class VaultError(Exception):
    """A problem with a passphrase or recovery key, safe to show the person."""


@dataclass
class Session:
    user_id: str
    key: bytes = field(repr=False)
    last_activity: float = field(default_factory=time.monotonic)


_lock = threading.RLock()
_sessions = {}                                   # token -> Session
_current = contextvars.ContextVar("journal_session", default=None)
_on_lock = []                                    # callbacks(user_id) when a person's last session closes


def on_lock(fn):
    _on_lock.append(fn)
    return fn


# ---- The current request's journal ----------------------------------------

def use(session):
    """Work on this session's journal for the rest of the request (or None)."""
    _current.set(session)


def current():
    s = _current.get()
    if s is None:
        raise Locked()
    return s


def data_key():
    return current().key


def user_id():
    return current().user_id


def session(token):
    """The unlocked session for a browser's token, or None."""
    return _sessions.get(token) if token else None


def touch(token):
    s = session(token)
    if s:
        s.last_activity = time.monotonic()


# ---- State ----------------------------------------------------------------

def is_set_up(uid):
    user = users.get(uid)
    return bool(user and user["key_password"])


def is_unlocked(uid):
    return any(s.user_id == uid for s in _sessions.values())


def _open(uid, key):
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = Session(uid, key)
    return token


def lock(token, reason="locked"):
    with _lock:
        s = _sessions.pop(token, None)
    if s:
        log.info("Journal %s (%s)", reason, s.user_id)
        if not is_unlocked(s.user_id):
            for fn in _on_lock:
                fn(s.user_id)


def lock_user(uid, reason="locked"):
    for token in [t for t, s in list(_sessions.items()) if s.user_id == uid]:
        lock(token, reason)


def lock_all(reason="locked"):
    for token in list(_sessions):
        lock(token, reason)


def _check_new_password(password):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise VaultError(f"Choose a passphrase of at least {MIN_PASSWORD_LENGTH} characters.")


# ---- Setting up, unlocking, recovering ------------------------------------

def set_up(uid, password):
    """First time: create this person's data key and wrap it. Returns (recovery_key, token)."""
    with _lock:
        if is_set_up(uid):
            raise VaultError("This journal already has a passphrase.")
        _check_new_password(password)
        key = crypto.new_data_key()
        recovery = crypto.new_recovery_key()
        users.set_keys(uid, key_recovery=crypto.wrap_data_key(key, recovery))
        users.set_keys(uid, key_password=crypto.wrap_data_key(key, password))  # last: marks setup complete
        log.info("Journal passphrase set up (%s)", uid)
        return recovery, _open(uid, key)


# After too many wrong guesses in a row, pause unlocking for a while.
MAX_FAILURES = 10
PAUSE_SECONDS = 15 * 60
_failures = {}  # user_id -> [times of recent failures]


def _check_not_paused(uid):
    recent = [t for t in _failures.get(uid, []) if time.monotonic() - t < PAUSE_SECONDS]
    _failures[uid] = recent
    if len(recent) >= MAX_FAILURES:
        raise VaultError("Too many wrong attempts. Unlocking is paused for 15 minutes.")


def _failed(uid):
    _failures.setdefault(uid, []).append(time.monotonic())
    time.sleep(1)  # slow down guessing


def unlock(uid, password):
    user = users.get(uid)
    if not user or not user["key_password"]:
        raise VaultError("This journal hasn't been set up yet.")
    _check_not_paused(uid)
    try:
        key = crypto.unwrap_data_key(user["key_password"], password or "")
    except crypto.WrongKey:
        _failed(uid)
        log.info("Unlock failed: wrong passphrase (%s)", uid)
        raise VaultError("That passphrase didn't open the journal.") from None
    _failures.pop(uid, None)
    log.info("Journal unlocked (%s)", uid)
    return _open(uid, key)


def recover(uid, recovery_key, new_password):
    """Unlock with the recovery key and set a new passphrase. Returns a token."""
    _check_new_password(new_password)
    _check_not_paused(uid)
    user = users.get(uid)
    try:
        key = crypto.unwrap_data_key(user["key_recovery"], crypto.normalise_recovery_key(recovery_key))
    except (crypto.WrongKey, TypeError, KeyError):
        _failed(uid)
        log.info("Recovery failed: wrong recovery key (%s)", uid)
        raise VaultError("That recovery key didn't open the journal. Check it and try again.") from None
    _failures.pop(uid, None)
    users.set_keys(uid, key_password=crypto.wrap_data_key(key, new_password))
    log.info("Journal unlocked with the recovery key; new passphrase set (%s)", uid)
    return _open(uid, key)


def change_password(uid, current_pw, new_pw):
    try:
        key = crypto.unwrap_data_key(users.get(uid)["key_password"], current_pw or "")
    except crypto.WrongKey:
        time.sleep(1)
        raise VaultError("Your current passphrase isn't right.") from None
    _check_new_password(new_pw)
    users.set_keys(uid, key_password=crypto.wrap_data_key(key, new_pw))
    log.info("Journal passphrase changed (%s)", uid)


def new_recovery_key(uid, password):
    """Replace the recovery key (the old one stops working). Returns the new one."""
    try:
        key = crypto.unwrap_data_key(users.get(uid)["key_password"], password or "")
    except crypto.WrongKey:
        time.sleep(1)
        raise VaultError("That passphrase isn't right.") from None
    recovery = crypto.new_recovery_key()
    users.set_keys(uid, key_recovery=crypto.wrap_data_key(key, recovery))
    log.info("New recovery key created (%s)", uid)
    return recovery


# ---- Auto-lock ----------------------------------------------------------

def lock_if_idle():
    """Lock sessions idle longer than their owner's chosen time. Returns how many locked."""
    now = time.monotonic()
    locked = 0
    for token, s in list(_sessions.items()):
        minutes = int(users.prefs(s.user_id).get("lock_minutes") or 0)
        if minutes and now - s.last_activity > minutes * 60:
            lock(token, f"locked after {minutes} minutes without activity")
            locked += 1
    return locked


def _watch():
    while True:
        time.sleep(20)
        try:
            lock_if_idle()
        except Exception:
            log.exception("Auto-lock check failed")


def start_auto_lock():
    threading.Thread(target=_watch, name="auto-lock", daemon=True).start()
