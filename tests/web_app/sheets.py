import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
FIELDNAMES = [
    "Business Name", "City", "Address", "Phone",
    "Rating", "Reviews", "Business Type", "Google Maps URL",
]


def get_credentials_from_dict(cred_data):
    return Credentials(
        token=cred_data["token"],
        refresh_token=cred_data.get("refresh_token"),
        token_uri=cred_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=cred_data.get("client_id"),
        client_secret=cred_data.get("client_secret"),
        scopes=SCOPES,
    )


def create_sheet_and_write(cred_data, title, leads):
    """Create a new Google Sheet and write leads to it.

    Returns the spreadsheet URL.
    """
    creds = get_credentials_from_dict(cred_data)
    service = build("sheets", "v4", credentials=creds)

    spreadsheet = service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()

    spreadsheet_id = spreadsheet["spreadsheetId"]
    url = spreadsheet["spreadsheetUrl"]

    # Build rows: header + data
    rows = [FIELDNAMES]
    for lead in leads:
        rows.append([lead.get(f, "") for f in FIELDNAMES])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    # Auto-resize columns
    sheet_id = spreadsheet["sheets"][0]["properties"]["sheetId"]
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
                            "endIndex": len(FIELDNAMES),
                        }
                    }
                },
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {
                                    "red": 0.9, "green": 0.9, "blue": 0.9,
                                },
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                },
            ]
        },
    ).execute()

    return url
