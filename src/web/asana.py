"""Minimal Asana integration via Personal Access Token (PAT).

Uses the REST API directly — no SDK needed. Auth: a single PAT in .env.
Functions cover what the dashboard needs: list projects/users in a workspace,
and create a task with optional assignee + project.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://app.asana.com/api/1.0"
ASANA_PAT = os.getenv("ASANA_PAT", "")
ASANA_WORKSPACE_GID = os.getenv("ASANA_WORKSPACE_GID", "")


class AsanaError(Exception):
    pass


def _headers():
    if not ASANA_PAT:
        raise AsanaError("ASANA_PAT not set in .env")
    return {
        "Authorization": f"Bearer {ASANA_PAT}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def is_configured():
    return bool(ASANA_PAT)


def _get(path, params=None):
    r = requests.get(f"{API_BASE}{path}", headers=_headers(), params=params, timeout=15)
    if r.status_code >= 400:
        raise AsanaError(f"GET {path} → {r.status_code}: {r.text[:200]}")
    return r.json().get("data", [])


def _post(path, body):
    r = requests.post(f"{API_BASE}{path}", headers=_headers(), json=body, timeout=15)
    if r.status_code >= 400:
        raise AsanaError(f"POST {path} → {r.status_code}: {r.text[:200]}")
    return r.json().get("data", {})


def get_workspace():
    """Return the configured workspace, or the first one available."""
    if ASANA_WORKSPACE_GID:
        return {"gid": ASANA_WORKSPACE_GID}
    workspaces = _get("/workspaces")
    if not workspaces:
        raise AsanaError("No workspaces accessible to this PAT")
    return workspaces[0]


def list_projects(workspace_gid=None):
    gid = workspace_gid or get_workspace()["gid"]
    return _get(f"/workspaces/{gid}/projects", params={"limit": 100, "archived": "false"})


def list_users(workspace_gid=None):
    gid = workspace_gid or get_workspace()["gid"]
    return _get(f"/workspaces/{gid}/users", params={"limit": 100, "opt_fields": "name,email"})


def create_task(name, notes="", project_gid=None, assignee_gid=None, workspace_gid=None):
    """Create a task. If project_gid is set the workspace is derived from it.

    Returns the created task dict (includes 'gid' and 'permalink_url' if available).
    """
    body = {"data": {"name": name, "notes": notes}}
    if project_gid:
        body["data"]["projects"] = [project_gid]
    else:
        body["data"]["workspace"] = workspace_gid or get_workspace()["gid"]
    if assignee_gid:
        body["data"]["assignee"] = assignee_gid

    task = _post("/tasks", body)

    # Fetch permalink_url separately (not returned by default).
    if task.get("gid"):
        try:
            r = requests.get(
                f"{API_BASE}/tasks/{task['gid']}",
                headers=_headers(),
                params={"opt_fields": "permalink_url,name"},
                timeout=15,
            )
            if r.status_code < 400:
                task.update(r.json().get("data", {}))
        except requests.RequestException:
            pass
    return task
