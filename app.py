"""Personal journal — web app.

On your own computer: start it with start.bat (one journal, your passphrase).
Hosted (e.g. on Render): people sign in with Google, then unlock their own
journal with their own passphrase. See README → "Hosting on Render".
"""
import io
import logging
import os
import random
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import Flask, g, jsonify, make_response, redirect, render_template, request, send_file, session

from journal import (activity, config, export, feedback, importer, invites, migrate, passkeys, reflection, stats, store, usage, users, vault,
                     sources, wipe, writing_prompts)
from journal.config import HOSTED, LOG_DIR, ROOT, TZ, settings
from journal.db import LOCAL_USER

load_dotenv(ROOT / ".env")

# ---- Logging: logs/journal.log (rotates at 1 MB, keeps 5), and the console --
# The log never contains what anyone writes: only IDs, counts and errors.
handler = RotatingFileHandler(LOG_DIR / "journal.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
handlers = [handler]
if HOSTED:
    handlers.append(logging.StreamHandler(sys.stdout))  # shows in Render's log view
logging.basicConfig(level=logging.INFO, handlers=handlers)
for noisy in ("werkzeug", "httpx", "httpx2", "httpcore", "anthropic", "urllib3", "authlib"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("journal")

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # CSV imports
MAX_ENTRY_CHARS = 200_000
CLIENT_HEADER = "X-Journal-Client"    # sent by our own page on every change
PASSIVE_HEADER = "X-Journal-Passive"  # timer-driven requests that don't count as activity
SESSION_COOKIE = "journal_session"    # which unlocked journal this browser holds
ALLOWED_HOSTS = {"localhost", "127.0.0.1"} | ({config.PUBLIC_HOST} if config.PUBLIC_HOST else set())

# Test-only shortcut that signs in without Google (never on Render).
TEST_LOGIN = os.environ.get("JOURNAL_TEST_LOGIN") == "1" and config.TEST_DIR and not os.environ.get("RENDER")

if HOSTED:
    from authlib.integrations.flask_client import OAuth
    from werkzeug.middleware.proxy_fix import ProxyFix

    if not os.environ.get("SECRET_KEY") and not TEST_LOGIN:
        raise SystemExit("SECRET_KEY must be set when hosting (see render.yaml).")
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # Render's proxy: https, real host
    app.config.update(SESSION_COOKIE_SECURE=not TEST_LOGIN, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(days=30))
    oauth = OAuth(app)
    oauth.register(
        "google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
else:
    app.secret_key = secrets.token_hex(32)  # not used for sign-in locally

# Reachable without an unlocked journal: the page and its files, signing in,
# the lock screen's requests, and a few harmless odds and ends.
PUBLIC_PATHS = {"/", "/login", "/auth/callback", "/auth/test-login", "/logout", "/privacy", "/sources", "/terms", "/healthz",
                "/favicon.ico", "/manifest.webmanifest", "/sw.js",
                "/api/status", "/api/vault", "/api/vault/setup", "/api/vault/unlock", "/api/vault/recover",
                "/api/vault/passkey-options", "/api/vault/unlock-passkey",
                "/api/background", "/api/shutdown"}


# ---- Who is asking, and is their journal unlocked? -------------------------

def _signed_in_user():
    """The user ID for this request (or None if signed out)."""
    if not HOSTED:
        return users.ensure_local()
    uid = session.get("user_id")
    if not uid:
        return None
    user = users.get(uid)
    if not user or (config.ALLOWED_EMAILS and (user["email"] or "").lower() not in config.ALLOWED_EMAILS):
        session.clear()  # removed from the allowed list since signing in
        return None
    return uid


def _old_address(host):
    """True for addresses that should forward to PUBLIC_HOST: the service's own
    *.onrender.com address (once a custom domain is set up) and www.<domain>."""
    if not (HOSTED and config.PUBLIC_HOST) or config.PUBLIC_HOST.endswith(".onrender.com"):
        return False
    return host.endswith(".onrender.com") or host == f"www.{config.PUBLIC_HOST}"


@app.before_request
def _guard():
    if request.path == "/healthz":
        return None
    # Only answer requests addressed to this app by name (blocks "DNS
    # rebinding" tricks), and only accept changes from our own page: other
    # websites can't add a custom header without the browser asking first,
    # which this app never approves.
    host = (request.host or "").rsplit(":", 1)[0].lower()
    if host not in ALLOWED_HOSTS and _old_address(host) and request.method in ("GET", "HEAD"):
        # The app's previous Render address, or www.: send people (and their bookmarks and
        # installed apps) to the real one. A redirect hands out nothing, so this is safe.
        return redirect(f"https://{config.PUBLIC_HOST}{request.full_path.rstrip('?')}", code=301)
    if host not in ALLOWED_HOSTS:
        return jsonify(error="Forbidden"), 403
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CLIENT_HEADER) != "1" \
            and request.path not in ("/logout",):
        return jsonify(error="Forbidden"), 403

    vault.use(None)
    g.user_id = _signed_in_user()
    g.token = request.cookies.get(SESSION_COOKIE)
    if request.path.startswith("/static/backgrounds/") and HOSTED and not g.user_id:
        return jsonify(error="Sign in first."), 403  # personal photos are for signed-in family only
    if request.path in PUBLIC_PATHS or request.path.startswith("/static/"):
        return None
    if not g.user_id:
        return jsonify(error="Please sign in.", signed_out=True), 401
    s = vault.session(g.token)
    if not s or s.user_id != g.user_id:
        return jsonify(error="The journal is locked.", locked=True), 423
    vault.use(s)
    if request.headers.get(PASSIVE_HEADER) != "1":
        vault.touch(g.token)
        activity.seen(g.user_id)   # for the usage stats: which days people use the app


def _lock_own_session(reason="locked"):
    """Lock this browser's session, but only if it's the signed-in person's own."""
    s = vault.session(g.token)
    if s and (not HOSTED or s.user_id == g.user_id):
        vault.lock(g.token, reason)


def _need_user():
    if not g.user_id:
        return jsonify(error="Please sign in.", signed_out=True), 401
    return None


@app.context_processor
def _static_versions():
    # ?v=<file time> makes the browser fetch fresh CSS/JS after every change.
    def static_url(filename):
        path = ROOT / "static" / filename
        version = int(path.stat().st_mtime) if path.exists() else 0
        return f"/static/{filename}?v={version}"
    return {"static_url": static_url, "app_name": config.APP_NAME, "copyright": config.COPYRIGHT, "app_version": config.APP_VERSION, "test_login": bool(HOSTED and TEST_LOGIN),
            "test_emails": sorted(config.ALLOWED_EMAILS),
            "logo_url": _brand_file("logo"), "logo_mask_url": _brand_file("logo-mask"), "hero_url": _brand_file("hero"),
            "has_icons": (BRAND_DIR / "icon-192.png").exists(), "theme_colour": THEME_COLOUR,
            "edition": config.EDITION, "brand_url": BRAND_URL, "brand_path": f"brands/{config.EDITION_NAME}"}


BRAND_DIR = config.BRAND_DIR                     # static/brands/<edition>/
BRAND_URL = f"/static/brands/{config.EDITION_NAME}"
BRAND_TYPES = (".svg", ".png", ".webp", ".jpg", ".jpeg")


def _brand_file(stem):
    """The edition's logo.* or hero.*, if one has been added (else None)."""
    for ext in BRAND_TYPES:
        path = BRAND_DIR / f"{stem}{ext}"
        if path.exists():
            return f"{BRAND_URL}/{path.name}?v={int(path.stat().st_mtime)}"
    return None


@app.after_request
def _headers(resp):
    if request.path == "/" or request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # nothing from the journal stays in the browser cache
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if HOSTED and not TEST_LOGIN:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


@app.errorhandler(vault.Locked)
def _on_locked(_err):
    return jsonify(error="The journal is locked.", locked=True), 423


@app.errorhandler(Exception)
def _on_error(err):
    code = getattr(err, "code", 500)
    if code >= 500:
        log.exception("Unhandled error on %s %s", request.method, request.path)
    return jsonify(error=getattr(err, "description", "Something went wrong.")), code


# ---- Pages ------------------------------------------------------------------

@app.get("/")
def index():
    uid = g.user_id
    prefs = users.prefs(uid) if uid else {k: config.DEFAULTS[k] for k in config.PREF_KEYS}
    return render_template(
        "index.html",
        hosted=HOSTED,
        is_admin=_is_admin(uid),
        user_name=prefs["user_name"],
        journal_name=prefs["journal_name"] or config.APP_NAME,
        autosave_seconds=prefs["draft_autosave_seconds"],
        timezone=settings["timezone"],
        theme=prefs["theme"],
        colour_theme=prefs["colour_theme"],
        colour_themes=config.COLOUR_THEMES,
        lock_choices=config.LOCK_MINUTE_CHOICES,
        background_photo=prefs["background_photo"],
        photo_blur=prefs["photo_blur"],
        photo_visibility=prefs["photo_visibility"],
    )


@app.get("/privacy")
def privacy():
    return render_template("privacy.html", hosted=HOSTED)


SITE_NAMES = {"en.wikisource.org": "Wikisource", "ccel.org": "CCEL", "www.gutenberg.org": "Project Gutenberg",
              "gutenberg.org": "Project Gutenberg", "biblehub.com": "Bible Hub"}


def source_groups():
    """This edition's quotable works, in the groups its edition.json gives
    (any work not listed there goes under "Other works", so none is hidden)."""
    works = sources.works()
    groups, listed = [], set()
    for group in config.EDITION.get("source_groups", []):
        items = []
        for work_id in group["works"]:
            w = works.get(work_id)
            if not w:
                continue
            listed.add(work_id)
            url = sources.page_url(w["work_page"])
            items.append({"author": w["author"], "title": w["title"],
                          "translation": w["translation"], "url": url,
                          "site": SITE_NAMES.get(urllib.parse.urlparse(url).hostname, "")})
        groups.append({**group, "items": items})
    rest = [w for w in works if w not in listed]
    if rest:
        groups.append({"name": "Other works", "about": "", "works": rest, "items": [
            {"author": works[w]["author"], "title": works[w]["title"], "translation": works[w]["translation"],
             "url": sources.page_url(works[w]["work_page"]), "site": ""} for w in rest]})
    return groups


TERMS_UPDATED = "October 9, 2026"   # change when the terms change


@app.get("/terms")
def terms_page():
    return render_template("terms.html", hosted=HOSTED, terms_updated=TERMS_UPDATED, edition_name=config.EDITION_NAME,
                           reflections_per_day=config.REFLECTIONS_PER_DAY or "unlimited",
                           prompts_per_day=config.PROMPTS_PER_DAY or "unlimited")


@app.get("/sources")
def sources_page():
    return render_template("sources.html", groups=source_groups(), hosted=HOSTED,
                           work_count=len(sources.works()))


@app.get("/healthz")
def healthz():
    return "ok"


# ---- Icons and "Install app" ----------------------------------------------
# Made from brand-source/<edition>/logo-original.png by tools/make_icons.py.

THEME_COLOUR = config.EDITION.get("theme_colour", "#2a4865")   # title bar and splash screen when installed


@app.get("/favicon.ico")
def favicon():
    path = BRAND_DIR / "favicon.ico"
    if not path.exists():
        return "", 404
    return send_file(path, mimetype="image/x-icon", max_age=86400)


@app.get("/sw.js")
def service_worker():
    """Lets browsers offer "Install app". It caches nothing (see static/sw.js)."""
    resp = send_file(ROOT / "static" / "sw.js", mimetype="text/javascript", max_age=0)
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    """What a phone or computer needs to offer "Install app" / "Add to Home Screen"."""
    icons = [{"src": f"{BRAND_URL}/{name}", "sizes": size, "type": "image/png", "purpose": purpose}
             for name, size, purpose in (("icon-192.png", "192x192", "any"), ("icon-512.png", "512x512", "any"),
                                         ("icon-maskable-512.png", "512x512", "maskable"))
             if (BRAND_DIR / name).exists()]
    resp = jsonify({
        "id": "/",
        "name": f"{config.APP_NAME}: {config.EDITION['tagline']}" if config.EDITION.get("tagline") else config.APP_NAME,
        "short_name": config.APP_NAME,
        "description": config.EDITION.get("description", ""),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": THEME_COLOUR,
        "theme_color": THEME_COLOUR,
        "icons": icons,
    })
    resp.mimetype = "application/manifest+json"
    return resp


# ---- Signing in with Google (hosted) ---------------------------------------

@app.get("/login")
def login():
    if not HOSTED:
        return redirect("/")
    return oauth.google.authorize_redirect(request.url_root.rstrip("/") + "/auth/callback")


def _sign_in(sub, email, name):
    email = (email or "").lower()
    if config.ALLOWED_EMAILS and email not in config.ALLOWED_EMAILS:
        log.info("Sign-in refused for an address that isn't on the allowed list")
        return redirect("/?signin=not-allowed")
    uid = users.from_google(sub, email, name)
    session.clear()
    session.permanent = True
    session["user_id"] = uid
    log.info("Signed in (%s)", uid)
    return redirect("/")


@app.get("/auth/callback")
def auth_callback():
    if not HOSTED:
        return redirect("/")
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        log.exception("Google sign-in failed")
        return redirect("/?signin=failed")
    info = token.get("userinfo") or {}
    if not info.get("email_verified"):
        return redirect("/?signin=failed")
    return _sign_in(info["sub"], info.get("email"), info.get("name"))


@app.get("/auth/test-login")
def test_login():
    """Only for automated tests on a developer's computer (never on Render)."""
    if not (HOSTED and TEST_LOGIN):
        return jsonify(error="Not found"), 404
    email = request.args.get("email", "")
    return _sign_in("test-" + email, email, email.split("@")[0].title())


@app.route("/logout", methods=["GET", "POST"])
def logout():
    _lock_own_session("locked (signed out)")
    session.clear()
    resp = make_response(redirect("/") if request.method == "GET" else jsonify(ok=True))
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ---- Background photo ---------------------------------------------------------

BACKGROUNDS_DIR = ROOT / "static" / "backgrounds"
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp"}


def background_photos():
    """This edition's photos: the shared ones in static/backgrounds/, plus its
    own in static/backgrounds/<edition>/ (e.g. backgrounds/christian/)."""
    folders = [BACKGROUNDS_DIR, BACKGROUNDS_DIR / config.EDITION_NAME]
    return sorted(p for folder in folders for p in folder.glob("*")
                  if p.is_file() and p.suffix.lower() in PHOTO_TYPES)


@app.get("/api/background")
def background():
    """A random photo for this edition, never the one just shown
    (the page passes its name as ?avoid=)."""
    photos = background_photos()
    show = users.prefs(g.user_id)["background_photo"] if g.user_id else False
    if not show or not photos:
        return jsonify(url=None)
    avoid = request.args.get("avoid")
    choices = [p for p in photos if p.relative_to(BACKGROUNDS_DIR).as_posix() != avoid] or photos
    photo = random.choice(choices)
    name = photo.relative_to(BACKGROUNDS_DIR).as_posix()     # "lake.jpg" or "christian/cross.jpg"
    return jsonify(url=f"/static/backgrounds/{urllib.parse.quote(name)}?v={int(photo.stat().st_mtime)}", name=name)


# ---- The lock: passphrase, recovery key, sessions ---------------------------

def _with_session(payload, token):
    resp = make_response(jsonify(payload))
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict", path="/",
                    secure=HOSTED and not TEST_LOGIN)
    return resp


@app.get("/api/status")
def api_status():
    s = vault.session(g.token)
    return jsonify(signed_in=bool(g.user_id),
                   unlocked=bool(s and g.user_id and s.user_id == g.user_id))


def _limits(uid):
    return {"reflections_left": usage.remaining(uid, "reflection"),
            "reflections_per_day": config.REFLECTIONS_PER_DAY or None,
            "prompts_left": usage.remaining(uid, "prompt")}


@app.get("/api/vault")
def vault_state():
    uid = g.user_id
    user = users.get(uid) if uid else None
    s = vault.session(g.token)
    return jsonify(
        mode="hosted" if HOSTED else "local",
        signed_in=bool(uid),
        email=(user or {}).get("email") if HOSTED else None,
        set_up=bool(uid) and vault.is_set_up(uid),
        unlocked=bool(s and uid and s.user_id == uid),
        legacy_entries=0 if HOSTED or (uid and vault.is_set_up(uid)) else migrate.legacy_count(),
        min_password=vault.MIN_PASSWORD_LENGTH,
        passkeys=len(passkeys.unlock_options(uid)) if uid else 0,
    )


def _import_legacy_if_any():
    """Local only: bring entries from the old unencrypted storage into the
    database, verify them, then delete the old files.
    Returns (imported, problem, cleanup_failed)."""
    if HOSTED:
        return 0, None, []
    if not migrate.legacy_count():
        return 0, None, migrate.retry_pending()
    if store.all_entries():
        return 0, None, []  # the journal already has entries; never import twice
    try:
        imported = migrate.import_legacy()
    except Exception:
        log.exception("Import of the old entries failed; old files kept")
        store.wipe_all_entries()  # remove any half-import so a retry starts clean
        return 0, ("Your old entries couldn't be imported, so they were left as they were. "
                   "The details are in logs/journal.log."), []
    return imported, None, migrate.delete_legacy()


@app.post("/api/vault/setup")
def vault_setup():
    if (resp := _need_user()):
        return resp
    data = request.get_json(silent=True) or {}
    try:
        recovery_key, token = vault.set_up(g.user_id, data.get("password") or "")
    except vault.VaultError as e:
        return jsonify(error=str(e)), 400
    vault.use(vault.session(token))
    imported, problem, cleanup_failed = _import_legacy_if_any()
    activity.seen(g.user_id)
    return _with_session({"recovery_key": recovery_key, "imported": imported,
                          "import_problem": problem, "cleanup_failed": cleanup_failed}, token)


@app.post("/api/vault/unlock")
def vault_unlock():
    if (resp := _need_user()):
        return resp
    data = request.get_json(silent=True) or {}
    try:
        token = vault.unlock(g.user_id, data.get("password") or "")
    except vault.VaultError as e:
        return jsonify(error=str(e)), 400
    _lock_own_session("replaced by a new unlock")  # this browser's previous session, if any
    vault.use(vault.session(token))
    imported, problem, cleanup_failed = _import_legacy_if_any()
    activity.seen(g.user_id)
    return _with_session({"ok": True, "imported": imported, "import_problem": problem,
                          "cleanup_failed": cleanup_failed}, token)


def _rp_id():
    """The passkey's "relying party": this site's own address (passkeys only work there)."""
    return (request.host or "").rsplit(":", 1)[0].lower()


@app.get("/api/vault/passkey-options")
def vault_passkey_options():
    if (resp := _need_user()):
        return resp
    return jsonify(rp_id=_rp_id(), credentials=passkeys.unlock_options(g.user_id))


@app.post("/api/vault/unlock-passkey")
def vault_unlock_passkey():
    if (resp := _need_user()):
        return resp
    data = request.get_json(silent=True) or {}
    try:
        token = vault.unlock_with_passkey(g.user_id, data.get("credential_id") or "", data.get("prf") or "")
    except vault.VaultError as e:
        return jsonify(error=str(e)), 400
    _lock_own_session("replaced by a new unlock")
    vault.use(vault.session(token))
    activity.seen(g.user_id)
    return _with_session({"ok": True}, token)


@app.post("/api/vault/recover")
def vault_recover():
    if (resp := _need_user()):
        return resp
    data = request.get_json(silent=True) or {}
    try:
        token = vault.recover(g.user_id, data.get("recovery_key") or "", data.get("new_password") or "")
    except vault.VaultError as e:
        return jsonify(error=str(e)), 400
    return _with_session({"ok": True}, token)


@app.post("/api/vault/lock")
def vault_lock():
    _lock_own_session()
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.post("/api/vault/password")
def vault_change_password():
    data = request.get_json(silent=True) or {}
    try:
        vault.change_password(g.user_id, data.get("current") or "", data.get("new") or "")
    except vault.VaultError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@app.post("/api/vault/recovery-key")
def vault_new_recovery_key():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(recovery_key=vault.new_recovery_key(g.user_id, data.get("password") or ""))
    except vault.VaultError as e:
        return jsonify(error=str(e)), 400


# ---- Passkeys (Settings → Security & Data) --------------------------------

@app.get("/api/passkeys")
def passkeys_list():
    user = users.get(g.user_id)
    return jsonify(
        passkeys=passkeys.list_for(g.user_id),
        rp_id=_rp_id(), rp_name=config.APP_NAME,
        # The passkey's user handle: our internal ID (not the email), so the device stores nothing personal.
        user_id=g.user_id, user_name=(user or {}).get("email") or config.APP_NAME,
        user_display=(user or {}).get("name") or (user or {}).get("email") or config.APP_NAME)


@app.post("/api/passkeys")
def passkeys_add():
    data = request.get_json(silent=True) or {}
    if data.get("check_only"):
        try:
            vault.check_password(g.user_id, data.get("password") or "")
        except vault.VaultError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True)
    try:
        vault.add_passkey(g.user_id, data.get("password") or "", data.get("credential_id") or "",
                          data.get("prf_salt") or "", data.get("prf") or "", data.get("label") or "")
    except vault.VaultError as e:
        return jsonify(error=str(e)), 400
    return jsonify(passkeys=passkeys.list_for(g.user_id))


