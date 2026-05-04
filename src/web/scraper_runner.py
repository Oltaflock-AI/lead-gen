"""Subprocess wrappers for the canonical scrapers in src/scrapers/.

Each runner spawns `python -m src.scrapers.<name>` from PROJECT_ROOT and yields
stdout lines as SSE events. The scraper writes its CSV directly; we just stream
its log.
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCRAPERS = {
    "law_firms_us": {
        "module": "src.scrapers.law_firms_us",
        "label": "Law Firms — US",
        "output": PROJECT_ROOT / "data" / "outputs" / "law_firms_us.csv",
        "description": "150+ US cities, no-website filter, 50+ reviews, 4.0+ rating.",
    },
    "seasonal_us": {
        "module": "src.scrapers.seasonal_us",
        "label": "Seasonal Businesses — US",
        "output": PROJECT_ROOT / "data" / "outputs" / "seasonal_us.csv",
        "description": "Landscaping, HVAC, pool, roofing, summer/outdoor across ~50 US cities.",
    },
    "businesses_india": {
        "module": "src.scrapers.businesses_india",
        "label": "Mixed Businesses — India",
        "output": PROJECT_ROOT / "data" / "outputs" / "businesses_india.csv",
        "description": "Restaurants, salons, gyms, clinics, retail across 25 Indian cities.",
    },
}


def list_scrapers():
    return [
        {
            "key": k,
            "label": v["label"],
            "description": v["description"],
            "output": str(v["output"]),
            "exists": v["output"].exists(),
        }
        for k, v in SCRAPERS.items()
    ]


def run_scraper_stream(key):
    """Yield log lines (str, no trailing newline) from the scraper subprocess."""
    spec = SCRAPERS.get(key)
    if not spec:
        yield f"ERROR: unknown scraper '{key}'"
        return

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", spec["module"]],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
    )
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
        proc.wait()
        yield f"__DONE__ exit_code={proc.returncode}"
    finally:
        if proc.poll() is None:
            proc.terminate()
