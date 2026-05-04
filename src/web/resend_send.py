"""Resend email send. Returns message id."""
import os
import resend

resend.api_key = os.getenv("RESEND_API_KEY", "")
DEFAULT_FROM = os.getenv("RESEND_FROM", "")


class ResendError(Exception):
    pass


def is_configured():
    return bool(os.getenv("RESEND_API_KEY") and os.getenv("RESEND_FROM"))


def send_email(to_addr, subject, text_body, html_body, from_addr=None):
    """Send a multipart text+html email via Resend. Returns the message id.

    `text_body` and `html_body` already include the signature.
    """
    if not resend.api_key:
        raise ResendError("RESEND_API_KEY not set")
    sender = from_addr or DEFAULT_FROM
    if not sender:
        raise ResendError("RESEND_FROM not set")

    params = {
        "from": sender,
        "to": [to_addr],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        params["html"] = html_body
    res = resend.Emails.send(params)
    return res.get("id")