@app.delete("/api/passkeys/<path:credential_id>")
def passkeys_remove(credential_id):
    try:
        passkeys.remove(g.user_id, credential_id)
    except KeyError:
        return jsonify(error="That passkey wasn't found."), 404
    log.info("Passkey removed (%s)", g.user_id)
    return jsonify(passkeys=passkeys.list_for(g.user_id))


# ---- Entries --------------------------------------------------------------

def _summary(e):
    return {
        "id": e["id"],
        "created_at": e["created_at"],
        "word_count": e["word_count"],
        "title": e.get("title"),
        "date": stats.local_date(e["created_at"]).isoformat(),  # local calendar day
        "favourite": bool(e.get("favourite")),
        "snippet": " ".join(e["text"].split())[:120],
        "reflection_status": e["reflection_status"],
    }


@app.get("/api/stats")
def api_stats():
    return jsonify(dict(stats.journal_stats(), **_limits(g.user_id),
                        feedback_sent=feedback.count_for(g.user_id),       # for the suggestion cards
                        invites_sent=invites.count_for(g.user_id) if HOSTED else 0))


@app.get("/api/entries")
def list_entries():
    return jsonify([_summary(e) for e in store.visible_entries()])


@app.get("/api/entries/hidden")
def list_hidden_entries():
    return jsonify([_summary(e) for e in store.all_entries() if e.get("hidden")])


