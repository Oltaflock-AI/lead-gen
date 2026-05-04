from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    # Gmail is read-only — used purely to detect replies on cold outreach
    # we sent through Resend. Sending happens through Resend, not Gmail.
    "https://www.googleapis.com/auth/gmail.readonly",
]


def get_credentials_from_dict(cred_data, scopes=None):
    return Credentials(
        token=cred_data["token"],
        refresh_token=cred_data.get("refresh_token"),
        token_uri=cred_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=cred_data.get("client_id"),
        client_secret=cred_data.get("client_secret"),
        scopes=scopes or SCOPES,
    )


def _format_header(service, spreadsheet_id, sheet_id, n_cols):
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": n_cols,
                        }
                    }
                },
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                },
            ]
        },
    ).execute()


def create_sheet_and_write(cred_data, title, leads, fieldnames=None):
    """Create a new spreadsheet, write leads, return (sheet_id, url)."""
    if not leads:
        raise ValueError("No leads to export")
    if fieldnames is None:
        fieldnames = list(leads[0].keys())

    creds = get_credentials_from_dict(cred_data)
    service = build("sheets", "v4", credentials=creds)

    spreadsheet = service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()

    spreadsheet_id = spreadsheet["spreadsheetId"]
    url = spreadsheet["spreadsheetUrl"]
    sheet_id = spreadsheet["sheets"][0]["properties"]["sheetId"]

    rows = [list(fieldnames)]
    for lead in leads:
        rows.append([str(lead.get(f, "")) for f in fieldnames])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    _format_header(service, spreadsheet_id, sheet_id, len(fieldnames))
    return spreadsheet_id, url


def ensure_master_spreadsheet(cred_data, title="Lead Gen — Master", existing_id=None):
    """Return (spreadsheet_id, url). Reuses `existing_id` if it still exists,
    otherwise creates a fresh master spreadsheet.
    """
    creds = get_credentials_from_dict(cred_data)
    service = build("sheets", "v4", credentials=creds)

    if existing_id:
        try:
            meta = service.spreadsheets().get(spreadsheetId=existing_id).execute()
            return existing_id, meta.get("spreadsheetUrl", "")
        except Exception:
            pass  # deleted upstream; fall through and create fresh

    spreadsheet = service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()
    return spreadsheet["spreadsheetId"], spreadsheet["spreadsheetUrl"]


def _existing_tab_titles(service, spreadsheet_id):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def _unique_tab_title(existing, base):
    if base not in existing:
        return base
    i = 2
    while f"{base} ({i})" in existing:
        i += 1
    return f"{base} ({i})"


def add_tab(cred_data, spreadsheet_id, tab_name, leads, fieldnames=None):
    """Add a new tab to the master spreadsheet and write `leads` to it.

    Returns dict {tab_title, tab_gid, url} where url deep-links to the new tab.
    Falls back to suffixing the tab name with "(2)" / "(3)" if the requested
    name is already taken.
    """
    if not leads:
        raise ValueError("No leads to write")
    if fieldnames is None:
        fieldnames = list(leads[0].keys())

    creds = get_credentials_from_dict(cred_data)
    service = build("sheets", "v4", credentials=creds)

    existing = _existing_tab_titles(service, spreadsheet_id)
    title = _unique_tab_title(existing, tab_name)

    add_resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()
    sheet_id = add_resp["replies"][0]["addSheet"]["properties"]["sheetId"]

    rows = [list(fieldnames)]
    for lead in leads:
        rows.append([str(lead.get(f, "")) for f in fieldnames])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    _format_header(service, spreadsheet_id, sheet_id, len(fieldnames))

    spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={sheet_id}"
    return {"tab_title": title, "tab_gid": sheet_id, "url": spreadsheet_url}


def update_tab(cred_data, spreadsheet_id, tab_title, leads, fieldnames=None):
    """Overwrite a named tab inside an existing spreadsheet.

    If the tab title doesn't exist, falls back to creating a new tab. Returns
    {tab_title, tab_gid, url}.
    """
    if not leads:
        raise ValueError("No leads to write")
    if fieldnames is None:
        fieldnames = list(leads[0].keys())

    creds = get_credentials_from_dict(cred_data)
    service = build("sheets", "v4", credentials=creds)

    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets_meta = meta.get("sheets", [])
    target = next((s for s in sheets_meta if s["properties"]["title"] == tab_title), None)

    if target is None:
        return add_tab(cred_data, spreadsheet_id, tab_title, leads, fieldnames)

    sheet_id = target["properties"]["sheetId"]
    title = target["properties"]["title"]

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1:ZZ",
        body={},
    ).execute()

    rows = [list(fieldnames)]
    for lead in leads:
        rows.append([str(lead.get(f, "")) for f in fieldnames])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    _format_header(service, spreadsheet_id, sheet_id, len(fieldnames))

    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={sheet_id}"
    return {"tab_title": title, "tab_gid": sheet_id, "url": url}


def update_sheet_values(cred_data, spreadsheet_id, leads, fieldnames=None):
    """Clear sheet and write fresh data. Returns the spreadsheet URL."""
    if not leads:
        raise ValueError("No leads to write")
    if fieldnames is None:
        fieldnames = list(leads[0].keys())

    creds = get_credentials_from_dict(cred_data)
    service = build("sheets", "v4", credentials=creds)

    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    url = meta.get("spreadsheetUrl", "")
    first_sheet = meta["sheets"][0]
    sheet_title = first_sheet["properties"]["title"]
    sheet_id = first_sheet["properties"]["sheetId"]

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_title}!A1:ZZ",
        body={},
    ).execute()

    rows = [list(fieldnames)]
    for lead in leads:
        rows.append([str(lead.get(f, "")) for f in fieldnames])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_title}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    _format_header(service, spreadsheet_id, sheet_id, len(fieldnames))
    return url
