"""
Tool: search_dice.py

Purpose:
    Parameter parsing, result normalization, date filtering, and .tmp/ backup
    for Dice job searches. The agent calls Dice MCP tools directly and
    passes results here for processing.

Usage:
    python tools/search_dice.py \
        --titles "Engineering Manager, Director of Engineering" \
        --location "Washington, DC" \
        --salary_min 175000 \
        --keywords "include:python,agile exclude:unpaid,internship" \
        --since_date "2026-03-07" \
        --results_file ".tmp/dice_raw.json"

Parameters:
    --titles        Comma-separated list of job titles to search
    --location      City/state string or "Remote"
    --salary_min    Integer minimum salary (0 = no filter)
    --keywords      Raw keyword string from .env (format: "include:a,b exclude:c,d")
    --since_date    ISO date string YYYY-MM-DD. Only return jobs posted on or after
                    this date. Omit (or pass empty string) to return all results.
    --results_file  Path to JSON file containing raw results from the agent.
                    When provided, reads from file instead of stdin.

Returns:
    JSON array printed to stdout. Each object:
    {
        "job_id":      str,   # Dice job ID, prefixed with "dice_"
        "job_hash":    str,   # Same as job_id (Dice IDs are already stable)
        "title":       str,
        "company":     str,
        "location":    str,
        "salary":      str,   # May be empty string if not listed
        "date_posted": str,   # As returned by Dice
        "url":         str,   # Direct link to job posting
        "description": str,   # Full job description text
        "source":      str    # Always "Dice"
    }

Exit codes:
    0 = success (even if 0 results after filtering)
    1 = error (details printed to stderr)

Notes:
    - The agent calls mcp__claude_ai_Dice__search_jobs directly.
      This script handles everything after that call: normalization, filtering, backup.
    - job_hash is prefixed with "dice_" to prevent collisions with Indeed hashes.
    - Date filtering and fail-open behavior mirrors search_indeed.py exactly.
    - Results are written to .tmp/dice_results_<timestamp>.json before filtering,
      and .tmp/dice_results_<timestamp>_filtered.json after, for debugging.
"""

import argparse
import json
import re
import sys
import os
from datetime import date, datetime, timedelta


# ---------------------------------------------------------------------------
# Keyword parsing (identical to search_indeed.py)
# ---------------------------------------------------------------------------

def parse_keywords(raw: str) -> dict:
    result = {"include": [], "exclude": []}
    if not raw:
        return result
    for part in raw.strip().split():
        if part.startswith("include:"):
            result["include"] = [k.strip() for k in part[8:].split(",") if k.strip()]
        elif part.startswith("exclude:"):
            result["exclude"] = [k.strip() for k in part[8:].split(",") if k.strip()]
    return result


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def parse_date_posted(date_str: str) -> date | None:
    """
    Parse a date_posted string from Dice into a datetime.date.

    Dice returns posting dates in several formats:
        "Just posted"     → today
        "Today"           → today
        "1 day ago"       → yesterday
        "3 days ago"      → 3 days before today
        "30+ days ago"    → 30 days before today
        "2026-03-07"      → ISO date
        "March 7, 2026"   → long-form

    Returns None if unparseable — callers must treat None as "include this job".
    """
    if not date_str:
        return None

    today = date.today()
    s = date_str.strip().lower()

    if s in ("just posted", "today", "active today", "posted today"):
        return today

    if s == "yesterday":
        return today - timedelta(days=1)

    match = re.match(r"(\d+)\+?\s+days?\s+ago", s)
    if match:
        return today - timedelta(days=int(match.group(1)))

    match = re.match(r"(\d+)\+?\s+day\s+ago", s)
    if match:
        return today - timedelta(days=int(match.group(1)))

    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        pass

    try:
        return datetime.strptime(date_str.strip(), "%B %d, %Y").date()
    except ValueError:
        pass

    try:
        return datetime.strptime(date_str.strip(), "%b %d, %Y").date()
    except ValueError:
        pass

    return None


# ---------------------------------------------------------------------------
# Job hash
# ---------------------------------------------------------------------------

