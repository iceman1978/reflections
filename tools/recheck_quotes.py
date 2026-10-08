"""Re-check the quotes in saved reflections against the current source texts.

Use after fixing or re-downloading sources (tools/build_sources.py), so quotes
saved earlier get the corrected wording, location and link. Close the journal
app first (its start.bat window), then run in the journal folder:

    uv run python tools/recheck_quotes.py           # show what would change
    uv run python tools/recheck_quotes.py --apply   # make the changes

You'll be asked for your journal passphrase. Only the quotes (and the quoted
words in the insight) change; your entry text is never touched. The journal's
daily backup keeps the previous version.
"""
import getpass
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal import sources, store, users, vault  # noqa: E402
from journal.config import settings  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def recheck(entry):
    """Returns (changed entry or None, list of notes)."""
    quotes = entry.get("quotes") or []
    if not quotes:
        return None, []
    insight = entry.get("insight") or ""
    new_quotes, notes, changed = [], [], False
    for q in quotes:
        fresh = sources.cite(q.get("work"), q.get("location"), q.get("quote"))
        if not fresh:
            notes.append(f"  ! couldn't re-find: {q.get('author')}, {q.get('title')} — left as is")
            new_quotes.append(q)
            continue
        if (fresh["quote"], fresh["location"], fresh["url"]) != (q.get("quote"), q.get("location"), q.get("url")):
            changed = True
            notes.append(f"  {q.get('author')}, {q.get('title')} {q.get('location')} -> {fresh['location']}")
            if fresh["quote"] != q.get("quote"):
                notes.append(f"    was: “{q.get('quote')}”")
                notes.append(f"    now: “{fresh['quote']}”")
                insight = insight.replace(f"“{q.get('quote')}”", f"“{fresh['quote']}”")
        new_quotes.append(fresh)
    if not changed:
        return None, notes
    return dict(entry, quotes=new_quotes, insight=insight), notes


def app_is_running():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", int(settings["port"]))) == 0


def main():
    apply = "--apply" in sys.argv
    if app_is_running():
        print("The journal app is running. Close its start.bat window first, then run this again.")
        sys.exit(1)
    uid = users.ensure_local()
    if not vault.is_set_up(uid):
        print("The journal doesn't have a passphrase yet; start the app once to set one.")
        sys.exit(1)
    try:
        token = vault.unlock(uid, getpass.getpass("Journal passphrase: "))
    except vault.VaultError as e:
        print(e)
        sys.exit(1)
    vault.use(vault.session(token))

    updates = []
    for entry in store.all_entries():
        new, notes = recheck(entry)
        if notes:
            print(f"Entry “{entry.get('title') or 'No Title'}”:")
            print("\n".join(notes))
        if new:
            updates.append(new)

    if not updates:
        print("All saved quotes already match the sources. Nothing to do.")
    elif not apply:
        print(f"\n{len(updates)} entr{'y' if len(updates) == 1 else 'ies'} would change. "
              "Run again with --apply to make the changes.")
    else:
        for entry in updates:
            store.put_entry(entry)  # 'Last Updated' stays as is: the entry itself didn't change
        print(f"\nUpdated {len(updates)} entr{'y' if len(updates) == 1 else 'ies'}.")
    vault.lock(token)


if __name__ == "__main__":
    main()
