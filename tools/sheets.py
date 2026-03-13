"""
Tool: sheets.py

Purpose:
    Create and manage the Job Tracker Google Sheet.
    Handles sheet creation, row appending, URL deduplication, and notes updates.

Usage:
    # Create the sheet (returns Sheet ID):
    python tools/sheets.py --action create

    # Create the sheet inside a specific Drive folder:
    python tools/sheets.py --action create --folder_id "1abc..."

    # Get all URLs already in the sheet (for deduplication):
    python tools/sheets.py --action get_urls --sheet_id SHEET_ID

    # Append a new job row (returns row number):
    python tools/sheets.py --action append_row --sheet_id SHEET_ID \
        --row '["2026-03-08","Software Engineer","Acme","Austin TX","$120k","2026-03-05","https://...","New",""]'

    # Write a URL into the Notes column for a specific row:
    python tools/sheets.py --action update_notes --sheet_id SHEET_ID \
        --row_num 5 --notes "https://drive.google.com/..."

Parameters:
    --action      One of: create | get_urls | append_row | update_notes
    --sheet_id    Google Sheet ID (stored as GOOGLE_SHEET_ID in .env)
    --folder_id   Optional Drive folder ID for create action (stored as DRIVE_FOLDER_ID in .env)
    --row         JSON array of cell values for append_row
    --row_num     Integer row number for update_notes (1-indexed)
    --notes       String to write into the Notes column

Returns:
    create        → prints Sheet ID to stdout
    get_urls      → prints JSON array of URL strings
    append_row    → prints the new row number (int)
    update_notes  → prints "ok"

Sheet schema (column order is fixed — do not change):
    A: Date Found
    B: Job Title
    C: Company
    D: Location
    E: Salary
    F: Date Posted
    G: URL          ← used for deduplication
    H: Status       ← default "New"
    I: Notes        ← tailored resume URL written here

Exit codes:
    0 = success
    1 = error (details on stderr)
"""

import argparse
import json
import sys
from pathlib import Path

from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.google_auth import get_credentials

SHEET_NAME = "Jobs"
HEADERS = [
    "Date Found", "Job Title", "Company", "Location", "Salary",
    "Date Posted", "URL", "Status", "Notes", "Job ID",
]


def extract_job_hash(job_id: str) -> str:
    """
    Extract the stable unique job hash from an Indeed job ID.

    Indeed job IDs follow the pattern:
        5-cmh1-0-{session_id}-{job_hash}[---{tracking}]

    The job_hash (5th segment) is consistent across searches for the same
    posting even when session_id and short URLs differ.

    Args:
        job_id: Full Indeed job ID string

    Returns:
        Lowercase hex job hash string, or the full job_id if parsing fails
    """
    parts = job_id.split("-")
    if len(parts) >= 5:
        return parts[4].split("---")[0].lower()
    return job_id.lower()


def _sheets_service():
    return build("sheets", "v4", credentials=get_credentials())


def _drive_service():
    return build("drive", "v3", credentials=get_credentials())


def create_sheet(title: str = "Job Tracker", folder_id: str = None) -> str:
    """
    Create a new Google Sheet named 'Job Tracker' with the correct headers.
    If folder_id is provided, moves the sheet into that Drive folder.

    Args:
        title:     Spreadsheet title (default: "Job Tracker")
        folder_id: Optional Drive folder ID to place the sheet in

    Returns:
        Sheet ID string
    """
    svc = _sheets_service()

    spreadsheet = svc.spreadsheets().create(
        body={
            "properties": {"title": title},
            "sheets": [{"properties": {"title": SHEET_NAME}}],
        }
    ).execute()

    sheet_id = spreadsheet["spreadsheetId"]

    # Write header row
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()

    # Move into project folder if specified
    if folder_id:
        drive_svc = _drive_service()
        # Get current parents so we can remove them (typically "root")
        file_meta = drive_svc.files().get(
            fileId=sheet_id, fields="parents"
        ).execute()
        current_parents = ",".join(file_meta.get("parents", []))
        drive_svc.files().update(
            fileId=sheet_id,
            addParents=folder_id,
            removeParents=current_parents,
            fields="id,parents",
        ).execute()

    return sheet_id


def get_existing_urls(sheet_id: str) -> list:
    """
    Return all URLs in column G (skipping the header row).
    Used to filter out jobs already in the sheet.

    Args:
        sheet_id: Google Sheet ID

    Returns:
        List of URL strings (may be empty)
    """
    svc = _sheets_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!G2:G",
    ).execute()
    values = result.get("values", [])
    return [row[0] for row in values if row]


def get_existing_job_hashes(sheet_id: str) -> set:
    """
    Return the set of job hashes already stored in column J.
    Used for definitive deduplication — job hashes are stable across searches
    even when Indeed generates different short URLs for the same posting.

    Args:
        sheet_id: Google Sheet ID

    Returns:
        Set of job hash strings (may be empty)
    """
    svc = _sheets_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!J2:J",
    ).execute()
    values = result.get("values", [])
    return {row[0].lower() for row in values if row and row[0]}


def append_row(sheet_id: str, row: list) -> int:
    """
    Append a job row to the sheet and return the row number.

    Args:
        sheet_id: Google Sheet ID
        row: List of 10 values matching the sheet column order:
             [date_found, title, company, location, salary,
              date_posted, url, status, notes, job_id_hash]

    Returns:
        1-indexed integer row number of the newly appended row
    """
    svc = _sheets_service()
    result = svc.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    updated_range = result["updates"]["updatedRange"]
    # Parse row number from range like "Jobs!A5:J5"
    row_num = int(updated_range.split("!")[1].split(":")[0][1:])
    return row_num


def update_notes(sheet_id: str, row_num: int, notes: str) -> None:
    """
    Write a value into the Notes column (column I) for a specific row.

    Args:
        sheet_id: Google Sheet ID
        row_num:  1-indexed row number to update
        notes:    String to write (typically a Google Drive or Doc URL)
    """
    svc = _sheets_service()
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!I{row_num}",
        valueInputOption="RAW",
        body={"values": [[notes]]},
    ).execute()


def main():
    parser = argparse.ArgumentParser(description="Manage the Job Tracker Google Sheet.")
    parser.add_argument("--action", required=True,
                        choices=["create", "get_urls", "get_job_hashes", "append_row", "update_notes"])
    parser.add_argument("--sheet_id", default=None)
    parser.add_argument("--folder_id", default=None,
                        help="Drive folder ID to place the sheet in (for create action)")
    parser.add_argument("--row", default=None, help="JSON array for append_row")
    parser.add_argument("--row_num", type=int, default=None)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    try:
        if args.action == "create":
            sheet_id = create_sheet(folder_id=args.folder_id)
            print(sheet_id)

        elif args.action == "get_urls":
            urls = get_existing_urls(args.sheet_id)
            print(json.dumps(urls))

        elif args.action == "get_job_hashes":
            hashes = get_existing_job_hashes(args.sheet_id)
            print(json.dumps(list(hashes)))

        elif args.action == "append_row":
            row_data = json.loads(args.row)
            row_num = append_row(args.sheet_id, row_data)
            print(row_num)

        elif args.action == "update_notes":
            update_notes(args.sheet_id, args.row_num, args.notes or "")
            print("ok")

        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
