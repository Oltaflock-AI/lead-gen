"""Gmail send via OAuth (uses the same Google credentials as Sheets export)."""
import base64
from email.message import EmailMessage

from googleapiclient.discovery import build

from .sheets import get_credentials_from_dict


def send_email(cred_data, to_addr, subject, body, signature=""):
    """Send a plain-text email via Gmail API. Returns the Gmail message id."""
    creds = get_credentials_from_dict(cred_data)
    service = build("gmail", "v1", credentials=creds)

    msg = EmailMessage()
    full_body = body
    if signature and signature.strip() not in body:
        full_body = body.rstrip() + "\n\n" + signature
    msg.set_content(full_body)
    msg["To"] = to_addr
    msg["Subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return sent.get("id")
