"""Shared composer for outbound emails.

Every cold email we send goes through `compose(...)` so that:
- Body is wrapped in Georgia 12pt
- Structured signature (name / title / company / website / cal link) is
  always appended
- Plain-text fallback is generated automatically for clients without HTML
- Any trailing signoff the LLM may have inserted (old prompt, manual edit)
  is stripped first so the structured signature is the only one
"""
import re
from html import escape

_SIGNOFF_KEYWORDS = (
    "best", "best,", "best regards", "regards", "cheers", "thanks",
    "thank you", "sincerely", "warmly", "kind regards", "talk soon",
    "speak soon", "yours", "all the best",
)


def _strip_trailing_signoff(body, settings):
    """Strip any signoff block the LLM left at the end of `body`.

    The structured signature is appended afterwards, so removing what the
    model wrote prevents duplicate signoffs (e.g. "Khush\\nOltaFlock AI"
    followed by the real Khush Mutha / Founder / Oltaflock AI block).

    Conservative: only strips the LAST contiguous run of "signoff-like"
    short lines from the bottom — never touches earlier paragraphs.
    """
    if not body:
        return body
    sender = (settings.get("sender_name") or "").strip().lower()
    company = (settings.get("company_name") or "").strip().lower()
    sender_first = sender.split()[0] if sender else ""

    lines = body.rstrip().splitlines()
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        low = last.lower().rstrip(",.;:!")
        is_signoff = (
            low in _SIGNOFF_KEYWORDS
            or any(low.startswith(k) for k in _SIGNOFF_KEYWORDS)
            # A line that's just the sender's full name or first name.
            or (sender and low == sender)
            or (sender_first and low == sender_first)
            # A line that's the company name (with or without "AI").
            or (company and (low == company or low == company.replace(" ai", "")))
            # Common LLM hallucination: "OltaFlock AI", "Oltaflock AI".
            or low in ("oltaflock ai", "oltaflock", "oltaflock.ai")
            # Two-token name lines like "Khush Mutha".
            or (sender and low in sender)
        )
        if is_signoff and len(last) <= 60:
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()

GEORGIA_STYLE = (
    "font-family: Georgia, 'Times New Roman', Times, serif; "
    "font-size: 12pt; "
    "color: #222; "
    "line-height: 1.55;"
)


def _body_to_html(body):
    """Convert plain-text body (paragraphs separated by blank lines) to HTML."""
    paragraphs = [p.strip() for p in body.replace("\r\n", "\n").split("\n\n") if p.strip()]
    return "\n".join(
        f'<p style="{GEORGIA_STYLE} margin: 0 0 14px;">{escape(p).replace(chr(10), "<br>")}</p>'
        for p in paragraphs
    )


def _signature_html(settings):
    name = (settings.get("sender_name") or "").strip()
    title = (settings.get("sender_title") or "").strip()
    company = (settings.get("company_name") or "").strip()
    website = (settings.get("website_url") or "").strip()
    booking = (settings.get("booking_url") or "").strip()

    role = ""
    if title and company:
        role = f"{escape(title)}, {escape(company)}"
    elif company:
        role = escape(company)
    elif title:
        role = escape(title)

    parts = []
    if name:
        parts.append(f'<strong style="color:#111;">{escape(name)}</strong>')
    if role:
        parts.append(role)
    if website:
        parts.append(f'<a href="{escape(website)}" style="color:#1664b8; text-decoration:none;">{escape(website.replace("https://","").replace("http://",""))}</a>')
    if booking:
        parts.append(f'Book a call &nbsp;<a href="{escape(booking)}" style="color:#1664b8; text-decoration:none;">{escape(booking.replace("https://","").replace("http://",""))}</a>')

    if not parts:
        return ""

    inner = "<br>".join(parts)
    return (
        '<p style="' + GEORGIA_STYLE +
        ' margin: 24px 0 0; padding-top: 12px; border-top: 1px solid #e5e5e5;">'
        + inner +
        "</p>"
    )


def _signature_text(settings):
    """Plain-text signature for the text/plain alternative."""
    parts = []
    name = (settings.get("sender_name") or "").strip()
    title = (settings.get("sender_title") or "").strip()
    company = (settings.get("company_name") or "").strip()
    website = (settings.get("website_url") or "").strip()
    booking = (settings.get("booking_url") or "").strip()
    if name:
        parts.append(name)
    if title and company:
        parts.append(f"{title}, {company}")
    elif company:
        parts.append(company)
    if website:
        parts.append(website)
    if booking:
        parts.append(f"Book a call: {booking}")
    return "\n".join(parts)


def compose(body, settings):
    """Return (text, html) versions of an outbound email."""
    body = _strip_trailing_signoff(body or "", settings)
    sig_html = _signature_html(settings)
    sig_text = _signature_text(settings)

    html = (
        '<!doctype html><html><body style="margin:0; padding:16px; background:#fff;">'
        '<div style="max-width: 640px;">'
        + _body_to_html(body)
        + sig_html
        + "</div></body></html>"
    )
    text = body.rstrip() + ("\n\n" + sig_text if sig_text else "")
    return text, html
