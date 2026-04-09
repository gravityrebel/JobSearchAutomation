"""
Tool: notion.py

Purpose:
    Create and manage job entries in the Notion Applications database.
    Replaces sheets.py for job tracking — handles deduplication, entry
    creation, and resume URL updates.

Usage:
    # Get all job hashes already in the database (for deduplication):
    python tools/notion.py --action get_job_hashes

    # Create a new job entry (returns Notion page ID):
    python tools/notion.py --action create_entry \
        --job_title "Engineering Manager" \
        --company "Acme Corp" \
        --location "Washington, DC" \
        --salary "$150k" \
        --date_posted "2026-03-23" \
        --url "https://indeed.com/job/..." \
        --job_hash "abc123"

    # Write the tailored resume Drive URL to a job entry:
    python tools/notion.py --action update_resume_url \
        --page_id "abc123-..." \
        --resume_url "https://drive.google.com/..."

Parameters:
    --action        One of: get_job_hashes | create_entry | update_resume_url
    --job_title     Job title string
    --company       Company name string
    --location      Location string
    --salary        Salary string (may be blank)
    --date_posted   Date posted string
    --url           Job posting URL
    --job_hash      Stable job hash for deduplication
    --page_id       Notion page ID (returned by create_entry)
    --resume_url    Tailored resume Drive URL

Returns:
    get_job_hashes  → prints JSON array of hash strings
    create_entry    → prints Notion page ID string
    update_resume_url → prints "ok"

Exit codes:
    0 = success
    1 = error (details on stderr)
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Load .env manually (no external dotenv dependency assumed)
def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

try:
    from notion_client import Client
except ImportError:
    print("ERROR: notion_client not installed. Run: pip install notion-client", file=sys.stderr)
    sys.exit(1)


def _client():
    token = os.environ.get("NOTION_API_KEY")
    if not token:
        raise RuntimeError("NOTION_API_KEY not set in environment or .env")
    return Client(auth=token)


def _database_id():
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not db_id:
        raise RuntimeError("NOTION_DATABASE_ID not set in environment or .env")
    return db_id

# Data source ID for the Applications collection (stable, used for queries in v3 SDK)
DATA_SOURCE_ID = "ad43c0d3-017f-4d55-b3e7-9eb2f94c28b7"


def get_job_hashes() -> list:
    """
    Return all Job Hash values already stored in the Notion database.
    Used to filter out jobs already tracked.

    Returns:
        List of job hash strings (lowercased)
    """
    client = _client()

    hashes = []
    cursor = None

    while True:
        kwargs = {
            "data_source_id": DATA_SOURCE_ID,
            "filter": {
                "property": "Job Hash",
                "rich_text": {"is_not_empty": True},
            },
            "page_size": 100,
        }
        if cursor:
            kwargs["start_cursor"] = cursor

        response = client.data_sources.query(**kwargs)

        for page in response["results"]:
            prop = page["properties"].get("Job Hash", {})
            rich_text = prop.get("rich_text", [])
            if rich_text:
                hashes.append(rich_text[0]["plain_text"].lower())

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    return hashes


def create_entry(
    job_title: str,
    company: str,
    location: str,
    salary: str,
    date_posted: str,
    url: str,
    job_hash: str,
) -> str:
    """
    Create a new job entry in the Notion Applications database.

    Args:
        job_title:   Job title
        company:     Company name
        location:    Job location
        salary:      Salary string (may be empty)
        date_posted: Date posted string
        url:         Job posting URL
        job_hash:    Stable dedup hash

    Returns:
        Notion page ID string
    """
    client = _client()
    db_id = _database_id()

    properties = {
        "Opportunity": {
            "title": [{"text": {"content": company}}]
        },
        "Company / School": {
            "rich_text": [{"text": {"content": company}}]
        },
        "Role / Program": {
            "rich_text": [{"text": {"content": job_title}}]
        },
        "Location": {
            "rich_text": [{"text": {"content": location or ""}}]
        },
        "Link": {
            "url": url or None
        },
        "Type": {
            "select": {"name": "Job"}
        },
        "Status": {
            "status": {"name": "Interested"}
        },
        "Job Hash": {
            "rich_text": [{"text": {"content": job_hash}}]
        },
    }

    if salary:
        properties["Salary"] = {
            "rich_text": [{"text": {"content": salary}}]
        }

    if date_posted:
        properties["Date Posted"] = {
            "rich_text": [{"text": {"content": date_posted}}]
        }

    response = client.pages.create(
        parent={"database_id": db_id},
        properties=properties,
    )

    return response["id"]


def update_resume_url(page_id: str, resume_url: str) -> None:
    """
    Write the tailored resume Drive URL into the Resume field of a job entry.

    Args:
        page_id:    Notion page ID (returned by create_entry)
        resume_url: Google Drive URL for the tailored resume
    """
    client = _client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Resume": {"url": resume_url}
        },
    )


def update_match_score(page_id: str, score: int, reason: str) -> None:
    """
    Write a match score and one-line reason to a job entry.

    Args:
        page_id: Notion page ID
        score:   Integer 1–10
        reason:  One-line explanation of the score
    """
    client = _client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Score": {"number": score},
            "Match Reason": {"rich_text": [{"text": {"content": reason}}]},
        },
    )


def update_cover_letter_url(page_id: str, cover_letter_url: str) -> None:
    """
    Write the tailored cover letter Drive URL into the Cover Letter field of a job entry.

    Args:
        page_id:          Notion page ID (returned by create_entry)
        cover_letter_url: Google Drive URL for the cover letter
    """
    client = _client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Cover Letter": {"url": cover_letter_url}
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Manage Notion job tracker.")
    parser.add_argument("--action", required=True,
                        choices=["get_job_hashes", "create_entry", "update_resume_url", "update_match_score", "update_cover_letter_url"])
    parser.add_argument("--job_title", default=None)
    parser.add_argument("--company", default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--salary", default=None)
    parser.add_argument("--date_posted", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--job_hash", default=None)
    parser.add_argument("--page_id", default=None)
    parser.add_argument("--resume_url", default=None)
    parser.add_argument("--cover_letter_url", default=None)
    parser.add_argument("--match_score", type=int, default=None)
    parser.add_argument("--match_reason", default=None)
    args = parser.parse_args()

    try:
        if args.action == "get_job_hashes":
            hashes = get_job_hashes()
            print(json.dumps(hashes))

        elif args.action == "create_entry":
            for field in ("job_title", "company", "url", "job_hash"):
                if not getattr(args, field):
                    print(f"ERROR: --{field} is required for create_entry", file=sys.stderr)
                    sys.exit(1)
            page_id = create_entry(
                job_title=args.job_title,
                company=args.company,
                location=args.location or "",
                salary=args.salary or "",
                date_posted=args.date_posted or "",
                url=args.url,
                job_hash=args.job_hash,
            )
            print(page_id)

        elif args.action == "update_resume_url":
            if not args.page_id or not args.resume_url:
                print("ERROR: --page_id and --resume_url are required", file=sys.stderr)
                sys.exit(1)
            update_resume_url(args.page_id, args.resume_url)
            print("ok")

        elif args.action == "update_match_score":
            if not args.page_id or args.match_score is None or not args.match_reason:
                print("ERROR: --page_id, --match_score, and --match_reason are required", file=sys.stderr)
                sys.exit(1)
            update_match_score(args.page_id, args.match_score, args.match_reason)
            print("ok")

        elif args.action == "update_cover_letter_url":
            if not args.page_id or not args.cover_letter_url:
                print("ERROR: --page_id and --cover_letter_url are required", file=sys.stderr)
                sys.exit(1)
            update_cover_letter_url(args.page_id, args.cover_letter_url)
            print("ok")

        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
