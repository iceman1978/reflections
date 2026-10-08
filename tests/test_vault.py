"""Tests for the encrypted journal and accounts. Run (the tests use a throwaway data folder):

    set JOURNAL_DATA_DIR to an empty temporary folder, then
    uv run python -m unittest discover tests
"""
import csv
import io
import json
import os
import shutil
import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if not os.environ.get("JOURNAL_DATA_DIR"):
    raise unittest.SkipTest("Set JOURNAL_DATA_DIR to a throwaway folder to run the vault tests.")

from journal import (activity, config, crypto, db, export, feedback, importer, migrate, sources, store, usage, users,  # noqa: E402
                     vault, wipe)
from journal.db import LOCAL_USER  # noqa: E402

PASSWORD = "correct horse battery"


def fresh_journal():
    vault.lock_all()
    vault.use(None)
    activity._seen.clear()
    vault._failures.clear()
    for p in config.DATA_DIR.glob("journal.db*"):
        p.unlink()
    for p in config.BACKUP_DIR.glob("*"):
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    db._initialised.clear()


def raw_bytes():
    return b"".join(p.read_bytes() for p in config.DATA_DIR.glob("journal.db*"))


def unlock_as(uid, password=PASSWORD):
    token = vault.unlock(uid, password)
    vault.use(vault.session(token))
    return token


def set_up_as(uid, password=PASSWORD):
    recovery, token = vault.set_up(uid, password)
    vault.use(vault.session(token))
    return recovery, token


class VaultTests(unittest.TestCase):
    def setUp(self):
        fresh_journal()
        self.uid = users.ensure_local()
        self.recovery, self.token = set_up_as(self.uid)

    def lock(self):
        vault.lock_all()
        vault.use(None)

    def test_nothing_readable_on_disk(self):
        store.create_entry("A very particular phrase: marmalade lighthouse.", title="Secret title here")
        store.save_draft("Draft about the quiet orchard", None, "Draft title")
        data = raw_bytes()
        for phrase in [b"marmalade", b"lighthouse", b"Secret title", b"orchard", b"Draft title", b'"created_at"']:
            self.assertNotIn(phrase, data, phrase)

    def test_locked_means_no_access(self):
        e = store.create_entry("hello there")
        self.lock()
        with self.assertRaises(vault.Locked):
            store.all_entries()
        with self.assertRaises(vault.Locked):
            store.get_entry(e["id"])

    def test_unlock(self):
        e = store.create_entry("remember this")
        self.lock()
        with self.assertRaises(vault.VaultError):
            vault.unlock(self.uid, "wrong password")
        unlock_as(self.uid)
        self.assertEqual(store.get_entry(e["id"])["text"], "remember this")

    def test_short_password_refused(self):
        fresh_journal()
        uid = users.ensure_local()
        with self.assertRaises(vault.VaultError):
            vault.set_up(uid, "short")

    def test_pause_after_many_wrong_guesses(self):
        self.lock()
        vault._failures[self.uid] = [time.monotonic()] * vault.MAX_FAILURES
        with self.assertRaises(vault.VaultError) as ctx:
            vault.unlock(self.uid, PASSWORD)   # even the right passphrase waits
        self.assertIn("paused", str(ctx.exception))

    def test_recovery_key_sets_new_password(self):
        store.create_entry("still here after recovery")
        self.lock()
        messy = self.recovery.lower().replace("-", " ")  # typed loosely
        vault.recover(self.uid, messy, "a brand new password")
        self.lock()
        with self.assertRaises(vault.VaultError):
            vault.unlock(self.uid, PASSWORD)
        unlock_as(self.uid, "a brand new password")
        self.assertEqual(store.all_entries()[0]["text"], "still here after recovery")

    def test_wrong_recovery_key(self):
        self.lock()
        with self.assertRaises(vault.VaultError):
            vault.recover(self.uid, crypto.new_recovery_key(), "another password")

    def test_change_password_and_new_recovery_key(self):
        with self.assertRaises(vault.VaultError):
            vault.change_password(self.uid, "not it", "whatever123")
        vault.change_password(self.uid, PASSWORD, "second password")
        new_key = vault.new_recovery_key(self.uid, "second password")
        self.lock()
        with self.assertRaises(vault.VaultError):
            vault.recover(self.uid, self.recovery, "third password")   # old recovery key no longer works
        vault.recover(self.uid, new_key, "third password")

    def test_entries_cannot_be_swapped_between_rows(self):
        a = store.create_entry("entry A")
        b = store.create_entry("entry B")
        con = db.connect()
        with con:
            blob_a = con.execute("SELECT data FROM entries WHERE id = ?", (a["id"],)).fetchone()[0]
            con.execute("UPDATE entries SET data = ? WHERE id = ?", (blob_a, b["id"]))
        con.close()
        self.lock()
        unlock_as(self.uid)
        texts = [e["text"] for e in store.all_entries()]
        self.assertEqual(texts, ["entry A"])  # the tampered row is refused, not shown as B

    def test_permanent_delete_leaves_backups(self):
        e = store.create_entry("to be deleted")   # also takes today's backup
        b = next(config.BACKUP_DIR.glob("journal-*.db"))
        store.delete_entry_permanently(e["id"])
        con = sqlite3.connect(b)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)
        con.close()

    def test_auto_lock_after_inactivity(self):
        users.update_prefs(self.uid, {"lock_minutes": 30})
        vault.touch(self.token)
        self.assertFalse(vault.lock_if_idle())                         # just active: stays open
        vault.session(self.token).last_activity -= 31 * 60             # pretend 31 minutes passed
        self.assertTrue(vault.lock_if_idle())
        self.assertFalse(vault.is_unlocked(self.uid))
        token = unlock_as(self.uid)
        users.update_prefs(self.uid, {"lock_minutes": 0})              # "only when I lock it"
        vault.session(token).last_activity -= 10 * 3600
        self.assertFalse(vault.lock_if_idle())

    def test_csv_export(self):
        store.create_entry("Line one\nLine two, with a comma — and café", title="Résumé")
        text = export.journal_csv()
        self.assertTrue(text.startswith("﻿"))
        rows = list(csv.reader(io.StringIO(text[1:])))
        self.assertEqual(rows[0][:3], ["Date", "Time", "Title"])
        self.assertEqual(rows[1][2], "Résumé")
        self.assertEqual(rows[1][4], "Line one\nLine two, with a comma — and café")

    def test_csv_import_round_trip(self):
        a = store.create_entry("First, with a comma — and café", title="Résumé")
        store.update_entry(a["id"], favourite=True, insight="An insight.", question="A question?",
                           reflection_status=store.DONE,
                           references=[sources.reference("apology", "", "An unexamined life.")])
        store.create_entry("Second entry")
        data = export.journal_csv().encode("utf-8")
        store.wipe_all_entries()
        self.assertEqual(store.all_entries(), [])
        self.assertEqual(importer.import_csv(data), (2, 0))
        self.assertEqual(importer.import_csv(data), (0, 2))          # duplicates skipped
        back = {e["text"]: e for e in store.all_entries()}
        first = back["First, with a comma — and café"]
        self.assertEqual(first["title"], "Résumé")
        self.assertTrue(first["favourite"])
        self.assertEqual(first["insight"], "An insight.")
        self.assertEqual(first["reflection_status"], store.DONE)
        self.assertEqual([r["work"] for r in first["references"]], ["apology"])

    def test_csv_import_refuses_other_files(self):
        with self.assertRaises(importer.ImportProblem):
            importer.import_csv(b"name,age\nx,3\n")
        with self.assertRaises(importer.ImportProblem):
            importer.import_csv(b"\xff\xfe\x00garbage")


