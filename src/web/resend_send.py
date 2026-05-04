"""Resend email send. Returns message id."""
import os
import resend

resend.api_key = os.getenv("RESEND_API_KEY", "")
DEFAULT_FROM = os.getenv("RESEND_FROM", "")


class ResendError(Exception):
    pass


def is_configured():
    return bool(os.getenv("RESEND_API_KEY") and os.getenv("RESEND_FROM"))


def send_email(to_addr, subject, body, signature="", from_addr=None):
    if not resend.api_key:
        raise ResendError("RESEND_API_KEY not set")
    sender = from_addr or DEFAULT_FROM
    if not sender:
        raise ResendError("RESEND_FROM not set")

    full_body = body
    if signature and signature.strip() not in body:
        full_body = body.rstrip() + "\n\n" + signature

    params = {
        "from": sender,
        "to": [to_addr],
        "subject": subject,
        "text": full_body,
    }
    res = resend.Emails.send(params)
    return res.get("id")
