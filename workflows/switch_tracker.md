# Workflow: Switch Tracker

## Objective
Switch the active job tracker between Google Sheets and Notion, walk through any additional
setup required, and optionally migrate existing data from the old tracker to the new one.

## When This Runs
- Read this file when the user asks to switch trackers, change their tracker, or move from Sheets to Notion (or vice versa).

---

## Step 1: Show Current State

Read `TRACKER` from `.env`.

Tell the user what is currently configured:
- If `TRACKER=sheets`: `You're currently using Google Sheets. Tracker: https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>`
- If `TRACKER=notion`: `You're currently using Notion.`
- If `TRACKER` is missing: `No tracker is configured yet. Let's set one up.`

---

## Step 2: Confirm the Switch

Ask:
> Which tracker would you like to switch to?
>
> **A) Google Sheets** — A spreadsheet in your Google Drive.
>
> **B) Notion** — A database in your Notion workspace.

If the user selects the tracker that's already active, tell them:
`You're already using that tracker — no changes needed.`
and stop.

---

## Step 3: Additional Setup (if needed)

### Switching to Google Sheets

If Google auth is not valid, read `workflows/onboarding_google.md` and complete Google setup first.

If `GOOGLE_SHEET_ID` is missing:
- Tell the user: `Creating a new Job Tracker spreadsheet in Google Drive...`
- Run `python tools/sheets.py --action create --folder_id "<DRIVE_FOLDER_ID>"`
- Save the returned sheet ID:
  `python tools/onboarding.py --action write_env --key GOOGLE_SHEET_ID --value "<sheet_id>"`
- Tell the user: `Sheet created: https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>`

### Switching to Notion

If `NOTION_API_KEY` is missing:
- Tell the user:
  `I need a Notion integration token. Go to https://www.notion.so/my-integrations, create a new integration (name it "Job Search"), and copy the token.`
- Ask for the token and save it:
  `python tools/onboarding.py --action write_env --key NOTION_API_KEY --value "<token>"`

If `NOTION_DATABASE_ID` is missing:
- Tell the user:
  `Now share your Applications database with the integration. Open the database in Notion → click the ••• menu → Connections → add your integration. Then copy the database ID from the URL (32-character string after the last slash, before any ?).`
- Ask for the database ID and save it:
  `python tools/onboarding.py --action write_env --key NOTION_DATABASE_ID --value "<database_id>"`

---

## Step 4: Offer Data Migration

Ask:
> Would you like to copy your existing job entries from [old tracker] to [new tracker]?
>
> - **Yes** — I'll migrate all entries now so nothing is lost.
> - **No** — Start fresh. Old entries stay in the old tracker but won't appear in the new one.

### If Migrating: Sheets → Notion

1. Read all existing rows from the sheet:
   `python tools/sheets.py --action get_all_rows --sheet_id "<GOOGLE_SHEET_ID>"`

   This returns a JSON array of row objects: `{job_title, company, location, salary, date_posted, url, job_hash, resume_url, status}`.

2. Read existing Notion hashes to avoid re-importing duplicates:
   `python tools/notion.py --action get_job_hashes`

3. For each row whose `job_hash` is not already in Notion:
   - Run `python tools/notion.py --action create_entry` with the row's fields
   - If the row has a `resume_url`, run `python tools/notion.py --action update_resume_url`
   - Log each migrated job title + company

4. Tell the user how many entries were migrated.

### If Migrating: Notion → Sheets

1. Read all existing entries from Notion:
   `python tools/notion.py --action get_all_entries`

   This returns a JSON array of entry objects: `{job_title, company, location, salary, date_posted, url, job_hash, resume_url, status}`.

2. Read existing sheet hashes to avoid re-importing duplicates:
   `python tools/sheets.py --action get_job_hashes --sheet_id "<GOOGLE_SHEET_ID>"`

3. For each entry whose `job_hash` is not already in the sheet:
   - Build the row array and run `python tools/sheets.py --action append_row`
   - If the entry has a `resume_url`, run `python tools/sheets.py --action update_notes`
   - Log each migrated job title + company

4. Tell the user how many entries were migrated.

**Migration failures:** If an individual entry fails to migrate, log it and continue — do not abort the whole migration.

---

## Step 5: Update TRACKER

Save the new tracker choice:
- If switching to sheets: `python tools/onboarding.py --action write_env --key TRACKER --value sheets`
- If switching to notion: `python tools/onboarding.py --action write_env --key TRACKER --value notion`

---

## Step 6: Confirm

Tell the user:
- What tracker is now active
- The URL of the new tracker (for sheets: spreadsheet link; for notion: database note)
- Whether migration ran and how many entries were transferred (or that they're starting fresh)
- `All future job searches will use [new tracker]. Your old [tracker] is unchanged — you can still view it anytime.`

---

## Notes

- The old tracker's data is never deleted by this workflow.
- If `get_all_rows` or `get_all_entries` actions are not yet implemented in the tools, log a clear
  error and tell the user to migrate manually — do not fabricate data.
- `sheets.py` already supports `get_job_hashes`. The `get_all_rows` and `notion.py get_all_entries`
  actions may need to be added to the tools if migration is requested.