def _visible(entry_id):
    entry = store.get_entry(entry_id)  # KeyError if missing (or someone else's)
    if entry.get("hidden"):
        raise KeyError(entry_id)
    return entry


def _for_page(entry, reflecting=None):
    """The entry plus what the page needs to show its reflection state."""
    if reflecting is None:
        reflecting = entry["id"] in _reflecting
    out = dict(entry, reflecting=reflecting)
    if entry.get("wellbeing_concern"):
        out["support_note"] = reflection.support_note()
    return out


@app.get("/api/entries/<entry_id>")
def get_entry(entry_id):
    try:
        return jsonify(_for_page(_visible(entry_id)))
    except KeyError:
        return jsonify(error="Entry not found."), 404


def _read_text():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return None, None, (jsonify(error="The entry is empty."), 400)
    if len(text) > MAX_ENTRY_CHARS:
        return None, None, (jsonify(error="That entry is too long to save in one go."), 400)
    return text, (data.get("prompt") or "").strip() or None, None


@app.post("/api/entries")
def create_entry():
    text, prompt, err = _read_text()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    # Saved (encrypted) before Claude is ever called.
    entry = store.create_entry(text, prompt, reflect=bool(data.get("reflect")), title=data.get("title"))
    log.info("Saved entry %s (%d words)", entry["id"], entry["word_count"])
    activity.entry_written(g.user_id)
    store.clear_draft()
    return jsonify(entry=_for_page(entry)), 201