class AccountTests(unittest.TestCase):
    """Several people on the hosted version: each sees only their own journal."""

    def setUp(self):
        fresh_journal()
        self.ann = users.from_google("sub-ann", "ann@example.com", "Ann Smith")
        self.bob = users.from_google("sub-bob", "bob@example.com", "Bob Jones")
        _, self.ann_token = set_up_as(self.ann, "ann's passphrase")
        store.create_entry("Ann's private thoughts")
        store.save_draft("Ann's draft", None, None)
        _, self.bob_token = set_up_as(self.bob, "bob's passphrase")
        store.create_entry("Bob's private thoughts")

    def tearDown(self):
        usage.LIMITS.update(reflection=config.REFLECTIONS_PER_DAY, prompt=config.PROMPTS_PER_DAY)

    def test_same_google_account_same_user(self):
        self.assertEqual(users.from_google("sub-ann", "ann@new.example.com", "Ann S"), self.ann)
        self.assertEqual(users.get(self.ann)["email"], "ann@new.example.com")
        self.assertEqual(users.prefs(self.ann)["user_name"], "Ann")

    def test_each_person_sees_only_their_own(self):
        vault.use(vault.session(self.ann_token))
        self.assertEqual([e["text"] for e in store.all_entries()], ["Ann's private thoughts"])
        self.assertEqual(store.load_draft()["text"], "Ann's draft")
        ann_entry = store.all_entries()[0]["id"]
        vault.use(vault.session(self.bob_token))
        self.assertEqual([e["text"] for e in store.all_entries()], ["Bob's private thoughts"])
        self.assertFalse(store.load_draft())
        with self.assertRaises(KeyError):
            store.get_entry(ann_entry)
        with self.assertRaises(KeyError):
            store.delete_entry_permanently(ann_entry)

    def test_one_persons_passphrase_doesnt_open_another(self):
        vault.lock_all()
        with self.assertRaises(vault.VaultError):
            vault.unlock(self.bob, "ann's passphrase")

    def test_locking_one_leaves_the_other_open(self):
        vault.lock(self.ann_token)
        self.assertFalse(vault.is_unlocked(self.ann))
        self.assertTrue(vault.is_unlocked(self.bob))
        vault.use(vault.session(self.bob_token))
        self.assertEqual(len(store.all_entries()), 1)

    def test_wipe_only_touches_one_person(self):
        vault.use(vault.session(self.ann_token))
        challenge = wipe.new_challenge(self.ann)
        words = " ".join(wipe.DIGIT_WORDS[int(d)] for d in challenge["number"])
        ok, *_ = wipe.wipe(challenge["id"], words, self.bob)    # someone else's challenge: refused (and used up)
        self.assertFalse(ok)
        self.assertEqual(len(store.all_entries()), 1)
        challenge = wipe.new_challenge(self.ann)
        words = " ".join(wipe.DIGIT_WORDS[int(d)] for d in challenge["number"])
        ok, *_ = wipe.wipe(challenge["id"], words, self.ann)
        self.assertTrue(ok)
        self.assertEqual(store.all_entries(), [])
        vault.use(vault.session(self.bob_token))
        self.assertEqual(len(store.all_entries()), 1)

    def test_daily_limits(self):
        usage.LIMITS["reflection"] = 2
        self.assertEqual(usage.remaining(self.ann, "reflection"), 2)
        usage.record(self.ann, "reflection")
        usage.record(self.ann, "reflection")
        with self.assertRaises(usage.LimitReached):
            usage.check(self.ann, "reflection")
        usage.check(self.bob, "reflection")                     # Bob's allowance is separate
        usage.LIMITS["reflection"] = 0
        self.assertIsNone(usage.remaining(self.ann, "reflection"))   # no limit (your own computer)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        fresh_journal()

    def test_send_list_done_export(self):
        feedback.add("u1", "ann@example.com", "Ann", "fix", "  The title box is cut off on my phone.  ", "write", "Phone")
        feedback.add("u2", None, "Bob", "feature", "Line one\nA comma, and “quotes”")
        items = feedback.all_feedback()
        self.assertEqual([f["kind"] for f in items], ["feature", "fix"])          # newest first
        self.assertEqual(items[1]["text"], "The title box is cut off on my phone.")
        feedback.set_done(items[1]["id"], True)
        self.assertTrue(feedback.all_feedback()[1]["done"])
        rows = list(csv.reader(io.StringIO(feedback.as_csv()[1:])))
        self.assertEqual(rows[0][:5], ["Date", "Time", "From", "Type", "Feedback"])
        self.assertEqual(rows[1][4], "Line one\nA comma, and “quotes”")
        self.assertEqual(rows[2][2:4], ["ann@example.com", "Fix or improvement"])
        with self.assertRaises(KeyError):
            feedback.set_done(9999, True)

    def test_refuses_bad_input(self):
        for kind, text in [("fix", "   "), ("praise", "hello"), ("feature", "x" * (feedback.MAX_CHARS + 1))]:
            with self.assertRaises(feedback.FeedbackProblem):
                feedback.add("u1", None, None, kind, text)

    def test_daily_cap(self):
        for i in range(feedback.PER_DAY):
            feedback.add("u1", None, None, "fix", f"note {i}")
        with self.assertRaises(feedback.FeedbackProblem):
            feedback.add("u1", None, None, "fix", "one too many")
        feedback.add("u2", None, None, "fix", "someone else is fine")


