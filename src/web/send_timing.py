"""Country-aware send-time scheduling.

The operator runs Flask in IST. Cold prospects live in AU / US / UK / DE /
etc. A naive day-offset scheduler would fire step 2 at "now + 3 days" UTC,
which translates to 4am Sydney for an Aussie tradie — terrible.

This module snaps any scheduled-for time to the next mid-morning weekday
slot in the prospect's local timezone, so step 2-7 always land at a
sensible time regardless of when the operator clicked Send.

Universal across niches — drop a country code in, get a UTC datetime back.

Public API:
  - `BUSINESS_HOURS`     : country → (IANA tz, preferred hour 0-23)
  - `next_send_at(country, earliest_utc)` : aware UTC datetime to schedule for
  - `format_local(dt, country)`            : human "Tue 10:00 am Sydney" string
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# (timezone, preferred local send hour). Hours skew toward 9-11am because
# every credible cold-email benchmark (Mailchimp, Yesware, HubSpot) puts
# open rates highest in the mid-morning local window.
BUSINESS_HOURS: dict[str, tuple[str, int]] = {
    "AU": ("Australia/Sydney",      10),
    "NZ": ("Pacific/Auckland",      10),
    "US": ("America/New_York",      10),  # ET — covers the largest US slice
    "CA": ("America/Toronto",       10),
    "UK": ("Europe/London",         10),
    "IE": ("Europe/Dublin",         10),
    "IN": ("Asia/Kolkata",          11),
    "DE": ("Europe/Berlin",          9),
    "FR": ("Europe/Paris",           9),
    "ES": ("Europe/Madrid",          9),
    "IT": ("Europe/Rome",            9),
    "NL": ("Europe/Amsterdam",       9),
    "SE": ("Europe/Stockholm",       9),
    "BE": ("Europe/Brussels",        9),
    "AT": ("Europe/Vienna",          9),
    "CH": ("Europe/Zurich",          9),
    "DK": ("Europe/Copenhagen",      9),
    "NO": ("Europe/Oslo",            9),
    "FI": ("Europe/Helsinki",        9),
    "PT": ("Europe/Lisbon",          9),
}

DEFAULT_TZ = os.getenv("LEADGEN_DEFAULT_TZ", "UTC")
DEFAULT_HOUR = int(os.getenv("LEADGEN_DEFAULT_SEND_HOUR", "10"))

# Global override — when set, EVERY sequence is scheduled at this hour in
# this tz, regardless of the prospect's country. Useful when the operator
# wants all sends to land at a single fixed local time (e.g. "8am EST for
# every outreach campaign").
FORCE_TZ = os.getenv("LEADGEN_FORCE_SEND_TZ", "").strip()
FORCE_HOUR = os.getenv("LEADGEN_FORCE_SEND_HOUR", "").strip()

# Mon=0 ... Sun=6. Tue/Wed/Thu have the highest cold-email open rates and
# the lowest "weekend backlog" effect. Configurable via env so the operator
# can broaden to Mon-Fri if they prefer volume over peak windows.
SEND_WEEKDAYS = tuple(
    int(x) for x in os.getenv("LEADGEN_SEND_WEEKDAYS", "1,2,3").split(",") if x.strip()
)


def _resolve(country: str) -> tuple[str, int]:
    code = (country or "").strip().upper()
    return BUSINESS_HOURS.get(code, (DEFAULT_TZ, DEFAULT_HOUR))


def next_send_at(country: str, earliest_utc: datetime) -> datetime:
    """Snap `earliest_utc` forward to the next preferred-hour, allowed-weekday
    slot in the prospect's local timezone. Always returns an aware UTC
    datetime so callers can `.isoformat()` it directly into SQLite.

    Algorithm:
      1. Convert `earliest_utc` to local prospect time.
      2. Build today's preferred-hour candidate.
      3. If candidate <= local now, push to tomorrow.
      4. Walk forward day-by-day until weekday is in SEND_WEEKDAYS.
      5. Return as UTC.
    """
    if earliest_utc.tzinfo is None:
        earliest_utc = earliest_utc.replace(tzinfo=timezone.utc)
    tz_name, hour = _resolve(country)
    try:
        tz = ZoneInfo(tz_name)
    except Exception as e:
        log.warning("unknown tz %r for country=%r — falling back to UTC: %s",
                    tz_name, country, e)
        tz = ZoneInfo("UTC")

    local = earliest_utc.astimezone(tz)
    candidate = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    # Walk to next allowed weekday.
    safety = 0
    while SEND_WEEKDAYS and candidate.weekday() not in SEND_WEEKDAYS:
        candidate += timedelta(days=1)
        safety += 1
        if safety > 14:  # impossible if SEND_WEEKDAYS non-empty
            break
    return candidate.astimezone(timezone.utc)


def format_local(dt_utc: datetime, country: str) -> str:
    """Human-readable 'Tue Mar 12, 10:00 Sydney' for the dashboard."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    tz_name, _ = _resolve(country)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    local = dt_utc.astimezone(tz)
    city = tz_name.rsplit("/", 1)[-1].replace("_", " ")
    return local.strftime(f"%a %b %-d, %-I:%M %p {city}")
