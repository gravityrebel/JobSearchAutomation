# Workflow: Parse Inbox

## Objective
Scan the Gmail inbox for recruiter outreach received since the last search run, extract
job leads, and return them in the same normalized format used by the Indeed and Dice
search results. Output feeds directly into the deduplication and Notion tracking steps
in `search_jobs.md`.

## When This Runs
Called from `search_jobs.md` Step 2d **only when `INBOX_ENABLED=true`** in `.env`.
Gmail inbox parsing is opt-in and disabled by default. To enable it, set `INBOX_ENABLED=true`
in `.env` and ensure Google auth includes the `gmail.readonly` scope (see Prerequisites below).

To enable inbox parsing, tell Claude: "enable inbox search" or set `INBOX_ENABLED=true` in `.env` directly.

Can be tested manually by the user with:
```
python tools/parse_inbox.py --since_date "2026-03-01" --max_results 20
```

---

## Prerequisites

**Gmail scope:** `parse_inbox.py` requires `gmail.readonly` in addition to the scopes
used by other tools. If Google auth was set up before this workflow existed, the saved
token will be missing this scope and must be refreshed:

```
rm ~/.config/gws/token.json
python tools/google_auth.py
```

The browser will open once to approve the new scope. This is a one-time step.

---

## Step 1: Fetch Inbox Emails

Run:
```
python tools/parse_inbox.py \
  --since_date "<LAST_SEARCH_DATE>" \
  --max_results 50
```

- If `LAST_SEARCH_DATE` is empty (first run): omit `--since_date` or pass empty string.
  The tool returns up to `max_results` matching messages regardless of date.
- The tool searches for emails with recruiter-signal subject keywords:
  `opportunity`, `position`, `role`, `opening`, `job`, `hiring`, `recruiter`, etc.
- Emails from `noreply`, `no-reply`, `donotreply`, newsletter senders, and the user's
  own address are excluded automatically.
- Raw results are saved to `.tmp/inbox_raw_<timestamp>.json` for debugging.

Output is a JSON array. Each object:
```json
{
  "message_id": "18e3a1bc2d4f5a6b",
  "thread_id":  "18e3a1bc2d4f5a6b",
  "subject":    "Exciting opportunity at Acme Corp",
  "from_name":  "Jane Smith",
  "from_email": "jane.smith@recruitfirm.com",
  "date":       "2026-03-22",
  "body_text":  "Hi, I came across your profile and wanted to reach out...",
  "snippet":    "Hi, I came across your profile..."
}
```

**If the tool fails:** log the error and continue without inbox results. Inbox failure
must not block the Indeed/Dice results.

---

## Step 2: Filter and Classify Emails

For each email returned, determine whether it is a genuine job lead by reading the
`subject` and `body_text`. Discard emails that are:
- Marketing blasts with no specific role mentioned
- Job board notification digests (Indeed, LinkedIn alerts, etc.) — these are already
  captured by the direct search tools
- Automatic confirmations or status updates for applications already in Notion
- Spam or obvious false positives

Keep emails where:
- A specific job title, role, or position is described
- A company name can be identified
- There is enough information to create a Notion entry

---

## Step 3: Extract Job Fields

For each kept email, extract the following fields. Use best-effort extraction —
blank values are acceptable:

| Field         | Source                                      | Notes                                    |
|---------------|---------------------------------------------|------------------------------------------|
| `title`       | Body or subject line                        | Best-guess job title                     |
| `company`     | Body, signature, or sender domain           | Company being recruited for              |
| `location`    | Body text                                   | City/state, "Remote", or ""              |
| `salary`      | Body text                                   | May be blank — recruiter emails rarely include it |
| `date_posted` | `date` field from the email object          | ISO date from the email received date    |
| `url`         | Body text                                   | Job posting link if included, else ""    |
| `description` | Full `body_text`                            | Use the raw email body as the description |
| `job_hash`    | Computed (see below)                        | Stable dedup identifier                  |
| `source`      | `"inbox"`                                   | Always literal "inbox"                   |

**Computing `job_hash` for inbox leads:**

Use the `message_id` from the email object, prefixed with `inbox_`:
```
job_hash = "inbox_" + message_id
```

This is stable — Gmail message IDs never change for the same message.

---

## Step 4: Return Normalized Results

Produce a list of job dicts in the same format used by `search_indeed.py` output,
with two additional fields (`job_hash` and `source`). Example:

```json
[
  {
    "title":       "Senior Software Engineer",
    "company":     "Acme Corp",
    "location":    "Remote",
    "salary":      "",
    "date_posted": "2026-03-22",
    "url":         "https://acmecorp.com/careers/123",
    "description": "Hi Sarah, I'm reaching out because we have an opening...",
    "job_hash":    "inbox_18e3a1bc2d4f5a6b",
    "source":      "inbox"
  }
]
```

This list is merged into the combined results in `search_jobs.md` Step 2d, before
Notion deduplication runs. The `inbox_` prefix on `job_hash` prevents any collision
with Indeed or Dice hashes.

---

## Edge Cases

| Situation | Handling |
|---|---|
| Email has no job title | Skip — do not create a Notion entry with a blank title |
| Email has no company name | Skip — a lead without a company cannot be acted on |
| Email is a thread with multiple replies | Use only the top-level message; `message_id` is stable |
| Email body is empty | Use `snippet` as fallback `description` |
| URL in body is a redirect/tracker | Include it as-is — do not resolve |
| Recruiter email mentions multiple roles | Create one entry for the most prominent role only |
| Gmail API rate limit hit | Log the error, return whatever was fetched before the error |
| Token missing `gmail.readonly` scope | Log a clear error with re-auth instructions, continue without inbox results |
