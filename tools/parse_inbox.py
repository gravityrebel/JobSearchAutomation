"""
Tool: parse_inbox.py

Purpose:
    Fetch recruiter outreach emails from Gmail and return structured raw data
    for the agent to normalize into job objects. Follows the same pattern as
    search_indeed.py and search_dice.py — this tool handles mechanics, the
    agent handles interpretation.

Usage:
    python tools/parse_inbox.py \
        --since_date "2026-03-07" \
        --max_results 50

    python tools/parse_inbox.py \
        --since_date "2026-03-07" \
        --label "INBOX" \
        --max_results 100

Parameters:
    --since_date   ISO date string YYYY-MM-DD. Only return emails received on or
                   after this date. Omit to return all matching emails.
    --label        Gmail label to search (default: INBOX). Use "recruiter" if you
                   have a label set up for recruiter emails.
    --max_results  Maximum number of emails to return (default: 50, max: 200).

Returns:
    JSON array printed to stdout. Each object:
    {
        "message_id":  str,   # Gmail message ID (stable, for deduplication)
        "thread_id":   str,   # Gmail thread ID
        "subject":     str,
        "from_name":   str,
        "from_email":  str,
        "date":        str,   # ISO date string YYYY-MM-DD
        "body_text":   str,   # Plain-text body (truncated at 8000 chars)
        "snippet":     str    # Gmail snippet (first ~100 chars)
    }

Exit codes:
    0 = success (even if 0 results)
    1 = error (details on stderr)

Notes:
    - Requires gmail.readonly scope in addition to gmail.send.
    - If your token was created before this scope was added, delete
      ~/.config/gws/token.json and run `python tools/google_auth.py` to re-auth.
    - The search query targets common recruiter/opportunity signals. It intentionally
      casts a wide net — the agent filters down to genuine job leads.
    - Raw results are saved to .tmp/inbox_raw_<timestamp>.json for debugging.
    - Emails from yourself (USER_EMAIL) are excluded automatically.
"""

import argparse
import base64
import email as email_lib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Gmail search query
# ---------------------------------------------------------------------------

# Terms that commonly appear in recruiter outreach subject lines.
# Cast wide — the agent will filter false positives.
RECRUITER_SUBJECT_TERMS = [
    "opportunity",
    "position",
    "role",
    "opening",
    "job",
    "hiring",
    "recruiter",
    "career",
    "offer",
    "interview",
    "connect",
    "talent",
    "candidate",
]


def _build_query(since_date_str: str, label: str) -> str:
    """
    Build a Gmail search query string.

    Args:
        since_date_str: ISO date YYYY-MM-DD, or empty string
        label:          Gmail label name (e.g. "INBOX", "recruiter")

    Returns:
        Gmail query string
    """
    parts = []

    # Label filter
    if label and label.upper() != "INBOX":
        parts.append(f"label:{label}")
    else:
        parts.append("in:inbox")

    # Date filter
    if since_date_str and since_date_str.strip():
        try:
            since = datetime.strptime(since_date_str.strip(), "%Y-%m-%d")
            # Gmail uses after:YYYY/MM/DD format
            parts.append(f"after:{since.strftime('%Y/%m/%d')}")
        except ValueError:
            pass  # Skip date filter if malformed

    # Exclude automated/no-reply senders
    parts.append("-from:noreply")
    parts.append("-from:no-reply")
    parts.append("-from:donotreply")
    parts.append("-from:notifications")
    parts.append("-from:mailer-daemon")

    # Exclude common automated email types
    parts.append("-subject:unsubscribe")
    parts.append("-subject:newsletter")
    parts.append("-subject:receipt")
    parts.append("-subject:invoice")

    # Subject terms — at least one must match
    subject_clause = " OR ".join(f'subject:"{t}"' for t in RECRUITER_SUBJECT_TERMS)
    parts.append(f"({subject_clause})")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Email parsing helpers
# ---------------------------------------------------------------------------

def _decode_header_value(value: str) -> str:
    """Decode RFC 2047 encoded header values (e.g. =?utf-8?b?...?=)."""
    if not value:
        return ""
    decoded_parts = email_lib.header.decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _parse_from(from_header: str) -> tuple[str, str]:
    """
    Split a From: header into (display_name, email_address).

    Args:
        from_header: Raw From header string, e.g. "Jane Smith <jane@example.com>"

    Returns:
        Tuple of (name, email)
    """
    from_header = _decode_header_value(from_header)
    match = re.match(r"^(.*?)\s*<([^>]+)>", from_header)
    if match:
        name = match.group(1).strip().strip('"')
        addr = match.group(2).strip()
        return name, addr
    # No angle brackets — the whole thing might just be an email
    addr = from_header.strip()
    return "", addr


