"""Loads config/settings.json and resolves paths relative to the project folder.

Two ways to run:
  - on your own computer (start.bat): one journal, unlocked with your passphrase;
  - hosted on the web (Render): people sign in with Google first, then unlock
    their own journal with their own passphrase. Hosted mode is switched on by
    setting GOOGLE_CLIENT_ID (and the other variables listed in render.yaml).
"""
import json
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # before reading any settings below (never overrides real environment variables)
SETTINGS_PATH = ROOT / "config" / "settings.json"

# JOURNAL_DATA_DIR puts all data, logs and settings somewhere else
# (the persistent disk on Render, or a throwaway folder for tests).
TEST_DIR = os.environ.get("JOURNAL_DATA_DIR")
if TEST_DIR:
    SETTINGS_PATH = Path(TEST_DIR) / "settings.json"

APP_NAME = os.environ.get("APP_NAME", "Reflections")   # shown in the browser tab and on the welcome page
HOSTED = bool(os.environ.get("GOOGLE_CLIENT_ID"))
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "").strip().lower()   # e.g. reflections.onrender.com

# Daily limits per person (hosted only; your own computer has none).
REFLECTIONS_PER_DAY = int(os.environ.get("REFLECTIONS_PER_DAY", "3")) if HOSTED else 0
PROMPTS_PER_DAY = int(os.environ.get("PROMPTS_PER_DAY", "10")) if HOSTED else 0

# Each person's own choices (stored with their account). Everything else in
# DEFAULTS is a setting for the whole app.
PREF_KEYS = ("user_name", "theme", "colour_theme", "background_photo", "photo_blur",
             "photo_visibility", "lock_minutes", "draft_autosave_seconds")

DEFAULTS = {
    "user_name": "",
    "port": 5050,
    "open_browser_on_start": True,
    "timezone": "America/Toronto",
    "backups_to_keep": 14,
    "draft_autosave_seconds": 3,
    "lock_minutes": 30,       # lock after this long without activity; 0 = only when the app closes
    "model": "claude-sonnet-5-5",
    "reflection_effort": "high",
    "api_timeout_seconds": 60,
    "api_retries": 2,
    "theme": "auto",
    "colour_theme": "lake",
    "background_photo": True,
    "photo_blur": 10,         # pixels
    "photo_visibility": 60,   # percent; lower = more veiled
}

# Colour themes offered in Settings (their colours are defined in static/style.css).
COLOUR_THEMES = [
    ("lake", "Lake"), ("walnut", "Walnut"), ("sage", "Sage"), ("amber", "Amber"),
    ("claret", "Claret"), ("lavender", "Lavender"), ("sea-glass", "Sea glass"), ("graphite", "Graphite"),
]

# Settings that only take effect after restarting the app.
RESTART_REQUIRED = {"port", "open_browser_on_start", "timezone"}

LOCK_MINUTE_CHOICES = (5, 15, 30, 60, 120, 0)

_save_lock = threading.Lock()


def _read_file():
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return {}


settings = {**DEFAULTS, **_read_file()}
if TEST_DIR:
    settings["port"] = int(os.environ.get("JOURNAL_PORT", settings["port"]))
    settings["open_browser_on_start"] = False
# On a server, these can be set as environment variables instead of a file.
for _key, _env in (("timezone", "TIMEZONE"), ("model", "CLAUDE_MODEL"), ("reflection_effort", "REFLECTION_EFFORT")):
    if os.environ.get(_env):
        settings[_key] = os.environ[_env]

TZ = ZoneInfo(settings["timezone"])


def resolve(p):
    p = Path(p).expanduser()
    return p if p.is_absolute() else ROOT / p


DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
if TEST_DIR:
    DATA_DIR = Path(TEST_DIR)
    LOG_DIR = DATA_DIR / "logs"

DB_PATH = DATA_DIR / "journal.db"          # the encrypted journal
BACKUP_DIR = DATA_DIR / "backups"          # daily copies of journal.db (also encrypted)

# Where the old, unencrypted version kept things (imported once, then deleted).
LEGACY_ENTRIES_DIR = DATA_DIR / "entries"
LEGACY_DRAFT_PATH = DATA_DIR / "draft.json"
LEGACY_EXCEL_PATH = (DATA_DIR / "journal.xlsx") if TEST_DIR else resolve(settings.get("excel_path") or "data/journal.xlsx")

for d in (DATA_DIR, BACKUP_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---- Editing settings from the app --------------------------------------

def _int_in(lo, hi):
    def check(v):
        v = int(v)
        if not lo <= v <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return v
    return check


def _timezone(v):
    v = str(v).strip()
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("isn't a known time zone (e.g. America/Toronto)")
    return v


def _lock_minutes(v):
    v = int(v)
    if v not in LOCK_MINUTE_CHOICES:
        raise ValueError("must be one of: " + ", ".join(str(m) for m in LOCK_MINUTE_CHOICES))
    return v


def _choice(*options):
    def check(v):
        if v not in options:
            raise ValueError(f"must be one of: {', '.join(options)}")
        return v
    return check


def _text(max_len, required=False):
    def check(v):
        v = str(v).strip()
        if required and not v:
            raise ValueError("can't be empty")
        if len(v) > max_len:
            raise ValueError(f"must be {max_len} characters or fewer")
        return v
    return check


VALIDATORS = {
    "user_name": _text(40),
    "port": _int_in(1024, 65535),
    "open_browser_on_start": bool,
    "timezone": _timezone,
    "backups_to_keep": _int_in(1, 365),
    "draft_autosave_seconds": _int_in(1, 60),
    "lock_minutes": _lock_minutes,
    "model": _text(100, required=True),
    "reflection_effort": _choice("low", "medium", "high"),
    "api_timeout_seconds": _int_in(10, 300),
    "api_retries": _int_in(0, 5),
    "theme": _choice("auto", "day", "evening"),
    "colour_theme": _choice(*[key for key, _ in COLOUR_THEMES]),
    "background_photo": bool,
    "photo_blur": _int_in(0, 30),
    "photo_visibility": _int_in(0, 100),
}


def validate(changes):
    """Check values; returns the cleaned ones. Raises ValueError with a readable message."""
    clean = {}
    for key, value in changes.items():
        if key not in VALIDATORS:
            continue
        try:
            clean[key] = VALIDATORS[key](value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} {e}") from None
    return clean


def update_settings(changes):
    """Validate and save app-wide settings to settings.json (not per-person
    preferences). Returns (saved, needs_restart). Raises ValueError."""
    clean = validate({k: v for k, v in changes.items() if k not in PREF_KEYS})

    with _save_lock:
        on_disk = public_settings()  # also drops retired settings (e.g. the old Excel path)
        changed = {k for k, v in clean.items() if on_disk.get(k) != v}
        on_disk.update(clean)
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
        tmp.write_text(json.dumps(on_disk, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
        # Live-apply everything that doesn't need a restart.
        for k in clean.keys() - RESTART_REQUIRED:
            settings[k] = clean[k]

    return public_settings(), sorted(changed & RESTART_REQUIRED)


def public_settings():
    """App-wide settings as they are on disk (what the settings page edits)."""
    on_disk = _read_file()
    return {k: on_disk.get(k, default) for k, default in DEFAULTS.items() if k not in PREF_KEYS}
