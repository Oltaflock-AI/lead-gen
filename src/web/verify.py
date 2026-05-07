"""Per-lead email lookup + multi-stage verification.

Pipeline:
  1. find_email → DDG search → top candidates ranked by `email_quality.score_email`
     (personal locals beat role accounts; business domains beat freemail).
  2. verify_mx → domain accepts mail at all.
  3. smtp_probe → server actually accepts RCPT TO for this address (best
     effort; falls back to 'unknown' on blocked port 25 or catch-all servers).
"""
import csv
import logging
import os
from pathlib import Path

from src.processors.enrich_leads import find_email, verify_mx
from .email_quality import (
    classify_local, is_disposable, is_freemail, score_email, smtp_probe,
)

log = logging.getLogger(__name__)

# Probing port-25 is slow and often blocked. On by default for local/dev,
# disable with LEADGEN_SMTP_PROBE=0 in .env or set timeout via _TIMEOUT.
SMTP_PROBE_ENABLED = os.getenv("LEADGEN_SMTP_PROBE", "1") not in ("0", "false", "no")
SMTP_PROBE_TIMEOUT = int(os.getenv("LEADGEN_SMTP_TIMEOUT", "8"))

# When True, refuse role addresses (info@, contact@, ...) outright. Caller
# decides — this lib only labels.
DROP_ROLE_DEFAULT = os.getenv("LEADGEN_DROP_ROLE", "0") in ("1", "true", "yes")


def _quality(email, business_name=""):
    if not email or "@" not in email:
        return {"kind": "unknown", "score": -100, "reasons": ["no_at_sign"]}
    s, kind, reasons = score_email(email, business_name=business_name)
    return {"kind": kind, "score": s, "reasons": reasons}


def verify_lead_email(
    business_name,
    current_email="",
    region="",
    country="",
    drop_role=None,
    probe_smtp=None,
    skip_search=False,
    deep=False,
):
    """Return a rich verification record:

    {
      email:        chosen address (or '' if nothing usable),
      verified:     True/False  — MX-passes (legacy field; for back-compat),
      source:       'existing' | 'search' | 'none',
      kind:         'personal' | 'role' | 'unknown',
      score:        int (higher = stronger candidate),
      reasons:      [str, ...] — why it scored that way,
      mx_ok:        bool,
      smtp_check:   'ok' | 'bad' | 'unknown' | 'skipped',
      legit:        bool   — mx_ok AND smtp_check != 'bad'  AND not disposable,
      dropped:      None or 'role'  — explains why we returned no email,
    }
    """
    if drop_role is None:
        drop_role = DROP_ROLE_DEFAULT
    if probe_smtp is None:
        probe_smtp = SMTP_PROBE_ENABLED

    def _build(email, source):
        if not email:
            return _empty(source)
        domain = email.split("@", 1)[1].lower()
        q = _quality(email, business_name)

        if drop_role and q["kind"] == "role":
            log.info("verify drop role: %s (%s)", email, business_name)
            return {
                **_empty("none"), "kind": "role", "score": q["score"],
                "reasons": q["reasons"], "dropped": "role",
            }
        if is_disposable(domain):
            log.info("verify drop disposable: %s", email)
            return {
                **_empty("none"), "kind": q["kind"], "score": q["score"],
                "reasons": q["reasons"] + ["disposable_domain"], "dropped": "disposable",
            }

        mx_ok = verify_mx(domain)
        smtp_result = "skipped"
        if mx_ok and probe_smtp:
            smtp_result = smtp_probe(email, timeout=SMTP_PROBE_TIMEOUT)
        legit = mx_ok and smtp_result != "bad"

        log.info(
            "verify %s: kind=%s score=%d mx=%s smtp=%s legit=%s freemail=%s",
            email, q["kind"], q["score"], mx_ok, smtp_result, legit, is_freemail(domain),
        )

        return {
            "email": email,
            "verified": mx_ok,        # legacy: True iff MX
            "source": source,
            "kind": q["kind"],
            "score": q["score"],
            "reasons": q["reasons"],
            "mx_ok": mx_ok,
            "smtp_check": smtp_result,
            "legit": bool(legit),
            "dropped": None,
        }

    # 1. Trust an existing address only if it survives the same checks.
    if current_email and "@" in current_email:
        rec = _build(current_email, "existing")
        if rec["email"] and rec.get("legit"):
            return rec
        if skip_search:
            return rec

    if skip_search:
        return _empty("none")

    # 2. Search.
    if not business_name:
        return _empty("none")
    if deep:
        from .email_finder import deep_find_email
        found, src = deep_find_email(business_name, region, country)
        if found:
            return _build(found, src or "deep_search")
        return _empty("none")
    found = find_email(business_name, region, country)
    if found:
        return _build(found, "search")
    return _empty("none")


def _empty(source):
    return {
        "email": "", "verified": False, "source": source,
        "kind": "unknown", "score": -100, "reasons": [],
        "mx_ok": False, "smtp_check": "skipped", "legit": False,
        "dropped": None,
    }


def update_csv_email(csv_path, business_name, new_email, verified=None,
                     kind=None, legit=None, smtp_check=None):
    """Write `new_email` and quality fields into the row matching `business_name`.

    `verified`   bool|None — True (MX-confirmed), False (found but no MX), None.
    `kind`       'role' | 'personal' | 'unknown' — local-part classifier.
    `legit`      bool — MX ok AND SMTP didn't reject AND not disposable.
    `smtp_check` 'ok' | 'bad' | 'unknown' | 'skipped' — RCPT TO probe outcome.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return False

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    def _find_col(candidates):
        """Case-insensitive header lookup. Returns the existing fieldname,
        or None when no candidate is present."""
        lower = {f.lower(): f for f in fieldnames}
        for c in candidates:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    name_col = _find_col(("business_name", "Business Name", "prospect_company_name"))
    if name_col is None:
        return False

    email_col = _find_col((
        "Email", "email", "contact_emails", "prospect_emails",
        "contact_professions_email",
    ))
    if email_col is None:
        email_col = "Email"
        fieldnames.insert(fieldnames.index(name_col) + 1, email_col)

    # Match aux columns to whatever casing the CSV already uses; insert in
    # the canonical (Title Case) form when nothing matches so we don't
    # collide with the lowercase legacy names.
    def _resolve(canonical, legacy):
        existing = _find_col((canonical, legacy))
        if existing:
            return existing
        fieldnames.append(canonical)
        return canonical

    verified_col = _resolve("Email Verified", "email_verified")
    kind_col     = _resolve("Email Kind",     "email_kind")
    legit_col    = _resolve("Email Legit",    "email_legit")
    smtp_col     = _resolve("Email SMTP",     "email_smtp")

    if verified is True:
        verified_str = "yes"
    elif verified is False and new_email:
        verified_str = "found"
    elif new_email:
        verified_str = "found"
    else:
        verified_str = "no"

    updated = False
    for r in rows:
        if (r.get(name_col) or "").strip() == (business_name or "").strip():
            r[email_col] = new_email
            r[verified_col] = verified_str
            if kind is not None:
                r[kind_col] = kind
            if legit is not None:
                r[legit_col] = "yes" if legit else "no"
            if smtp_check is not None:
                r[smtp_col] = smtp_check
            updated = True

    if not updated:
        return False

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return True