@app.put("/api/entries/<entry_id>")
def edit_entry(entry_id):
    text, _, err = _read_text()
    if err:
        return err
    fields = {"text": text}
    data = request.get_json(silent=True) or {}
    if data.get("created_at_local"):
        # Date and time as typed in the editor, in the journal's time zone.
        try:
            when = datetime.fromisoformat(data["created_at_local"]).replace(second=0, microsecond=0, tzinfo=TZ)
        except (TypeError, ValueError):
            return jsonify(error="That date or time isn't valid."), 400
        if when > datetime.now(TZ) + timedelta(minutes=5):
            return jsonify(error="The date can't be in the future."), 400
        fields["created_at"] = when.isoformat()
    try:
        old = _visible(entry_id)
        if "title" in data:
            title = store.clean_title(data.get("title"))
            if title != old.get("title"):
                fields["title"] = title
                fields["title_source"] = "user" if title else None
        entry = store.update_entry(entry_id, **fields)
    except KeyError:
        return jsonify(error="Entry not found."), 404
    log.info("Edited entry %s", entry_id)
    return jsonify(entry=_for_page(entry))


@app.post("/api/entries/<entry_id>/favourite")
def favourite_entry(entry_id):
    data = request.get_json(silent=True) or {}
    try:
        _visible(entry_id)
        entry = store.update_entry(entry_id, touch=False, favourite=bool(data.get("favourite")))
    except KeyError:
        return jsonify(error="Entry not found."), 404
    return jsonify(entry=_for_page(entry))


