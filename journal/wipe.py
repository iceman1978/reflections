"""Wiping the journal, guarded by a typed confirmation.

The server picks a random three-digit number; the page shows it, and the wipe
only happens if the person types it back digit by digit in words
("352" -> "three five two"). Each number allows one attempt and expires after
ten minutes, so a stray request can't wipe anything.
"""
import logging
import re
import secrets
import threading
import time

from . import store

log = logging.getLogger(__name__)

DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
EXPIRY_SECONDS = 600

_lock = threading.Lock()
_challenges = {}  # id -> (digits, created, user_id)


def new_challenge(user_id=None):
    number = str(secrets.randbelow(900) + 100)  # 100–999
    challenge_id = secrets.token_hex(8)
    with _lock:
        now = time.time()
        for cid in [c for c, (_, t, _u) in _challenges.items() if now - t > EXPIRY_SECONDS]:
            del _challenges[cid]
        _challenges[challenge_id] = (number, now, user_id)
    return {"id": challenge_id, "number": number}


def answer_matches(number, answer):
    """'three five two', 'Three-Five-Two', 'three, five, two' all match 352."""
    words = re.findall(r"[a-z]+", (answer or "").lower())
    return words == [DIGIT_WORDS[int(d)] for d in number]


def wipe(challenge_id, answer, user_id=None):
    """Wipe the current person's journal. Returns (ok, message, details)."""
    with _lock:
        challenge = _challenges.pop(challenge_id, None)  # one attempt per number
    if not challenge or time.time() - challenge[1] > EXPIRY_SECONDS or challenge[2] != user_id:
        return False, "That confirmation has expired. Start again to get a new number.", {}
    number = challenge[0]
    if not answer_matches(number, answer):
        log.info("Wipe cancelled: confirmation didn't match")
        return False, "That didn't match, so nothing was deleted.", {}

    count, failed = store.wipe_all_entries()  # this person's entries, draft, and their rows in backups
    log.warning("Journal wiped: %d entries deleted", count)
    return True, f"Your journal has been wiped ({count} entr{'y' if count == 1 else 'ies'} deleted).", {
        "deleted": count, "failed_backups": failed,
    }
