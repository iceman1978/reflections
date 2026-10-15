"""Tests for the encrypted journal and accounts. Run (the tests use a throwaway data folder):

    set JOURNAL_DATA_DIR to an empty temporary folder, then
    uv run python -m unittest discover tests
"""
import base64
import csv
import io
import json
import os
import shutil
import sqlite3
import sys
import time
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if not os.environ.get("JOURNAL_DATA_DIR"):
    raise unittest.SkipTest("Set JOURNAL_DATA_DIR to a throwaway folder to run the vault tests.")

from journal import (activity, config, crypto, db, export, feedback, importer, migrate, sources, store, usage, users,  # noqa: E402
                     vault, wipe)
from journal.db import LOCAL_USER  # noqa: E402
from journal import passkeys  # noqa: E402


def b64url(raw):
    """How a browser sends passkey bytes: base64url without padding."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

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


class PasskeyTests(unittest.TestCase):
    """A passkey's PRF secret is one more wrapping of the journal's key."""
    SECRET = os.urandom(32)

    def setUp(self):
        fresh_journal()
        self.uid = users.ensure_local()
        self.recovery, self.token = set_up_as(self.uid)
        self.cred = b64url(os.urandom(20))
        vault.add_passkey(self.uid, PASSWORD, self.cred, b64url(os.urandom(32)),
                          b64url(self.SECRET), "Phone")

    def test_passkey_unlocks_the_same_journal(self):
        store.create_entry("Written before the passkey unlock.")
        vault.lock_all()
        token = vault.unlock_with_passkey(self.uid, self.cred, b64url(self.SECRET))
        vault.use(vault.session(token))
        self.assertEqual(store.all_entries()[0]["text"], "Written before the passkey unlock.")
        self.assertIsNotNone(passkeys.list_for(self.uid)[0]["last_used_at"])

    def test_wrong_secret_or_unknown_passkey_fails(self):
        vault.lock_all()
        vault._failures.clear()
        with self.assertRaises(vault.VaultError):
            vault.unlock_with_passkey(self.uid, self.cred, b64url(os.urandom(32)))
        with self.assertRaises(vault.VaultError):
            vault.unlock_with_passkey(self.uid, b64url(os.urandom(20)), b64url(self.SECRET))
        vault._failures.clear()

    def test_adding_needs_the_passphrase(self):
        with self.assertRaises(vault.VaultError):
            vault.add_passkey(self.uid, "not the passphrase", b64url(os.urandom(20)),
                              b64url(os.urandom(32)), b64url(os.urandom(32)), "Laptop")
        self.assertEqual(len(passkeys.list_for(self.uid)), 1)

    def test_secret_is_never_stored(self):
        self.assertNotIn(self.SECRET, raw_bytes())
        self.assertNotIn(b64url(self.SECRET).encode(), raw_bytes())

    def test_removed_passkey_no_longer_unlocks(self):
        passkeys.remove(self.uid, self.cred)
        vault.lock_all()
        with self.assertRaises(vault.VaultError):
            vault.unlock_with_passkey(self.uid, self.cred, b64url(self.SECRET))
        vault._failures.clear()


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


class VarietyTests(unittest.TestCase):
    """Reflections are nudged away from the thinkers used most recently."""

    def setUp(self):
        fresh_journal()
        set_up_as(users.ensure_local())

    def test_recent_thinkers_note(self):
        from journal import reflection
        def reflected(text, **fields):
            e = store.create_entry(text)
            return store.update_entry(e["id"], reflection_status=store.DONE, insight="An insight.", **fields)
        # An older reflection, from before thinkers were recorded: read from its quotes and links.
        reflected("one", quotes=[{"author": "Plato"}], references=[{"author": "Aristotle"}])
        reflected("two", thinkers=["Plato"])
        reflected("three", thinkers=["Plato", "Aristotle"])
        store.create_entry("not reflected yet")                                  # ignored
        current = store.create_entry("the entry being reflected on")
        counts, seen = reflection.recent_thinkers(current)
        self.assertEqual(seen, 3)
        self.assertEqual(counts, {"Plato": 3, "Aristotle": 2})
        note = reflection._variety_note(current)
        self.assertIn("Plato (3), Aristotle (2)", note)
        self.assertIn("Not used recently: Socrates, Epicurus, Cicero, Seneca", note)
        self.assertIn(note, reflection._user_message(current))

    def test_no_note_for_a_first_reflection(self):
        from journal import reflection
        self.assertIsNone(reflection._variety_note(store.create_entry("first ever entry")))


class LongestEntryTests(unittest.TestCase):
    def setUp(self):
        fresh_journal()
        set_up_as(users.ensure_local())

    def test_longest_entry_record(self):
        from journal import stats
        self.assertEqual(stats.journal_stats()["longest_words"], 0)
        store.create_entry("one two three")
        long_one = store.create_entry("a b c d e f g h")
        hidden = store.create_entry(" ".join(["word"] * 50))
        store.set_hidden(hidden["id"], True)                     # hidden entries don't hold the record
        s = stats.journal_stats()
        self.assertEqual((s["longest_words"], s["longest_id"], s["shown_entries"]), (8, long_one["id"], 2))
        store.create_entry("1 2 3 4 5 6 7 8")                   # a tie doesn't take the record
        self.assertEqual(stats.journal_stats()["longest_id"], long_one["id"])
        longer = store.create_entry("1 2 3 4 5 6 7 8 9")         # beating it does
        self.assertEqual(stats.journal_stats()["longest_id"], longer["id"])


class InviteTests(unittest.TestCase):
    def setUp(self):
        fresh_journal()
        self._allowed = set(config.ALLOWED_EMAILS)
        config.ALLOWED_EMAILS.clear()
        config.ALLOWED_EMAILS.update({"ann@example.com", "friend-in@example.com"})

    def tearDown(self):
        config.ALLOWED_EMAILS.clear()
        config.ALLOWED_EMAILS.update(self._allowed)

    def test_request_and_status(self):
        from journal import invites
        inv = invites.add("u-ann", "ann@example.com", "Ann", " New.Friend@Gmail.com ", "Sam", "my cousin")
        self.assertEqual((inv["email"], inv["status"], inv["name"]), ("new.friend@gmail.com", "requested", "Sam"))
        self.assertEqual([i["email"] for i in invites.mine("u-ann")], ["new.friend@gmail.com"])
        self.assertEqual(invites.mine("u-bob"), [])                           # only your own requests
        config.ALLOWED_EMAILS.add("new.friend@gmail.com")                    # added on the server
        self.assertEqual(invites.mine("u-ann")[0]["status"], "added")
        invites.set_status(inv["id"], "declined")
        config.ALLOWED_EMAILS.discard("new.friend@gmail.com")
        self.assertEqual(invites.all_requests()[0]["status"], "declined")
        with self.assertRaises(ValueError):
            invites.set_status(inv["id"], "added")                           # worked out, never stored
        with self.assertRaises(KeyError):
            invites.set_status(9999, "declined")

    def test_refusals(self):
        from journal import invites
        for email in ("not an email", "ann@example.com", "friend-in@example.com"):   # bad, own, already in
            with self.assertRaises(invites.InviteProblem):
                invites.add("u-ann", "ann@example.com", "Ann", email)
        invites.add("u-ann", "ann@example.com", "Ann", "pal@gmail.com")
        with self.assertRaises(invites.InviteProblem):
            invites.add("u-bob", "bob@example.com", "Bob", "PAL@gmail.com")           # already requested
        for i in range(invites.PER_DAY - 1):
            invites.add("u-ann", "ann@example.com", "Ann", f"p{i}@gmail.com")
        with self.assertRaises(invites.InviteProblem):
            invites.add("u-ann", "ann@example.com", "Ann", "one-too-many@gmail.com")  # daily cap

    def test_invite_lets_them_in_and_emails_them(self):
        from unittest import mock
        from journal import invites
        req = invites.add("u-ann", "ann@example.com", "Ann", "sam@gmail.com", "Sam", "You'll love this")
        self.assertFalse(invites.allowed("sam@gmail.com"))
        with mock.patch("journal.mail.send") as send:
            inv, problem = invites.admit(req["id"], "https://inhissteps.app/")
        self.assertIsNone(problem)
        self.assertEqual(inv["status"], "added")
        self.assertTrue(inv["emailed_at"])
        self.assertTrue(invites.allowed("SAM@gmail.com"))                   # straight away, no redeploy
        to, subject, text, html = send.call_args.args
        self.assertEqual(to, "sam@gmail.com")
        self.assertIn("Ann invited you", subject)
        self.assertIn("https://inhissteps.app/", text)
        self.assertIn("You&#39;ll love this", html)                          # the personal note goes in
        self.assertIn("— Ann", html)

    def test_invite_directly_with_a_message(self):
        from unittest import mock
        from journal import invites
        with mock.patch("journal.mail.send") as send:
            inv, problem = invites.invite("u-ann", "ann@example.com", "Ann", " Pat@Gmail.com ", "Pat",
                                          "Thought of you <3", "https://inhissteps.app/")
        self.assertIsNone(problem)
        self.assertTrue(invites.allowed("pat@gmail.com"))
        html = send.call_args.args[3]
        self.assertIn("Thought of you &lt;3", html)                          # escaped
        with self.assertRaises(invites.InviteProblem):
            invites.invite("u-ann", "ann@example.com", "Ann", "not an email")

    def test_without_an_email_service_they_are_still_let_in(self):
        from journal import invites
        with mock_env(RESEND_API_KEY=None):
            inv, problem = invites.invite("u-ann", "ann@example.com", "Ann", "lee@gmail.com")
        self.assertIn("RESEND_API_KEY", problem)
        self.assertEqual(inv["status"], "added")
        self.assertIsNone(inv["emailed_at"])

    def test_anyone_may_sign_in_only_while_there_is_no_list(self):
        from journal import invites
        config.ALLOWED_EMAILS.clear()
        self.assertTrue(invites.allowed("stranger@gmail.com"))
        invites.invite("u-ann", "ann@example.com", "Ann", "first@gmail.com")
        self.assertFalse(invites.allowed("stranger@gmail.com"))
        self.assertTrue(invites.allowed("first@gmail.com"))


    def test_the_server_list_moves_into_the_app(self):
        from journal import invites
        self.assertEqual(invites.import_allowed(), 2)
        self.assertEqual(invites.import_allowed(), 0)                           # once each
        config.ALLOWED_EMAILS.clear()                                          # the server's list emptied
        self.assertTrue(invites.allowed("ann@example.com"))
        self.assertFalse(invites.allowed("stranger@gmail.com"))
        self.assertTrue(invites.remove("friend-in@example.com"))
        self.assertFalse(invites.allowed("friend-in@example.com"))
        invites.grant("friend-in@example.com")                                 # let back in
        self.assertTrue(invites.allowed("friend-in@example.com"))

    def test_removing_someone_still_on_the_server_list_says_so(self):
        from journal import invites
        invites.import_allowed()
        self.assertFalse(invites.remove("ann@example.com"))


    def test_sharing_with_someone_who_has_joined(self):
        from unittest import mock
        from journal import invites, users
        invites.import_allowed()
        users.from_google("g-ann", "ann@example.com", "Ann Smith")
        with mock.patch("journal.mail.send") as send:
            self.assertIsNone(invites.share("ann@example.com", "Aaron", "Look at the new page"))
        to, subject, text, html = send.call_args.args
        self.assertIn("shared what's new", subject)
        self.assertIn("Hi Ann,", text)
        self.assertNotIn("passphrase", text)                                # no sign-up steps
        self.assertIn("Look at the new page", html)
        self.assertIn("ann@example.com", invites.shared_dates())
        with mock.patch("journal.mail.send") as send:                       # not joined yet: the invitation
            invites.share("friend-in@example.com", "Aaron")
        self.assertIn("invited you", send.call_args.args[1])
        with self.assertRaises(invites.InviteProblem):
            invites.share("stranger@gmail.com", "Aaron")


def mock_env(**values):
    """Set (or, with None, remove) environment variables for a `with` block."""
    from unittest import mock
    clean = {k: v for k, v in values.items() if v is not None}
    patcher = mock.patch.dict(os.environ, clean)
    class _Env:
        def __enter__(self):
            patcher.start()
            for k, v in values.items():
                if v is None:
                    os.environ.pop(k, None)
        def __exit__(self, *exc):
            patcher.stop()
    return _Env()


class ReminderTests(unittest.TestCase):
    """Reminder emails: 2, 6 and 14 days after the last entry; once for an unfinished setup."""

    TODAY = date(2026, 11, 20)

    def setUp(self):
        fresh_journal()
        self._allowed = set(config.ALLOWED_EMAILS)
        config.ALLOWED_EMAILS.clear()

    def tearDown(self):
        config.ALLOWED_EMAILS.clear()
        config.ALLOWED_EMAILS.update(self._allowed)

    def person(self, email, last_entry=None, set_up=True, joined="2026-11-01T12:00:00+00:00", entries=1):
        from journal import users
        uid = users.from_google("g-" + email, email, "Sam Jones")
        con = db.connect()
        with con:
            con.execute("UPDATE users SET created_at = ?, key_password = ? WHERE id = ?",
                        (joined, "{}" if set_up else None, uid))
            if last_entry:
                con.execute("INSERT INTO activity (user_id, day, entries) VALUES (?, ?, 1)", (uid, last_entry))
            for i in range(entries if set_up else 0):
                con.execute("INSERT INTO entries (id, user_id, data) VALUES (?, ?, x'00')", (f"{uid}-{i}", uid))
        con.close()
        return uid

    def kinds(self, day=None):
        from journal import reminders
        return {p["email"]: (kind, days) for p, kind, days in reminders.due(day or self.TODAY)}

    def test_the_schedule(self):
        self.person("one@x.com", "2026-11-19")       # 1 day: nothing yet
        self.person("two@x.com", "2026-11-18")       # 2 days
        self.person("six@x.com", "2026-11-14")       # 6 days
        self.person("old@x.com", "2026-10-30")       # 21 days: the last one
        self.assertEqual(self.kinds(), {"two@x.com": (1, 2), "six@x.com": (2, 6), "old@x.com": (3, 21)})

    def test_once_per_stage_then_quiet_until_they_write(self):
        from unittest import mock
        from journal import reminders
        uid = self.person("sam@x.com", "2026-11-18")
        with mock.patch("journal.mail.send") as send:
            self.assertEqual(reminders.send_due(self.TODAY, "secret", "https://inhissteps.app/"), 1)
            self.assertEqual(reminders.send_due(self.TODAY, "secret", "https://inhissteps.app/"), 0)   # claimed
            self.assertEqual(self.kinds(date(2026, 11, 21)), {})                  # 3 days: still stage 1
            self.assertEqual(self.kinds(date(2026, 11, 24)), {"sam@x.com": (2, 6)})
        subject, text, html = send.call_args.args[1:4]
        self.assertIn("Hi Sam,", text)
        self.assertIn("2 days since your last entry", text)
        self.assertIn("turn these reminders off", text)                         # the first one says so
        self.assertIn("reminders/off?t=", html)
        self.assertEqual(send.call_args.kwargs["local"], "reminder")
        self.assertIn("List-Unsubscribe", send.call_args.kwargs["headers"])
        con = db.connect()                                                        # they write again
        with con:
            con.execute("INSERT INTO activity (user_id, day, entries) VALUES (?, '2026-11-22', 1)", (uid,))
        con.close()
        self.assertEqual(self.kinds(date(2026, 11, 23)), {})
        self.assertEqual(self.kinds(date(2026, 11, 24)), {"sam@x.com": (1, 2)})  # a fresh count

    def test_a_failed_send_is_tried_again(self):
        from unittest import mock
        from journal import mail, reminders
        self.person("sam@x.com", "2026-11-18")
        with mock.patch("journal.mail.send", side_effect=mail.MailProblem("down")), \
                mock.patch("journal.mail.ready", return_value=True):
            self.assertEqual(reminders.send_due(self.TODAY, "secret", "https://x/"), 0)
        self.assertEqual(self.kinds(), {"sam@x.com": (1, 2)})

    def test_unfinished_setup_gets_one_email(self):
        from unittest import mock
        from journal import reminders
        self.person("new@x.com", set_up=False, joined="2026-11-19T12:00:00+00:00")
        self.assertEqual(self.kinds(), {"new@x.com": ("setup", 1)})
        with mock.patch("journal.mail.send") as send:
            reminders.send_due(self.TODAY, "secret", "https://x/")
        self.assertIn("ready when you are", send.call_args.args[1])
        self.assertEqual(self.kinds(date(2026, 11, 30)), {})

    def test_no_reminders_when_switched_off_or_without_access(self):
        from journal import invites, reminders, users
        off = self.person("off@x.com", "2026-11-10")
        users.update_prefs(off, {"email_reminders": False})
        self.person("gone@x.com", "2026-11-10")
        invites.grant("someone-else@x.com")                                       # there's a list now
        invites.grant("off@x.com")
        self.assertEqual(self.kinds(), {})
        token = reminders.off_token(off, "secret")
        self.assertEqual(reminders.user_from_token(token, "secret"), off)
        self.assertIsNone(reminders.user_from_token(token, "another secret"))
        self.assertIsNone(reminders.user_from_token("junk", "secret"))

    def test_first_entry_wording(self):
        from journal import reminders
        self.person("fresh@x.com", entries=0, joined="2026-11-17T12:00:00+00:00")
        person, kind, days = reminders.due(self.TODAY)[0]
        subject, text, _ = reminders.email(person, kind, days, self.TODAY, "https://x/", "https://x/off")
        self.assertIn("first entry", subject)


class ShareTests(unittest.TestCase):
    """Sharing a passage by email: checked against the library, limited, and stoppable."""

    def setUp(self):
        fresh_journal()
        from journal import invites, sources
        invites.grant("ann@example.com")                                          # a sign-in list, without Cole
        self.pick = ("Trust & surrender", None, None)
        self.work = next(iter(sources.works()))
        self.found = {"work": self.work, "author": "", "title": "Psalms", "translation": "Berean Standard Bible",
                      "location": "23:1", "quote": "The LORD is my shepherd; I shall not want.",
                      "url": "https://biblehub.com/bsb/psalms/23.htm"}

    def send(self, to="cole@gmail.com", note="Thought of you", found=True):
        from unittest import mock
        from journal import share
        with mock.patch("journal.sources.cite", return_value=self.found if found else None), \
                mock.patch("journal.passages._theme_of", return_value=self.pick) as self.picked, \
                mock.patch("journal.mail.send") as send:
            share.send("u-ann", "ann@example.com", "Ann", to, "Cole", note, self.work, "23:1",
                       "The LORD is my shepherd", "https://inhissteps.app/", "secret")
        return send

    def test_the_email(self):
        send = self.send()
        to, subject, text, html = send.call_args.args
        self.assertEqual(to, "cole@gmail.com")
        self.assertIn("Ann shared a passage with you", subject)
        self.assertIn("The LORD is my shepherd; I shall not want.", text)          # the source's wording
        self.assertIn("Psalms 23:1 (BSB)", text)
        self.assertIn("ask Ann for an invitation", text)                          # not invited yet
        self.assertIn("https://inhissteps.app/p/", html)                          # the passage's own page
        self.assertEqual(send.call_args.kwargs["reply_to"], "ann@example.com")
        self.assertEqual(send.call_args.kwargs["local"], "share")

    def test_only_passages_from_the_library(self):
        from journal import share
        with self.assertRaises(share.ShareProblem):
            self.send(found=False)

    def test_limits_and_refusals(self):
        from journal import share
        for bad in ("not an email", "ann@example.com"):
            with self.assertRaises(share.ShareProblem):
                self.send(to=bad)
        self.send(), self.send()
        with self.assertRaises(share.ShareProblem):
            self.send()                                                           # a couple per address a day
        for i in range(share.PER_DAY - 2):
            self.send(to=f"p{i}@x.com")
        with self.assertRaises(share.ShareProblem):
            self.send(to="one-more@x.com")                                        # plenty for one day

    def test_passages_are_saved_with_a_theme_and_counted(self):
        from journal import passages
        self.send()
        html = self.send(to="pat@x.com").call_args.args[3]
        self.assertIn("static/email-vignettes/trust-and-surrender.png", html)   # the passage's own drawing
        top = passages.most_shared()
        self.assertEqual([(p["quote"], p["theme"], p["shares"]) for p in top],
                         [("The LORD is my shepherd; I shall not want.", "Trust & surrender", 2)])
        self.picked.assert_not_called()                                         # chosen once, then saved
        self.assertNotIn("cole", json.dumps(top).lower())                      # never who it went to

    def test_passages_that_need_a_theme_are_listed(self):
        from journal import passages
        self.pick = (None, "Shepherding", None)                                   # nothing fitted
        html = self.send().call_args.args[3]
        self.assertIn("email-vignette.png", html)                                # the edition's drawing instead
        gaps = passages.needing_themes()
        self.assertEqual([(g["theme"], g["missing_theme"]) for g in gaps], [(None, "Shepherding")])
        self.pick = ("Trust & surrender", None, None)                            # tried again next time
        self.send(to="pat@x.com")
        self.assertEqual(passages.needing_themes(), [])

    def test_a_passage_has_its_own_short_link_and_counts(self):
        from journal import kept, passages
        html = self.send().call_args.args[3]
        key = kept.key_of(self.found)
        self.assertIn(f"https://inhissteps.app/p/{key[:10]}", html)                 # through the app, not the source
        self.assertNotIn("biblehub.com", html)
        self.assertEqual(passages.by_short(key[:10])["quote"], self.found["quote"])
        self.assertIsNone(passages.by_short("../etc"))
        passages.shared(key, by_email=False)
        passages.kept_count(self.found, +1)
        passages.kept_count(self.found, +1)
        passages.released(key)
        p = passages.get(key)
        self.assertEqual((p["shares"], p["other_shares"], p["keeps"]), (1, 1, 1))
        passages.released(key), passages.released(key)
        self.assertEqual(passages.get(key)["keeps"], 0)                           # never below nothing

    def test_a_shared_passage_waits_in_their_kept_passages(self):
        from journal import kept
        uid = users.from_google("g-cole", "cole@gmail.com", "Cole")
        set_up_as(uid)
        mine = {**self.found, "quote": "He makes me lie down in green pastures.", "location": "23:2"}
        kept.keep(mine, "2026-10-01T09:00:00-04:00")                              # one he kept himself
        vault.use(None)
        text = self.send(note="For your mornings").call_args.args[2]
        self.assertIn("also waiting in your Kept passages", text)                 # he's a member
        self.found = mine
        self.send(to="cole@gmail.com", note=None)                                 # one he already has
        vault.lock_all()
        unlock_as(uid)
        self.assertEqual(kept.take_shared("cole@gmail.com"), 2)
        got = {p["location"]: p for p in kept.passages()}
        self.assertEqual(got["23:1"]["own"], False)
        self.assertEqual([(s["by"], s["note"]) for s in got["23:1"]["shared"]], [("Ann", "For your mornings")])
        self.assertNotIn("own", got["23:2"])                                      # still his own keep
        self.assertEqual(got["23:2"]["shared"][0]["by"], "Ann")
        self.assertEqual(kept.take_shared("cole@gmail.com"), 0)                   # moved, not copied
        kept.release(got["23:1"]["key"])                                          # he can let it go
        self.assertEqual(len(kept.passages()), 1)

    def test_asking_not_to_receive_them(self):
        from journal import share
        token = share.stop_token("cole@gmail.com", "secret")
        self.assertEqual(share.email_from_token(token, "secret"), "cole@gmail.com")
        self.assertIsNone(share.email_from_token(token, "other"))
        share.block("Cole@gmail.com")
        with self.assertRaises(share.ShareProblem):
            self.send()


class DigestTests(unittest.TestCase):
    """The admin's "new work" email: at 9:00 and 17:00, only when something's new."""

    def setUp(self):
        fresh_journal()
        self._admins = set(config.ADMIN_EMAILS)
        config.ADMIN_EMAILS.clear()
        config.ADMIN_EMAILS.add("boss@x.com")

    def tearDown(self):
        config.ADMIN_EMAILS.clear()
        config.ADMIN_EMAILS.update(self._admins)

    def at(self, hour, day=15):
        from journal.config import TZ
        return datetime(2026, 10, day, hour, 5, tzinfo=TZ)

    @staticmethod
    def at_noon():
        """Date everything made so far at noon on the 15th (between the 9:00 and 17:00 emails)."""
        con = db.connect()
        with con:
            for table in ("invites", "feedback", "users"):
                con.execute(f"UPDATE {table} SET created_at = '2026-10-15T12:00:00-04:00'")
            con.execute("UPDATE mail_failures SET at = '2026-10-15T16:00:00+00:00'")
        con.close()

    def run_at(self, hour, day=15):
        from unittest import mock
        from journal import digest
        with mock.patch("journal.mail.send") as send:
            digest.send_due(self.at(hour, day), "https://inhissteps.app/")
        return send

    def test_twice_a_day_and_only_with_news(self):
        from journal import feedback, invites
        self.assertFalse(self.run_at(8).called)                                  # before 9: nothing
        self.assertFalse(self.run_at(9).called)                                  # nothing new
        invites.add("u-ann", "ann@example.com", "Ann", "pal@gmail.com", note="my cousin")
        feedback.add("u-ann", "ann@example.com", "Ann", "fix", "The button is odd")
        self.at_noon()
        send = self.run_at(10)
        self.assertFalse(send.called)                                            # the 9:00 email was this morning's
        send = self.run_at(17)
        to, subject, text, html = send.call_args.args
        self.assertEqual(to, "boss@x.com")
        self.assertIn("1 invitation request", subject)
        self.assertIn("from Ann", text)
        self.assertIn("1 new piece of feedback", text)
        self.assertNotIn("pal@gmail.com", text + html)                           # names only
        self.assertNotIn("cousin", text + html)
        self.assertNotIn("button is odd", text + html)
        self.assertIn("?settings=admin", html)
        self.assertEqual(send.call_args.kwargs["local"], "admin")
        self.assertFalse(self.run_at(18).called)                                 # once per slot
        self.assertFalse(self.run_at(9, day=16).called)                          # nothing new since

    def test_joined_and_failed_emails_are_counted(self):
        from journal import digest, users
        self.run_at(9)
        users.from_google("g-new", "new@x.com", "New Person")
        digest.record_failure("reminder")
        self.at_noon()
        text = self.run_at(17).call_args.args[2]
        self.assertIn("1 person joined", text)
        self.assertIn("1 email couldn't be sent", text)

    def test_an_admin_can_turn_it_off(self):
        from journal import feedback, users
        uid = users.from_google("g-boss", "boss@x.com", "Boss")
        users.update_prefs(uid, {"admin_digest": False})
        self.run_at(9)
        feedback.add("u-ann", "ann@example.com", "Ann", "fix", "Hello")
        self.at_noon()
        self.assertFalse(self.run_at(17).called)


class EmailSignInTests(unittest.TestCase):
    """Signing in with an emailed code: invited addresses only, once, briefly."""

    def setUp(self):
        fresh_journal()
        from journal import email_signin, invites
        email_signin._recent.clear()
        invites.grant("trevor@carlisletechnology.com")                           # a list, with Trevor on it

    def ask(self, email="Trevor@CarlisleTechnology.com", device="1.2.3.4"):
        from unittest import mock
        from journal import email_signin
        with mock.patch("journal.mail.send") as send:
            email_signin.request(email, device, "secret", "https://inhissteps.app/")
        return send

    def code_of(self, send):
        return send.call_args.args[1].split(" ")[0]                               # "123456 is your ... code"

    def test_invited_address_gets_a_code_that_works_once(self):
        from journal import email_signin
        send = self.ask()
        self.assertEqual(send.call_args.args[0], "trevor@carlisletechnology.com")
        self.assertEqual(send.call_args.kwargs["local"], "signin")
        code = self.code_of(send)
        self.assertEqual(email_signin.check_code("trevor@carlisletechnology.com", code, "secret"),
                         "trevor@carlisletechnology.com")
        with self.assertRaises(email_signin.SignInProblem):
            email_signin.check_code("trevor@carlisletechnology.com", code, "secret")       # used up

    def test_strangers_get_nothing_and_no_hint(self):
        send = self.ask("bob@rogers.com")
        self.assertFalse(send.called)                                             # and no error either

    def test_wrong_codes_and_limits(self):
        from journal import email_signin
        code = self.code_of(self.ask())
        for _ in range(email_signin.MAX_TRIES):
            with self.assertRaises(email_signin.SignInProblem):
                email_signin.check_code("trevor@carlisletechnology.com", "000000" if code != "000000" else "111111", "secret")
        with self.assertRaises(email_signin.SignInProblem):
            email_signin.check_code("trevor@carlisletechnology.com", code, "secret")       # locked after 5 tries
        for _ in range(email_signin.PER_ADDRESS - 1):
            self.ask(device="5.6.7.8")
        with self.assertRaises(email_signin.SignInProblem):
            self.ask(device="9.9.9.9")                                            # plenty for one address

    def test_the_link_works_once(self):
        from journal import email_signin
        html = self.ask().call_args.args[3]
        token = html.split("auth/email?t=")[1].split('"')[0]
        self.assertEqual(email_signin.check_link(token, "secret"), "trevor@carlisletechnology.com")
        self.assertIsNone(email_signin.check_link(token, "secret"))

    def test_one_account_per_address_whichever_way(self):
        uid = users.from_email("trevor@carlisletechnology.com")
        self.assertEqual(users.from_email("TREVOR@carlisletechnology.com"), uid)
        self.assertEqual(users.from_google("g-trevor", "trevor@carlisletechnology.com", "Trevor"), uid)
        self.assertEqual(users.from_google("g-trevor", "trevor@carlisletechnology.com", "Trevor"), uid)


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
