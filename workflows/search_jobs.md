# Workflow: Search Jobs

## Objective
Silently search Indeed and Dice for new jobs matching stored criteria, append only new results to the master Google Sheet, and trigger resume tailoring for each new job.

## When This Runs
- On the registered schedule (set during onboarding).
- Can also be triggered manually by running `run.md`.
- **Never asks the user anything during execution.**

---

## Step 1: Load Configuration

Read `.env` and extract:
- `JOB_TITLES` - split by comma into a list
- `LOCATION`
- `SALARY_MIN`
- `KEYWORDS`
- `GOOGLE_SHEET_ID`
- `RESUME_DRIVE_URL`
- `LAST_SEARCH_DATE` - ISO date string `YYYY-MM-DD` (may be empty on first run - that is expected)

If any required field other than `LAST_SEARCH_DATE` is missing, log an error and exit. Do not ask the user - the schedule should not block on input.

---

## Step 2: Search Indeed and Dice

Indeed is accessed via the MCP connector authenticated in the Claude web UI (`claude.ai`).
The agent calls `mcp__claude_ai_Indeed__search_jobs` and `mcp__claude_ai_Indeed__get_job_details` directly.
`tools/search_indeed.py` handles date filtering, result normalization, and `.tmp/` backup.

**2a. Call Indeed MCP** for each title in `JOB_TITLES`. Collect all raw results into a single list.

Each result object must have:
- `job_id` - full Indeed job ID string (for example `5-cmh1-0-{session}-{hash}`)
- `job_hash` - stable unique identifier extracted from `job_id` using `extract_job_hash()` in `tools/sheets.py`
- `title` - job title
- `company` - company name
- `location` - job location
- `salary` - salary string (may be blank)
- `date_posted` - posting date as returned by Indeed
- `url` - direct link to job posting
- `description` - full job description text

**2b. Write Indeed raw results to a temp file**, then run the date filter:

```text
python tools/search_indeed.py \
  --titles "<JOB_TITLES>" \
  --location "<LOCATION>" \
  --salary_min <SALARY_MIN> \
  --keywords "<KEYWORDS>" \
  --since_date "<LAST_SEARCH_DATE>" \
  --results_file ".tmp/indeed_raw.json"
```

- If `LAST_SEARCH_DATE` is empty (first run): pass an empty string - the tool returns all results.
- If `LAST_SEARCH_DATE` is set: the tool filters to only jobs posted on or after that date.
  Jobs with unparseable dates are always included (fail open - never silently drop a real job).
- The tool prints a summary to stderr: `X total, Y skipped (already seen), Z new`
- The tool outputs the filtered JSON list to stdout.

**2c. Search Dice** using `mcp__claude_ai_Dice__search_jobs` for each title in `JOB_TITLES`.
Collect all raw Dice results into a single list and write to `.tmp/dice_raw.json`.

Then run the date filter:

```text
python tools/search_dice.py \
  --titles "<JOB_TITLES>" \
  --location "<LOCATION>" \
  --salary_min <SALARY_MIN> \
  --keywords "<KEYWORDS>" \
  --since_date "<LAST_SEARCH_DATE>" \
  --results_file ".tmp/dice_raw.json"
```

- Same date-filter rules as Indeed apply.
- The tool normalizes Dice field names and prefixes all `job_hash` values with `dice_` to prevent
  collisions with Indeed hashes.
- If the Dice MCP call fails or returns an error, log it and continue with Indeed results only —
  a Dice failure must not block the whole run.

**2d. Merge results**: combine the filtered Indeed list and filtered Dice list into one list.

**Edge cases:**
- If the combined list is empty, log `"No new jobs found this run."` and stop.
- If either tool errors, log the full error. Only stop if both sources fail.

---

## Step 3: Deduplicate Against Existing Sheet

Run `tools/sheets.py --action get_job_hashes --sheet_id <GOOGLE_SHEET_ID>`.

The tool returns the set of job hashes already stored in the sheet (column J).

Filter the results from Step 2: keep only jobs whose `job_hash` is NOT already in that set.

**Why hashes, not URLs:** Indeed generates different short URLs for the same job across different search queries. The job hash (extracted from the job ID's 5th segment) is stable and definitive.

- If 0 new jobs remain after filtering, log `"No new jobs this run."` and stop.

---

## Step 4: For Each New Job

For each new job in the filtered list:

**4a. Append to Sheet**

Run `tools/sheets.py --action append_row` with:
- `sheet_id` = `GOOGLE_SHEET_ID`
- `row` = `[today's date, title, company, location, salary, date_posted, url, "New", "", job_hash]`

Column order in sheet: `Date Found | Job Title | Company | Location | Salary | Date Posted | URL | Status | Notes | Job ID`

The tool returns the row number of the newly added row.

**4b. Tailor Resume**

Call `workflows/tailor_resume.md` with:
- `job_url` = the job's URL
- `job_description` = the job's description text
- `company` = company name
- `job_title` = job title
- `sheet_row` = the row number returned in 4a

Wait for `tailor_resume` to complete and return the Google Doc URL.

**4c. Write Resume URL to Sheet**

Run `tools/sheets.py --action update_notes` with:
- `sheet_id` = `GOOGLE_SHEET_ID`
- `row_num` = the row number from 4a
- `notes` = the Google Doc URL returned by `tailor_resume`

---

## Step 5: Record Search Date

Write today's date to `.env` as `LAST_SEARCH_DATE`:

```text
python tools/onboarding.py --action write_env --key LAST_SEARCH_DATE --value "<today's date as YYYY-MM-DD>"
```

Do this before the workflow ends so the date is saved even if a later step fails.
On the next run, `search_indeed.py` will use this date to filter out anything already seen.

---

## Error Handling

| Error | Action |
|---|---|
| `.env` missing fields | Log error, exit silently |
| `LAST_SEARCH_DATE` missing | First run - pass empty string to `search_indeed.py`, return all results |
| `search_indeed.py` fails | Log error and stop |
| `search_dice.py` fails | Log error, continue with Indeed results only |
| Dice MCP unavailable | Log warning, continue with Indeed results only |
| Writing `LAST_SEARCH_DATE` fails | Log error, continue - next run will re-process recent jobs (job hash dedup prevents duplicates) |
| Sheet append fails | Log error, skip resume tailoring for that job |
| `tailor_resume` fails | Log error, leave Notes column blank for that row, continue |

All errors are logged to `.tmp/search_jobs_log.txt` with timestamp.

---

## Tools Used
- `tools/search_indeed.py` - parameter parsing and result normalization (Indeed queried via MCP)
- `tools/search_dice.py` - parameter parsing and result normalization (Dice queried via MCP)
- `tools/sheets.py` - reads/writes Google Sheet via Python Google API
- `workflows/tailor_resume.md` - resume tailoring sub-workflow