@app.post("/api/entries/<entry_id>/hide")
def hide_entry(entry_id):
    try:
        store.set_hidden(entry_id, True)
    except KeyError:
        return jsonify(error="Entry not found."), 404
    return jsonify(ok=True)


@app.post("/api/entries/<entry_id>/unhide")
def unhide_entry(entry_id):
    try:
        store.set_hidden(entry_id, False)
    except KeyError:
        return jsonify(error="Entry not found."), 404
    return jsonify(ok=True)


@app.delete("/api/entries/<entry_id>")
def delete_entry(entry_id):
    try:
        failed_backups = store.delete_entry_permanently(entry_id)  # also removed from backups
    except KeyError:
        return jsonify(error="Entry not found."), 404
    log.info("Permanently deleted entry %s", entry_id)
    return jsonify(ok=True, failed_backups=failed_backups)


# ---- Reflection and prompts --------------------------------------------------

_reflecting = set()  # entry IDs with a Claude request in progress
_reflecting_lock = threading.Lock()


@app.post("/api/entries/<entry_id>/reflect")
def reflect_entry(entry_id):
    try:
        entry = _visible(entry_id)
    except KeyError:
        return jsonify(error="Entry not found."), 404
    with _reflecting_lock:
        if entry_id in _reflecting:
            return jsonify(error="Already reflecting on this entry.", entry=_for_page(entry)), 409
        _reflecting.add(entry_id)
    had_reflection = entry.get("reflection_status") == store.DONE and entry.get("insight")
    try:
        try:
            usage.check(g.user_id, "reflection")
        except usage.LimitReached as e:
            if not had_reflection:
                entry = store.update_entry(entry_id, reflection_status=store.FAILED, reflection_error=str(e))
            return jsonify(error=str(e), entry=_for_page(entry, reflecting=False), limit=True), 429
        if not had_reflection:
            store.update_entry(entry_id, reflection_status=store.PENDING, reflection_error=None)
        try:
            fields = reflection.reflect(entry)
        except reflection.ReflectionError as e:
            # "Reflect again" failing keeps the earlier reflection.
            status = store.DONE if had_reflection else store.FAILED
            entry = store.update_entry(entry_id, reflection_status=status, reflection_error=str(e))
            return jsonify(error=str(e), entry=_for_page(entry, reflecting=False)), 502
        usage.record(g.user_id, "reflection")
        suggested = store.clean_title(fields.pop("suggested_title", None))
        current = store.get_entry(entry_id)  # may have been renamed while Claude was thinking
        if suggested and not current.get("title"):
            fields.update(title=suggested, title_source="generated")
        entry = store.update_entry(entry_id, reflection_status=store.DONE, reflection_error=None, **fields)
        return jsonify(entry=_for_page(entry, reflecting=False), **_limits(g.user_id))
    finally:
        with _reflecting_lock:
            _reflecting.discard(entry_id)


