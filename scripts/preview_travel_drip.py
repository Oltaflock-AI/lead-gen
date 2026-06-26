"""Preview the EXACT outbound emails for the travel-agency drip, no sending.

Renders each lead's email exactly as Resend would receive it: the body plus the
auto-appended signature, run through the same strip_dashes / signature pipeline
the live sender uses. Step 1 is the hand-reviewed seeded copy (sent verbatim);
steps 2-7 are drafted live by Claude using the travel instruction set (only when
--full is passed and ANTHROPIC_API_KEY is set).

Usage (from project root):
    python -m scripts.preview_travel_drip                 # step 1 for all leads
    python -m scripts.preview_travel_drip --lead 1        # step 1 for lead #1
    python -m scripts.preview_travel_drip --lead 1 --full # all 7 steps, lead #1
"""
import argparse
import csv
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

from lib import sequence as seq  # noqa: E402

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "travel_agencies_personalized_emails.csv"
TRAVEL_CONFIG = {"instruction_set": "travel"}
OFFER = (
    "Oltaflock sets up AI itinerary generators for travel agencies. Custom, client-ready "
    "itineraries and quotes go out in under 5 minutes instead of 1 to 2 days by hand, about "
    "12 hours a week back per agent. Travel clients message 4 to 5 agencies at once and whoever "
    "sends a solid quote first usually wins, while the rest are still building theirs a day later. "
    "Done-for-you build, you only start paying once it is live and saving time. About 10 days to "
    "set up. Exclusivity: once an agency in a city is onboarded we will not take a direct "
    "competitor there. CTA: offer a free 2-minute sample itinerary built for their agency."
)


def lead_from_row(r: dict) -> dict:
    """Mirror api/index.py _csv_row_to_lead for the fields the engine reads."""
    signals = {"source": "csv"}
    if r.get("FirstName"):
        signals["contact_first_name"] = r["FirstName"].strip()
    if r.get("Body"):
        signals["seed_body"] = r["Body"].strip()
        signals["seed_subject"] = (r.get("Subject") or "").strip()
        signals["seed_step"] = 1
    return {
        "business": r.get("Company"), "city": r.get("City"),
        "country": r.get("Country"), "email": (r.get("Email") or "").lower(),
        "website": None, "signals": signals,
    }


def render(draft: dict) -> str:
    """Exactly what send_email puts in the text/plain part."""
    return seq._md_to_text(draft["body"] + seq._signature())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", type=int, help="1-based row number (default: all)")
    ap.add_argument("--full", action="store_true", help="draft steps 2-7 too (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    if args.lead:
        rows = [rows[args.lead - 1]]

    print(f"FROM: {seq.RESEND_FROM}")
    print("SIGNATURE appended to every email:")
    print(seq._signature().strip())
    print("=" * 72)

    for i, r in enumerate(rows, 1):
        lead = lead_from_row(r)
        steps = range(1, seq.max_step(TRAVEL_CONFIG) + 1) if args.full else [1]
        for step in steps:
            stub = {"current_step": step - 1, "opens": 0, "clicks": 0}
            draft = seq.draft_one(lead, stub, step, OFFER, TRAVEL_CONFIG)
            tag = "SEEDED" if draft.get("model") == "seed" else f"drafted/{draft.get('angle')}"
            print(f"\n### {r.get('Company')}  <{lead['email']}>  step {step} [{tag}]")
            print(f"SUBJECT: {draft['subject']}")
            print("-" * 72)
            print(render(draft))
            print("-" * 72)


if __name__ == "__main__":
    main()
