"""Shared composer for outbound emails.

Every cold email we send goes through `compose(...)` so that:
- Body is wrapped in Georgia 12pt
- Structured signature (name / title / company / website / cal link) is
  always appended
- Plain-text fallback is generated automatically for clients without HTML
"""
from html import escape

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
