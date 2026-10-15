"""The app's emails (invitations, reminders), in one branded template:
templates/email/app.html. Email programs ignore stylesheets and web fonts, so
the template is inline styles and tables, and the handwritten name and the
drawing are pictures made by tools/make_email_art.py.

page() fills in what every email shares: the cover, the edition's drawing and
verse, and the no-reply line. Each kind of email adds its own words.
"""
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config
from .config import ROOT

_ENV = Environment(loader=FileSystemLoader(ROOT / "templates" / "email"), autoescape=select_autoescape(["html"]))

NO_REPLY = ("This address doesn't receive replies. If you have a question, use the Feedback "
            "button (the speech bubble) once you're signed in.")


def _png_width(path):
    """A PNG's width, from its header (no imaging library on the server)."""
    with open(path, "rb") as f:
        return int.from_bytes(f.read(24)[16:20], "big")


def site(site_url):
    return site_url if site_url.endswith("/") else site_url + "/"


def quote():
    """The welcome page's verse (or saying) and where it's from, as plain text."""
    q = config.EDITION.get("welcome_quote") or {}
    return q.get("text"), re.sub(r"<[^>]+>", "", q.get("cite_html", ""))


def page(site_url, **words):
    """The email's HTML. `words` are the template's own parts: subject, preview,
    hello, opening, lead, paragraphs, message, who, prompt, passage, passage_cite,
    points, code, button_text, button_url, sign_in, unsubscribe_url, block_url, reply_note
    (in place of the no-reply line), vignette_url (in place of the edition's drawing)."""
    edition, home = config.EDITION, site(site_url)
    brand = f"{home}static/brands/{config.EDITION_NAME}"
    wordmark = ROOT / "static" / "brands" / config.EDITION_NAME / "email-wordmark.png"
    text, cite = quote()
    drawing = words.pop("vignette_url", None) or (f"{brand}/email-vignette.png" if edition.get("email_vignette") else None)
    return _ENV.get_template("app.html").render(
        app=config.APP_NAME, tagline=edition.get("tagline"), site_url=home,
        logo_url=f"{brand}/logo.png", wordmark_url=f"{brand}/email-wordmark.png",
        wordmark_width=_png_width(wordmark) // 2 if wordmark.exists() else 300,
        vignette_url=drawing,
        quote=text, quote_cite=cite, no_reply=NO_REPLY, **words)