@app.post("/api/writing-prompt")
def writing_prompt():
    data = request.get_json(silent=True) or {}
    shown = [str(p)[:500] for p in (data.get("already_suggested") or [])][:10]
    try:
        usage.check(g.user_id, "prompt")
        result = writing_prompts.suggest(shown)
    except usage.LimitReached as e:
        return jsonify(error=str(e), limit=True), 429
    except reflection.ReflectionError as e:
        return jsonify(error=str(e)), 502
    usage.record(g.user_id, "prompt")
    return jsonify(result)


@app.post("/api/test-key")
def test_key():
    if HOSTED:
        return jsonify(error="Not available here."), 404
    try:
        return jsonify(ok=True, message=reflection.test_key())
    except reflection.ReflectionError as e:
        return jsonify(ok=False, message=str(e))


# ---- Export, import, wipe, settings -------------------------------------------

@app.get("/api/export")
def export_csv():
    """Download the journal as a CSV file (an unencrypted copy, on request)."""
    data = export.journal_csv().encode("utf-8")
    name = f"journal-{datetime.now(TZ):%Y-%m-%d}.csv"
    log.info("Exported journal (%s)", g.user_id)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name, mimetype="text/csv")


@app.post("/api/import")
def import_csv():
    f = request.files.get("file")
    if not f:
        return jsonify(error="Choose a CSV file exported from the journal."), 400
    try:
        imported, skipped = importer.import_csv(f.read())
    except importer.ImportProblem as e:
        return jsonify(error=str(e)), 400
    log.info("Imported %d entries from CSV, skipped %d already present (%s)", imported, skipped, g.user_id)
    return jsonify(ok=True, imported=imported, skipped=skipped)


