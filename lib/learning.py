"""Phase 2 — the self-improving layer.

The sequencer tags every send with the subject ANGLE it used. The Resend webhook
later logs opened/clicked events against the same (sequence, step). This module
joins the two to answer one question: which subject angles actually get opened?

recompute_angle_performance() aggregates that into the angle_performance table
(nightly, via api/cron/learning_tick). sequence.angle_for() then reads it and
biases new sends toward the winners (epsilon-greedy), while still exploring so
the rates keep improving. open_rate is Laplace-smoothed so a 1-for-1 fluke angle
never beats a proven one on sample size.

All rates are "fraction of distinct sends that earned at least one open", not raw
event counts, so a single over-opening lead can't skew an angle.
"""
import os
from datetime import datetime, timedelta, timezone

from lib import supabase as sb

# Only learn from recent history so stale copy doesn't anchor the model forever.
WINDOW_DAYS   = int(os.environ.get("LEADGEN_LEARN_WINDOW_DAYS", "90"))
MIN_SENT      = int(os.environ.get("LEADGEN_ANGLE_MIN_SENT", "20"))   # min sample before an angle can "win"
_MAX_EVENTS   = 100000


def _window_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()


def recompute_angle_performance() -> dict:
    """Scan sequence_events, tally open/click rate per subject angle, upsert into
    angle_performance. Returns a JSON-serializable summary."""
    since = _window_iso()
    sent = sb.select(
        "sequence_events",
        {"select": "sequence_id,step,meta", "event_type": "eq.sent", "ts": f"gte.{since}"},
        limit=_MAX_EVENTS,
    )
    eng = sb.select(
        "sequence_events",
        {"select": "sequence_id,step,event_type", "event_type": "in.(opened,clicked)", "ts": f"gte.{since}"},
        limit=_MAX_EVENTS,
    )

    # (seq, step) -> angle, and the set of distinct sends per angle.
    angle_of: dict[tuple, str] = {}
    sent_keys: dict[str, set] = {}
    for r in sent:
        angle = ((r.get("meta") or {}).get("angle")) or None
        if not angle:
            continue
        key = (r.get("sequence_id"), r.get("step"))
        angle_of[key] = angle
        sent_keys.setdefault(angle, set()).add(key)

    opened_keys, clicked_keys = set(), set()
    for r in eng:
        key = (r.get("sequence_id"), r.get("step"))
        if key not in angle_of:
            continue
        if r.get("event_type") == "opened":
            opened_keys.add(key)
        elif r.get("event_type") == "clicked":
            clicked_keys.add(key)

    rows = []
    for angle, keys in sent_keys.items():
        s = len(keys)
        o = len(keys & opened_keys)
        c = len(keys & clicked_keys)
        rows.append({
            "angle": angle,
            "sent": s,
            "opened": o,
            "clicked": c,
            "open_rate": round((o + 1) / (s + 2), 4),    # Laplace smoothing
            "click_rate": round((c + 1) / (s + 2), 4),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    if rows:
        sb.insert("angle_performance", rows, on_conflict="angle")

    rows.sort(key=lambda r: r["open_rate"], reverse=True)
    return {
        "ok": True, "window_days": WINDOW_DAYS,
        "angles": len(rows), "sent_events": len(sent), "engagement_events": len(eng),
        "top": [{"angle": r["angle"], "sent": r["sent"], "open_rate": r["open_rate"]} for r in rows[:5]],
    }


# ─────────── Read side (used by the sequencer at draft time) ───────────
_CACHE: list[dict] | None = None  # per-process; cron workers are short-lived


def _load() -> list[dict]:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = sb.select("angle_performance", {"select": "*"}, limit=1000) or []
        except Exception:
            _CACHE = []
    return _CACHE


def best_angles(k: int = 3, min_sent: int | None = None) -> list[str]:
    """Top angle NAMES by smoothed open rate, among those with enough samples.
    Empty during cold start → caller falls back to deterministic exploration."""
    floor = MIN_SENT if min_sent is None else min_sent
    rows = [r for r in _load() if (r.get("sent") or 0) >= floor]
    rows.sort(key=lambda r: (r.get("open_rate") or 0), reverse=True)
    return [r["angle"] for r in rows[:k]]


def whats_working_brief() -> str:
    """One-line summary of the best-performing angles, injected into the draft
    system prompt so the model leans into proven styles. Empty until we have data."""
    rows = [r for r in _load() if (r.get("sent") or 0) >= MIN_SENT]
    if not rows:
        return ""
    rows.sort(key=lambda r: (r.get("open_rate") or 0), reverse=True)
    top = rows[:3]
    parts = [f"'{r['angle']}' ({round((r.get('open_rate') or 0) * 100)}% open)" for r in top]
    return ("WHAT IS WORKING (live data from prior sends, higher = more opens): "
            + ", ".join(parts) + ". Lean into the spirit of these styles.")
