from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
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
