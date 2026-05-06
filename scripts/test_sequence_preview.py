"""Send the full 7-step drip back-to-back to a single inbox so the operator
can eyeball every template in one sitting (instead of waiting 28 days).

Run from project root:
    python -m scripts.test_sequence_preview khush@oltaflock.ai

What it does:
    1. Loads .env (Resend + Anthropic keys).
    2. Ensures a 'plumbers' niche_offer exists (seeds from the playbook if missing).
    3. Builds a realistic home-services stub lead.
    4. Calls sequencer.draft_all_steps() — Claude drafts all 7 emails up-front.
    5. Sends each step via Resend with a ~12s gap so the inbox renders them in
       order and Resend doesn't rate-limit.
    6. Prints subject + word count + first body line for every step.

Nothing is written to outreach_log / sequences. This is preview-only.
"""
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

from src.web import db, resend_send, sequencer  # noqa: E402
from src.web.email_compose import compose as compose_email  # noqa: E402

PLUMBERS_OFFER = (
    "AI Voice Agent + Missed-Call SMS Recovery for plumbers. Answers every "
    "inbound call 24/7 with a local-accent voice, triages emergency vs "
    "scheduled jobs, books straight into the calendar, and fires an SMS "
    "within 15 seconds if the caller hangs up before pickup. "
    "Pricing: $0 setup, $0 monthly, $0 contract. You only pay when the "
    "agent books a job. 30-day '$100 back if we don't book one' guarantee. "
    "Risk-reversal verbatim: 'You don't pay a cent until we book your next "
    "job. If we don't book a single job in 30 days, I send you $100 of my "
    "own money. You still owe nothing.'"
)
PLUMBERS_TONE = (
    "Aussie blunt, direct, outcome-led. No corporate filler, no buzzwords, "
    "no em dashes. Use trade language: 'after-hours', 'callouts', 'burst "
    "pipe vs slow drip'. One specific observation per email tied to the "
    "lead facts. Subject lines lowercase, under 50 chars."
)
PLUMBERS_LOOM = "https://www.loom.com/share/oltaflock-plumber-walkthrough"


def ensure_offer():
    existing = db.get_niche_offer("plumbers")
    if existing and existing.get("offer"):
        print("  ✓ plumbers offer already seeded")
        return
    db.upsert_niche_offer(
        niche="plumbers",
        offer=PLUMBERS_OFFER,
        tone=PLUMBERS_TONE,
        loom_url=PLUMBERS_LOOM,
        brief_md="",
    )
    print("  ✓ seeded plumbers offer (offer + tone + loom)")


def main():
    if len(sys.argv) < 2:
        print("usage: python -m scripts.test_sequence_preview <to_email>")
        sys.exit(2)
    to_addr = sys.argv[1].strip()

    db.init_db()

    print("== preflight ==")
    if not resend_send.is_configured():
        print("  ✗ Resend not configured (RESEND_API_KEY / RESEND_FROM)")
        sys.exit(1)
    print(f"  ✓ Resend ready, from = {resend_send.DEFAULT_FROM}")
    ensure_offer()

    lead = {
        "email": to_addr,
        "business_name": "Acme Plumbing",
        "city": "Sydney",
        "niche": "plumbers",
        "business_type": "plumbers",
        "rating": 4.7,
        "review_count": 335,
        "website": "acmeplumbing.com.au",
    }
    settings = db.get_settings()
    sender = settings.get("sender_name", "Khush")

    print(f"\n== drafting all 7 steps for {lead['business_name']} ==")
    drafts = sequencer.draft_all_steps(lead, "plumbers", sender_name=sender)
    for i, d in enumerate(drafts, 1):
        wc = len(d["body"].split())
        first_line = d["body"].splitlines()[0] if d["body"] else ""
        print(f"  step {i}: subj={d['subject']!r}  wc={wc}  -> {first_line[:70]}")
        if d.get("error"):
            print(f"           (LLM error -> fallback: {d['error']})")

    print(f"\n== sending all 7 to {to_addr} (gap=12s) ==")
    sent_ids = []
    for i, d in enumerate(drafts, 1):
        # Prefix subject so the inbox shows ordering.
        subj = f"[seq {i}/7] {d['subject']}"
        text_body, html_body = compose_email(d["body"], settings)
        try:
            mid = resend_send.send_email(to_addr, subj, text_body, html_body)
            sent_ids.append((i, mid))
            print(f"  ✓ step {i} sent  resend_id={mid}")
        except Exception as e:
            print(f"  ✗ step {i} FAILED  {e}")
        if i < len(drafts):
            time.sleep(12)

    print("\n== summary ==")
    print(f"  sent {len(sent_ids)}/7 to {to_addr}")
    for i, mid in sent_ids:
        print(f"    step {i}  {mid}")
    print(
        "\n  Note: this is a preview blast — no rows added to "
        "outreach_log or sequences. The real /api/outreach/send path "
        "auto-enrols at proper 0/3/7/11/16/21/28-day cadence."
    )


if __name__ == "__main__":
    main()
