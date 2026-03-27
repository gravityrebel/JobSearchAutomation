# Workflow: Search Jobs

## Objective
Silently search Indeed and Dice for new jobs matching stored criteria, track new results in the
configured tracker (Google Sheets or Notion), and trigger resume tailoring for each new job.

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
- `TRACKER` - either `sheets` or `notion`
- `RESUME_DRIVE_URL`
- `LAST_SEARCH_DATE` - ISO date string `YYYY-MM-DD` (may be empty on first run - that is expected)

If TRACKER=sheets, also load:
- `GOOGLE_SHEET_ID`

If TRACKER=notion, also load:
- `NOTION_API_KEY`
- `NOTION_DATABASE_ID`

If TRACKER=sheets and there is no `GOOGLE_SHEET_ID`, let the user know they need to set up sheets, read from `onboarding_google.md` and walk the user through the process of setting up sheets.

If TRACKER=sheets and there are missing `NOTION*` entries, let the user know they need to set up Notion, read from `onboarding_google.md` and walk the user through the process of setting up Notion.

If there has been a change, ask the user about doing a migration.

If any other required field other than `LAST_SEARCH_DATE` is missing, log an error and exit. Do not ask the user - the schedule should not block on input.

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

## Step 3: Deduplicate Against Existing Tracker

### If TRACKER=sheets

Run `python tools/sheets.py --action get_job_hashes --sheet_id "<GOOGLE_SHEET_ID>"`.

The tool returns the list of job hashes already stored in column J of the sheet.

### If TRACKER=notion

Run `python tools/notion.py --action get_job_hashes`.

The tool returns the list of job hashes already stored in the Notion Applications database.

---

Filter the results from Step 2: keep only jobs whose `job_hash` is NOT already in that list.

**Why hashes, not URLs:** Indeed generates different short URLs for the same job across different search queries. The job hash (extracted from the job ID's 5th segment) is stable and definitive.

- If 0 new jobs remain after filtering, log `"No new jobs this run."` and stop.

---

## Step 4: For Each New Job

For each new job in the filtered list:

**4a. Score the Job**

Before creating the tracker entry, score the job 1–10 against the candidate profile:

Rubric (add the three components):
- **Title fit (0–4):** Exact "Engineering Manager" or "Senior/Lead EM" = 4; "Software/Systems EM" or "Manager, Engineering" = 3; adjacent senior-leadership title = 2; poor match = 0–1
- **Salary fit (0–3):** Salary floor ≥ $175k = 3; floor $150k–$175k = 2; floor $120k–$150k = 1; floor below $120k = 0
- **Location fit (0–3):** DC/MD/VA metro or fully remote = 3; remote but listed in another city = 2; requires relocation = 1; on-site outside DC = 0

Write a one-sentence reason citing the score drivers (include actual dollar figures from the salary field).

**4b. Create Tracker Entry**

### If TRACKER=sheets

Run `python tools/sheets.py --action append_row --sheet_id "<GOOGLE_SHEET_ID>"` with:
```
--row '["<today>","<job_title>","<company>","<location>","<salary>","<date_posted>","<url>","New","","<job_hash>"]'
```

The tool returns the row number of the newly appended row. Store as `tracker_id`.

### If TRACKER=notion

Run `python tools/notion.py --action create_entry` with:
- `--job_title` = job title
- `--company` = company name
- `--location` = job location
- `--salary` = salary string (may be blank)
- `--date_posted` = date posted string
- `--url` = job posting URL
- `--job_hash` = the job's hash

The tool returns the Notion page ID of the newly created entry. Store as `tracker_id`.

Then run `python tools/notion.py --action update_match_score` with:
- `--page_id` = `tracker_id`
- `--match_score` = the integer score from 4a
- `--match_reason` = the one-sentence reason from 4a

**4c. Tailor Resume**

Call `workflows/tailor_resume.md` with:
- `job_url` = the job's URL
- `job_description` = the job's description text
- `company` = company name
- `job_title` = job title
- `tracker_type` = value of `TRACKER` (either `sheets` or `notion`)
- `tracker_id` = the row number or page ID from 4b

Wait for `tailor_resume` to complete and return the Drive URL.

**4d. Write Resume URL to Tracker**

### If TRACKER=sheets

Run `python tools/sheets.py --action update_notes` with:
- `--sheet_id` = `GOOGLE_SHEET_ID`
- `--row_num` = `tracker_id`
- `--notes` = the Drive URL returned by `tailor_resume`

### If TRACKER=notion

Run `python tools/notion.py --action update_resume_url` with:
- `--page_id` = `tracker_id`
- `--resume_url` = the Drive URL returned by `tailor_resume`

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
| Tracker entry creation fails | Log error, skip resume tailoring for that job |
| `tailor_resume` fails | Log error, leave resume field blank for that entry, continue |

All errors are logged to `.tmp/search_jobs_log.txt` with timestamp.

---

## Tools Used
- `tools/search_indeed.py` - parameter parsing and result normalization (Indeed queried via MCP)
- `tools/search_dice.py` - parameter parsing and result normalization (Dice queried via MCP)
- `tools/sheets.py` - creates and updates job entries in the Google Sheet (when TRACKER=sheets)
- `tools/notion.py` - creates and updates job entries in the Notion database (when TRACKER=notion)
- `workflows/tailor_resume.md` - resume tailoring sub-workflow