def _extract_body(payload: dict) -> str:
    """
    Recursively extract plain-text body from a Gmail message payload.

    Args:
        payload: Gmail API message payload dict

    Returns:
        Plain text body string (may be empty)
    """
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        try:
            return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    # Recurse into multipart
    parts = payload.get("parts", [])
    for part in parts:
        text = _extract_body(part)
        if text:
            return text

    # Fallback: try HTML part and strip tags
    for part in parts:
        if part.get("mimeType") == "text/html":
            html_data = part.get("body", {}).get("data", "")
            if html_data:
                try:
                    html = base64.urlsafe_b64decode(html_data + "==").decode("utf-8", errors="replace")
                    # Basic HTML tag stripping
                    text = re.sub(r"<[^>]+>", " ", html)
                    text = re.sub(r"&nbsp;", " ", text)
                    text = re.sub(r"&amp;", "&", text)
                    text = re.sub(r"&lt;", "<", text)
                    text = re.sub(r"&gt;", ">", text)
                    text = re.sub(r"\s{3,}", "\n\n", text)
                    return text.strip()
                except Exception:
                    pass

    return ""


def _parse_date(date_str: str) -> str:
    """
    Parse a Gmail internal date (Unix ms timestamp or RFC 2822 string) to ISO date.

    Args:
        date_str: Date string from Gmail header

    Returns:
        ISO date string YYYY-MM-DD, or empty string if unparseable
    """
    if not date_str:
        return ""
    # Try RFC 2822 format (from email header)
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _internal_date_to_iso(internal_date_ms: str) -> str:
    """Convert Gmail internalDate (Unix ms) to ISO date string."""
    try:
        ts = int(internal_date_ms) / 1000
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def fetch_recruiter_emails(since_date_str: str, label: str, max_results: int) -> list:
    """
    Fetch recruiter-style emails from Gmail.

    Args:
        since_date_str: ISO date YYYY-MM-DD, or empty string
        label:          Gmail label name
        max_results:    Maximum number of messages to fetch

    Returns:
        List of email dicts
    """
    # Import here so import errors surface cleanly
    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: google-api-python-client not installed. Run: pip install google-api-python-client", file=sys.stderr)
        sys.exit(1)

    # Import auth helper
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from tools.google_auth import get_credentials
    except ImportError:
        from google_auth import get_credentials

    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    user_email = os.environ.get("USER_EMAIL", "").lower()

    query = _build_query(since_date_str, label)
    print(f"# Gmail query: {query}", file=sys.stderr)

    # List matching messages
    list_kwargs = {
        "userId": "me",
        "q": query,
        "maxResults": min(max_results, 200),
    }
    response = service.users().messages().list(**list_kwargs).execute()
    messages = response.get("messages", [])

    print(f"# Found {len(messages)} matching messages", file=sys.stderr)

    results = []
    for msg_ref in messages:
        msg_id = msg_ref["id"]
        try:
            msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full",
            ).execute()
        except Exception as e:
            print(f"# Warning: could not fetch message {msg_id}: {e}", file=sys.stderr)
            continue

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        from_raw = headers.get("From", "")
        from_name, from_email = _parse_from(from_raw)

        # Skip emails from yourself
        if user_email and from_email.lower() == user_email:
            continue

        subject = _decode_header_value(headers.get("Subject", "(no subject)"))
        date_header = headers.get("Date", "")
        msg_date = _parse_date(date_header) or _internal_date_to_iso(msg.get("internalDate", ""))

        body = _extract_body(msg.get("payload", {}))
        body_truncated = body[:8000] if len(body) > 8000 else body

        results.append({
            "message_id": msg_id,
            "thread_id": msg.get("threadId", ""),
            "subject": subject,
            "from_name": from_name,
            "from_email": from_email,
            "date": msg_date,
            "body_text": body_truncated,
            "snippet": msg.get("snippet", ""),
        })

    return results


def save_to_tmp(results: list, suffix: str = "") -> str:
    """Save results to .tmp/ as a timestamped JSON backup."""
    os.makedirs(".tmp", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f".tmp/inbox_raw_{timestamp}{suffix}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch recruiter emails from Gmail.")
    parser.add_argument("--since_date", default="",
                        help="ISO date YYYY-MM-DD — only return emails on or after this date.")
    parser.add_argument("--label", default="INBOX",
                        help="Gmail label to search (default: INBOX).")
    parser.add_argument("--max_results", type=int, default=50,
                        help="Max emails to return (default: 50, max: 200).")
    args = parser.parse_args()

    try:
        results = fetch_recruiter_emails(
            since_date_str=args.since_date,
            label=args.label,
            max_results=args.max_results,
        )

        path = save_to_tmp(results)
        print(f"# Raw inbox results saved to {path}", file=sys.stderr)
        print(f"# Returning {len(results)} emails", file=sys.stderr)

        print(json.dumps(results, indent=2, ensure_ascii=False))
        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