class ActivityTests(unittest.TestCase):
    def setUp(self):
        fresh_journal()

    def test_summary(self):
        from datetime import timedelta
        today = activity._today()
        ann = users.from_google("sub-ann", "ann@example.com", "Ann")
        bob = users.from_google("sub-bob", "bob@example.com", "Bob")
        users.from_google("sub-cat", "cat@example.com", "Cat")            # signed in once, never used it
        con = db.connect()
        with con:
            for d, n in ((0, 2), (3, 1), (10, 0), (45, 5)):                # Ann: 3 days in the last 30, one older
                con.execute("INSERT INTO activity VALUES (?, ?, ?)", (ann, (today - timedelta(days=d)).isoformat(), n))
            con.execute("INSERT INTO activity VALUES (?, ?, ?)", (bob, (today - timedelta(days=2)).isoformat(), 1))
        con.close()
        activity.seen(ann)                                                 # already recorded today: no change
        usage.record(ann, "reflection")
        s = activity.summary()
        self.assertEqual((s["people"], s["active_today"], s["active_week"], s["active_month"]), (3, 1, 2, 2))
        self.assertEqual(s["returning"], 1)                                # only Ann came back on another day
        self.assertEqual(s["avg_active_days"], 2.0)                        # (3 + 1) / 2
        self.assertEqual(s["entries_recent"], 4)                           # 2 + 1 + 0 (Ann) + 1 (Bob)
        self.assertEqual(s["reflections_recent"], 1)
        rows = {r["who"]: r for r in s["rows"]}
        self.assertEqual((rows["ann@example.com"]["days_since"], rows["ann@example.com"]["first_seen"]),
                         (0, (today - timedelta(days=45)).isoformat()))
        self.assertEqual(rows["bob@example.com"]["days_since"], 2)
        self.assertIsNone(rows["cat@example.com"]["last_seen"])
        self.assertEqual(len(s["series"]), 30)
        self.assertEqual(s["series"][-1], {"day": today.isoformat(), "users": 1})

    def test_entry_written_counts(self):
        uid = users.ensure_local()
        activity.entry_written(uid)
        activity.entry_written(uid)
        self.assertEqual(activity.summary()["entries_recent"], 2)