@app.post("/api/wipe/challenge")
def wipe_challenge():
    return jsonify(wipe.new_challenge(g.user_id))


@app.post("/api/wipe")
def wipe_journal():
    data = request.get_json(silent=True) or {}
    ok, message, details = wipe.wipe(data.get("id"), data.get("answer"), g.user_id)
    if not ok:
        return jsonify(ok=False, error=message), 400
    return jsonify(ok=True, message=message, **details)


@app.get("/api/settings")
def get_settings():
    merged = dict(users.prefs(g.user_id))
    if not HOSTED:
        merged.update(config.public_settings())  # app-wide settings only on your own computer
    return jsonify(settings=merged, hosted=HOSTED, restart_required=sorted(config.RESTART_REQUIRED))


@app.put("/api/settings")
def put_settings():
    data = request.get_json(force=True, silent=True) or {}
    try:
        prefs = users.update_prefs(g.user_id, data)
        needs_restart = []
        if not HOSTED:
            _, needs_restart = config.update_settings(data)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    merged = dict(prefs)
    if not HOSTED:
        merged.update(config.public_settings())
    return jsonify(settings=merged, needs_restart=needs_restart)


# ---- Feedback -------------------------------------------------------------

def _is_admin(uid):
    """Can read the feedback everyone sends: you, on your own computer;
    the addresses in ADMIN_EMAILS, on the web."""
    if not HOSTED:
        return True
    user = users.get(uid) if uid else None
    return bool(user and (user["email"] or "").lower() in config.ADMIN_EMAILS)


@app.post("/api/feedback")
def send_feedback():
    data = request.get_json(silent=True) or {}
    user = users.get(g.user_id) or {}
    try:
        fid = feedback.add(g.user_id, user.get("email"), users.prefs(g.user_id)["user_name"] or user.get("name"),
                           data.get("kind"), data.get("text"), data.get("screen"),
                           request.headers.get("User-Agent"))
    except feedback.FeedbackProblem as e:
        return jsonify(error=str(e)), 400
    log.info("Feedback #%d received (%s)", fid, g.user_id)
    return jsonify(ok=True)


def _need_admin():
    if not _is_admin(g.user_id):
        return jsonify(error="Not found"), 404
    return None


@app.get("/api/feedback")
def list_feedback():
    if (resp := _need_admin()):
        return resp
    return jsonify(feedback.all_feedback())


@app.post("/api/feedback/<int:feedback_id>/done")
def feedback_done(feedback_id):
    if (resp := _need_admin()):
        return resp
    try:
        feedback.set_done(feedback_id, bool((request.get_json(silent=True) or {}).get("done")))
    except KeyError:
        return jsonify(error="Not found"), 404
    return jsonify(ok=True)


