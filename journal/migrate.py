"""One-time move from the old, unencrypted storage into the encrypted database.

The old version kept each entry as a readable JSON file in data/entries/,
mirrored everything into journal.xlsx, and kept dated workbook backups.
When the journal password is first set, the entries (and draft) are imported,
every imported entry is decrypted again and compared with the original, and
only then are the old files deleted. Anything that can't be deleted yet (say,
journal.xlsx open in Excel) is retried each time the app starts or unlocks.
"""
import json
import logging
import shutil

from . import db, store
from .config import BACKUP_DIR, DATA_DIR, LEGACY_DRAFT_PATH, LEGACY_ENTRIES_DIR, LEGACY_EXCEL_PATH

log = logging.getLogger(__name__)

PENDING_KEY = "legacy_cleanup_pending"


def legacy_entry_files():
    return sorted(LEGACY_ENTRIES_DIR.glob("*.json")) if LEGACY_ENTRIES_DIR.exists() else []


def legacy_count():
    return len(legacy_entry_files())


def _legacy_paths():
    """Every unencrypted leftover of the old version."""
    paths = [LEGACY_ENTRIES_DIR, LEGACY_DRAFT_PATH, LEGACY_EXCEL_PATH,
             LEGACY_EXCEL_PATH.with_name(LEGACY_EXCEL_PATH.stem + ".saving.xlsx"),
             DATA_DIR / "deleted_entry_ids.json", DATA_DIR / ".no-backup-of-old-workbook",
             DATA_DIR / ".excel_needs_update"]
    for folder in {BACKUP_DIR, LEGACY_EXCEL_PATH.parent / "backups"}:
        if folder.exists():
            paths += list(folder.glob("*.xlsx"))                    # old workbook backups
            paths += [p for p in folder.glob("entries-before-*") if p.is_dir()]  # old repair-tool backups
    return [p for p in paths if p.exists()]


def import_legacy():
    """Import old entries and draft into the (unlocked) encrypted database.

    Raises RuntimeError if anything doesn't check out, in which case the old
    files are left untouched. Returns the number of entries imported.
    """
    originals = []
    for path in legacy_entry_files():
        originals.append(json.loads(path.read_text(encoding="utf-8")))

    imported = []
    for old in originals:
        entry = dict(old, id=store.new_id())   # random IDs (old ones contained the time)
        store.put_entry(entry)
        imported.append((old, entry["id"]))

    if LEGACY_DRAFT_PATH.exists():
        try:
            d = json.loads(LEGACY_DRAFT_PATH.read_text(encoding="utf-8"))
            if (d.get("text") or "").strip() or d.get("prompt"):
                store.save_draft(d.get("text") or "", d.get("prompt"), d.get("title"))
        except (OSError, ValueError):
            log.warning("Old draft couldn't be read; skipped")

    # Verify: read every entry back from the database, decrypt, compare.
    store.reload()
    for old, new_id in imported:
        back = store.get_entry(new_id)
        if {**back, "id": old["id"]} != old:
            raise RuntimeError("An imported entry didn't read back exactly; the old files were kept.")
    log.info("Imported %d unencrypted entries into the encrypted journal", len(imported))
    return len(imported)


def delete_legacy():
    """Delete the old unencrypted files. Remembers anything that couldn't go yet."""
    failed = []
    for path in _legacy_paths():
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        except OSError:
            failed.append(str(path))
    db.set_meta(PENDING_KEY, failed)
    if failed:
        log.warning("%d unencrypted file(s) couldn't be deleted yet (open elsewhere?); will retry", len(failed))
    else:
        log.info("Old unencrypted files deleted")
    return failed


def retry_pending():
    """Called at start-up and unlock: finish deleting old files if any were stuck."""
    if not db.get_meta(PENDING_KEY):
        return []
    return delete_legacy()