class UpgradeTests(unittest.TestCase):
    """A single-person encrypted journal (before accounts) becomes the 'local' user."""

    def test_v1_database_upgrades_without_decrypting(self):
        fresh_journal()
        key = crypto.new_data_key()
        con = sqlite3.connect(config.DB_PATH)
        with con:
            con.executescript("""
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE entries (id TEXT PRIMARY KEY, data BLOB NOT NULL);
                CREATE TABLE draft (id INTEGER PRIMARY KEY CHECK (id = 1), data BLOB NOT NULL);
            """)
            con.execute("INSERT INTO meta VALUES ('key_password', ?)", (json.dumps(crypto.wrap_data_key(key, PASSWORD)),))
            entry = {"id": "aaaa000011112222", "created_at": "2026-09-10T12:00:00-04:00",
                     "updated_at": "2026-09-10T12:00:00-04:00", "text": "From before accounts", "word_count": 3,
                     "reflection_status": "Not requested"}
            con.execute("INSERT INTO entries VALUES (?, ?)",
                        (entry["id"], crypto.encrypt(key, json.dumps(entry).encode(), entry["id"].encode())))
        con.close()

        self.assertTrue(vault.is_set_up(LOCAL_USER))   # first connection runs the upgrade
        self.assertTrue((config.BACKUP_DIR / "journal-before-accounts.db").exists())
        unlock_as(LOCAL_USER)
        self.assertEqual([e["text"] for e in store.all_entries()], ["From before accounts"])
        self.assertIsNone(db.get_meta("key_password"))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        fresh_journal()
        config.LEGACY_ENTRIES_DIR.mkdir(exist_ok=True)
        self.old = []
        for i in range(3):
            e = {"id": f"2026091{i}-120000-abc{i}", "created_at": f"2026-09-1{i}T12:00:00-04:00",
                 "updated_at": f"2026-09-1{i}T12:00:00-04:00", "title": f"Old {i}", "text": f"Old entry {i} ünïcode",
                 "word_count": 3, "quotes": [], "favourite": i == 1, "hidden": False,
                 "reflection_status": "Done", "insight": "x", "question": "y"}
            (config.LEGACY_ENTRIES_DIR / f"{e['id']}.json").write_text(json.dumps(e), encoding="utf-8")
            self.old.append(e)
        config.LEGACY_DRAFT_PATH.write_text(json.dumps({"text": "old draft", "prompt": None}), encoding="utf-8")
        config.LEGACY_EXCEL_PATH.write_bytes(b"pretend workbook")
        (config.BACKUP_DIR / "journal-2026-09-01.xlsx").write_bytes(b"pretend backup")
        (config.BACKUP_DIR / "entries-before-something").mkdir()

    def test_import_verify_and_delete(self):
        set_up_as(users.ensure_local())
        self.assertEqual(migrate.import_legacy(), 3)
        failed = migrate.delete_legacy()
        self.assertEqual(failed, [])
        entries = store.all_entries()
        self.assertEqual([e["text"] for e in entries], [o["text"] for o in self.old])
        self.assertTrue(all(len(e["id"]) == 16 for e in entries))          # random IDs now
        self.assertEqual(store.load_draft()["text"], "old draft")
        for p in [config.LEGACY_ENTRIES_DIR, config.LEGACY_DRAFT_PATH, config.LEGACY_EXCEL_PATH,
                  config.BACKUP_DIR / "journal-2026-09-01.xlsx", config.BACKUP_DIR / "entries-before-something"]:
            self.assertFalse(p.exists(), p)
        self.assertNotIn(b"Old entry", raw_bytes())


if __name__ == "__main__":
    unittest.main()