@app.get("/api/feedback/export")
def export_feedback():
    if (resp := _need_admin()):
        return resp
    data = feedback.as_csv().encode("utf-8")
    return send_file(io.BytesIO(data), as_attachment=True, mimetype="text/csv",
                     download_name=f"reflections-feedback-{datetime.now(TZ):%Y-%m-%d}.csv")


# ---- Invitations (web version) ------------------------------------------------

@app.post("/api/invites")
def request_invite():
    """Ask for someone to be added to the invite list (goes to whoever runs the app)."""
    if not HOSTED:
        return jsonify(error="Not available here."), 404
    data = request.get_json(silent=True) or {}
    user = users.get(g.user_id) or {}
    try:
        inv = invites.add(g.user_id, user.get("email"), users.prefs(g.user_id)["user_name"] or user.get("name"),
                          data.get("email"), data.get("name"), data.get("note"))
    except invites.InviteProblem as e:
        return jsonify(error=str(e)), 400
    log.info("Invitation request #%d (%s)", inv["id"], g.user_id)
    return jsonify(ok=True, invites=invites.mine(g.user_id))


@app.get("/api/invites")
def my_invites():
    if not HOSTED:
        return jsonify([])
    return jsonify(invites.mine(g.user_id))


@app.get("/api/admin/invites")
def all_invites():
    if (resp := _need_admin()):
        return resp
    return jsonify(invites.all_requests())


@app.post("/api/admin/invites/<int:invite_id>")
def invite_status(invite_id):
    if (resp := _need_admin()):
        return resp
    try:
        invites.set_status(invite_id, (request.get_json(silent=True) or {}).get("status"))
    except ValueError:
        return jsonify(error="Unknown status."), 400
    except KeyError:
        return jsonify(error="Not found"), 404
    return jsonify(ok=True)


# ---- Usage stats (admins) -------------------------------------------------

@app.get("/api/admin/usage")
def usage_stats():
    """Who uses the app and how often: days active, entries and reflections
    (counts only; nothing anyone wrote)."""
    if (resp := _need_admin()):
        return resp
    stats = activity.summary()
    stats["invited"] = len(config.ALLOWED_EMAILS) if HOSTED else None
    return jsonify(stats)


# ---- Drafts ---------------------------------------------------------------

@app.get("/api/draft")
def get_draft():
    return jsonify(store.load_draft() or {})


@app.post("/api/draft")
def save_draft():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text") or ""
    prompt = (data.get("prompt") or "").strip()[:1000] or None
    if text.strip() or prompt:
        store.save_draft(text[:MAX_ENTRY_CHARS], prompt, store.clean_title(data.get("title")))
    else:
        store.clear_draft()
    return jsonify(ok=True)


@app.delete("/api/draft")
def delete_draft():
    store.clear_draft()
    return jsonify(ok=True)


# ---- Starting and stopping (your own computer) ------------------------------

@app.post("/api/shutdown")
def shutdown():
    """Lets a newly started copy of the app replace this one (not on the server)."""
    if HOSTED and not TEST_LOGIN:
        return jsonify(error="Not found"), 404
    log.info("Shutting down so a new copy of the app can start")
    vault.lock_all("locked (app closing)")
    threading.Timer(0.3, os._exit, args=[0]).start()
    return jsonify(ok=True)


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _replace_running_copy(port):
    """Ask a copy that's already running to exit. Returns True once the port is free."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown", method="POST", headers={CLIENT_HEADER: "1"}
    )
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except (urllib.error.URLError, OSError):
        return False  # not our app, or an older copy without this feature
    for _ in range(50):
        if not _port_in_use(port):
            return True
        time.sleep(0.1)
    return False


def main():
    """Run on your own computer (start.bat)."""
    port = int(settings["port"])
    url = f"http://localhost:{port}"
    if _port_in_use(port):
        print("  Replacing the copy of the journal that's already running...")
        if not _replace_running_copy(port):
            print(
                f"\n  Something else is already using {url}.\n"
                "  If an older journal window is open, close it and run start.bat again.\n"
                "  (Or change \"port\" in config/settings.json.)\n"
            )
            return 1
    if not HOSTED:
        users.ensure_local()
        migrate.retry_pending()     # finish deleting old unencrypted files, if any were stuck
    vault.start_auto_lock()
    log.info("Starting journal on %s", url)
    print(f"\n  Journal is running at {url}\n  Keep this window open while you write. Close it to stop the app.\n")
    if settings["open_browser_on_start"]:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    # 127.0.0.1 only: the app is not reachable from other computers.
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
    return 0


if HOSTED and __name__ != "__main__":
    # Running under gunicorn on the server.
    vault.start_auto_lock()
    log.info("Journal started (hosted%s)", ", TEST SIGN-IN ENABLED" if TEST_LOGIN else "")

if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        log.exception("Journal crashed on startup")
        raise
    sys.exit(code)