def make_job_hash(job_id: str) -> str:
    """
    Return a stable unique hash for a Dice job.

    Dice job IDs are already stable unique identifiers (GUIDs or slugs).
    Prefix with "dice_" to prevent collisions with Indeed hashes in the sheet.
    """
    return "dice_" + re.sub(r"[^a-z0-9_-]", "", job_id.strip().lower())


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize(raw_jobs: list) -> list:
    """
    Normalize raw Dice MCP results into the standard job dict format.

    Dice MCP may return field names that differ slightly from Indeed.
    This function maps all known variants to the canonical field names.
    """
    normalized = []
    for job in raw_jobs:
        # Dice MCP field name variants
        job_id = (
            job.get("id") or
            job.get("jobId") or
            job.get("job_id") or
            job.get("externalId") or
            ""
        )
        url = (
            job.get("url") or
            job.get("applyUrl") or
            job.get("apply_url") or
            job.get("jobDetailsUrl") or
            ""
        )
        salary = (
            job.get("salary") or
            job.get("salaryRange") or
            job.get("salary_range") or
            job.get("compensation") or
            ""
        )
        date_posted = (
            job.get("date_posted") or
            job.get("datePosted") or
            job.get("postedDate") or
            job.get("posted_date") or
            job.get("publishedDate") or
            ""
        )
        description = (
            job.get("description") or
            job.get("jobDescription") or
            job.get("job_description") or
            ""
        )

        hash_val = make_job_hash(job_id) if job_id else make_job_hash(url)

        normalized.append({
            "job_id":      "dice_" + job_id if job_id else hash_val,
            "job_hash":    hash_val,
            "title":       job.get("title") or job.get("jobTitle") or "",
            "company":     job.get("company") or job.get("companyName") or job.get("employer") or "",
            "location":    job.get("location") or job.get("jobLocation") or "",
            "salary":      str(salary),
            "date_posted": str(date_posted),
            "url":         url,
            "description": description,
            "source":      "Dice",
        })
    return normalized


# ---------------------------------------------------------------------------
# Date filtering (identical logic to search_indeed.py)
# ---------------------------------------------------------------------------

def filter_by_date(results: list, since_date_str: str) -> tuple[list, int]:
    if not since_date_str or not since_date_str.strip():
        return results, 0

    try:
        since = datetime.strptime(since_date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        return results, 0

    kept = []
    skipped = 0
    for job in results:
        posted = parse_date_posted(job.get("date_posted", ""))
        if posted is None:
            kept.append(job)
        elif posted >= since:
            kept.append(job)
        else:
            skipped += 1

    return kept, skipped


# ---------------------------------------------------------------------------
# .tmp/ backup
# ---------------------------------------------------------------------------

def save_to_tmp(results: list, suffix: str = "") -> str:
    os.makedirs(".tmp", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f".tmp/dice_results_{timestamp}{suffix}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Filter and save Dice search results.")
    parser.add_argument("--titles", required=True, help="Comma-separated job titles")
    parser.add_argument("--location", required=True, help="Location or 'Remote'")
    parser.add_argument("--salary_min", type=int, default=0, help="Minimum salary")
    parser.add_argument("--keywords", default="", help="include:/exclude: keyword string")
    parser.add_argument("--since_date", default="",
                        help="ISO date YYYY-MM-DD — only return jobs posted on or after this date.")
    parser.add_argument("--results_file", default="",
                        help="Path to a JSON file containing raw results from the agent.")
    args = parser.parse_args()

    try:
        if args.results_file:
            with open(args.results_file, encoding="utf-8") as f:
                raw_results = json.load(f)
        else:
            raw_results = json.load(sys.stdin)

        normalized = normalize(raw_results)
        save_to_tmp(normalized)

        filtered, skipped = filter_by_date(normalized, args.since_date)
        save_to_tmp(filtered, suffix="_filtered")

        print(
            f"# Dice results: {len(normalized)} total, {skipped} skipped (already seen), "
            f"{len(filtered)} new",
            file=sys.stderr
        )

        print(json.dumps(filtered, indent=2, ensure_ascii=False))
        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
