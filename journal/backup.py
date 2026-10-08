"""Daily backups of the journal database (data/backups/journal-YYYY-MM-DD.db).

They're copies of the encrypted database, so they're encrypted too and need
the same password. One is taken on the first change of each day; the newest
`backups_to_keep` are kept. A permanently deleted entry is removed from
every backup as well.
"""
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime

from . import db
from .config import BACKUP_DIR, TZ, settings

log = logging.getLogger(__name__)
_lock = threading.Lock()


def _backups():
    """The dated daily backups (the ones that rotate)."""
    return sorted(BACKUP_DIR.glob("journal-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].db"))


def daily():
    """Take today's backup if there isn't one yet."""
    target = BACKUP_DIR / f"journal-{datetime.now(TZ):%Y-%m-%d}.db"
    if target.exists():
        return
    with _lock:
        if target.exists():
            return
        tmp = target.with_suffix(".db.tmp")
        try:
            src = db.connect()
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)                         # consistent copy, even mid-write
                dst.execute("PRAGMA journal_mode = DELETE")  # a single self-contained file
            finally:
                dst.close()
                src.close()
            os.replace(tmp, target)
        except Exception:
            log.exception("Could not back up the journal")
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            return
        keep = max(1, int(settings["backups_to_keep"]))
        for old in _backups()[:-keep]:
            try:
                old.unlink()
            except OSError:
                log.warning("Could not remove old backup %s", old.name)


def _all_backup_files():
    """Every backup database, including the copy kept when accounts were introduced."""
    return sorted(BACKUP_DIR.glob("*.db"))


def _clean(path, statements):
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA secure_delete = ON")
        with con:
            for sql, args in statements(con):
                con.execute(sql, args)
        con.execute("VACUUM")
    finally:
        con.close()


def _has_owner_column(con):
    return "user_id" in {row[1] for row in con.execute("PRAGMA table_info(entries)")}


def forget_entry(entry_id):
    """Remove a permanently deleted entry from every backup.
    Returns the names of any backups that couldn't be cleaned."""
    failed = []
    for path in _all_backup_files():
        try:
            _clean(path, lambda con: [("DELETE FROM entries WHERE id = ?", (entry_id,))])
        except Exception:
            log.exception("Could not remove an entry from backup %s", path.name)
            failed.append(path.name)
    return failed


def forget_user(user_id):
    """Remove one person's entries and draft from every backup (part of wiping
    their journal); other people's entries stay. Returns names that couldn't be cleaned."""
    def statements(con):
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if _has_owner_column(con):
            out = [("DELETE FROM entries WHERE user_id = ?", (user_id,))]
            if "drafts" in tables:
                out.append(("DELETE FROM drafts WHERE user_id = ?", (user_id,)))
            return out
        # A backup from before accounts: everything in it was the local journal's.
        if user_id != db.LOCAL_USER:
            return []
        return [("DELETE FROM entries", ())] + ([("DELETE FROM draft", ())] if "draft" in tables else [])

    failed = []
    for path in _all_backup_files():
        try:
            _clean(path, statements)
        except Exception:
            log.exception("Could not clean backup %s", path.name)
            failed.append(path.name)
    # Leftovers from before encryption (old workbook backups etc.) go too.
    if user_id == db.LOCAL_USER:
        for path in BACKUP_DIR.iterdir():
            if path.suffix != ".db":
                try:
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
                except OSError:
                    failed.append(path.name)
    return failed
