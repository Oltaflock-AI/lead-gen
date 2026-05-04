"""Subprocess wrapper for src/processors/enrich_leads.py."""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENRICH_SCRIPT = PROJECT_ROOT / "src" / "processors" / "enrich_leads.py"


def run_enrich_stream(csv_path, keep_with_website=False, skip_email=False):
    """Yield log lines from `python3 src/processors/enrich_leads.py <csv>`."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        yield f"ERROR: file not found: {csv_path}"
        return

    cmd = [sys.executable, "-u", str(ENRICH_SCRIPT), str(csv_path)]
    if keep_with_website:
        cmd.append("--keep-with-website")
    if skip_email:
        cmd.append("--skip-email")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
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
