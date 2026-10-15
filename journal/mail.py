"""Sending email (invitations), through Resend's API (https://resend.com).

Needs RESEND_API_KEY, and the sending domain (PUBLIC_HOST, e.g. inhissteps.app)
verified in Resend. Mail comes from "<app name> <invitations@<domain>>", or
MAIL_FROM if set, with no reply address: the message itself points to the
Feedback button instead. Without a key nothing is sent, and the caller says so.

Resend receives only what's in the message: the recipient's address and name,
and the words of the invitation. Nothing anyone has written in their journal.
"""
import logging
import os

import requests

from . import config

log = logging.getLogger(__name__)

API_URL = "https://api.resend.com/emails"
TIMEOUT = 15


class MailProblem(Exception):
    """The message couldn't be sent; safe to show whoever runs the app."""


def sender():
    """The From line, or None when there's no domain to send from."""
    if os.environ.get("MAIL_FROM"):
        return os.environ["MAIL_FROM"].strip()
    host = config.PUBLIC_HOST
    if not host or host.endswith(".onrender.com"):
        return None
    return f"{config.APP_NAME} <invitations@{host}>"


def ready():
    return bool(os.environ.get("RESEND_API_KEY") and sender())


def send(to, subject, text, html):
    """Send one message. Raises MailProblem if it can't be sent."""
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise MailProblem("No email service is set up yet (RESEND_API_KEY on Render).")
    if not sender():
        raise MailProblem("There's no address to send from yet (PUBLIC_HOST on Render).")
    try:
        r = requests.post(API_URL, timeout=TIMEOUT, json={
            "from": sender(), "to": [to], "subject": subject, "text": text, "html": html,
        }, headers={"Authorization": f"Bearer {key}", "User-Agent": f"{config.APP_NAME} journal"})
    except requests.RequestException:
        log.warning("Couldn't reach the email service")
        raise MailProblem("Couldn't reach the email service. Try again in a minute.") from None
    if r.status_code >= 300:
        try:
            detail = r.json().get("message") or ""
        except ValueError:
            detail = ""
        log.warning("The email service refused a message (%s): %s", r.status_code, detail)
        raise MailProblem(f"The email service refused it ({r.status_code}){': ' + detail if detail else ''}.")
    log.info("Email sent")
