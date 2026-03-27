# Workflow: Onboarding Finalize

## Objective
Ask the user which tracker they want (if not already chosen), complete tracker-specific setup,
and present the final setup summary.

## When This Runs
- Read this file after dependencies, Google setup, criteria, and resume setup are complete.

---

## Step 1: Choose a Tracker

If `TRACKER` is already set in `.env`, skip to Step 2.

Ask the user:

> **Where would you like to track your job applications?**
>
> **A) Google Sheets** — A spreadsheet in your Google Drive. Simple, familiar, no extra accounts.
>
> **B) Notion** — A database in your Notion workspace. Richer views, custom fields, good if you already use Notion.
>
> Type A or B.

Save the answer:
- If A: `python tools/onboarding.py --action write_env --key TRACKER --value sheets`
- If B: `python tools/onboarding.py --action write_env --key TRACKER --value notion`

---

## Step 2: Tracker-Specific Setup

### If TRACKER=sheets

If `GOOGLE_SHEET_ID` is missing:
- Tell the user: `Creating your Job Tracker spreadsheet in Google Drive...`
- Read `DRIVE_FOLDER_ID` from `.env`
- Run `python tools/sheets.py --action create --folder_id "<DRIVE_FOLDER_ID>"`
- Save the returned sheet ID:
  `python tools/onboarding.py --action write_env --key GOOGLE_SHEET_ID --value "<sheet_id>"`

### If TRACKER=notion

If `NOTION_API_KEY` is missing:
- Tell the user:
  `I need a Notion integration token. Go to https://www.notion.so/my-integrations, create a new integration (name it "Job Search"), and copy the token.`
- Ask for the token and save it:
  `python tools/onboarding.py --action write_env --key NOTION_API_KEY --value "<token>"`

If `NOTION_DATABASE_ID` is missing:
- Tell the user:
  `Now share your Applications database with the integration. Open the database in Notion → click the ••• menu → Connections → add your integration. Then copy the database ID from the URL (it's the 32-character string after the last slash, before the ?).`
- Ask for the database ID and save it:
  `python tools/onboarding.py --action write_env --key NOTION_DATABASE_ID --value "<database_id>"`

---

## Step 3: Confirm to the User

Tell the user:
`You're all set. Here's what I saved:`

For TRACKER=sheets, show:
- Job titles: `[JOB_TITLES]`
- Location: `[LOCATION]`
- Salary minimum: `[SALARY_MIN]`
- Keywords: `[KEYWORDS]`
- Resume on Drive: `[RESUME_DRIVE_URL]`
- Job tracker: `https://docs.google.com/spreadsheets/d/[GOOGLE_SHEET_ID]`
- All files are in the `JobSearchAutomation` Drive folder: `https://drive.google.com/drive/folders/[DRIVE_FOLDER_ID]`

For TRACKER=notion, show:
- Job titles: `[JOB_TITLES]`
- Location: `[LOCATION]`
- Salary minimum: `[SALARY_MIN]`
- Keywords: `[KEYWORDS]`
- Resume on Drive: `[RESUME_DRIVE_URL]`
- Job tracker (Notion): your Notion Applications database
- Tailored resumes stored in Drive folder: `https://drive.google.com/drive/folders/[DRIVE_FOLDER_ID]`

Then remind the user:
`To keep using this project, open it in Claude Code for VS Code or run Claude Code from the CLI in this folder. If you want automatic recurring runs, I can help set that up using the /loop slash command. Examples: /loop 5m check the deploy, /loop 30m /babysit-prs, /loop 1h run the workflow. You can switch trackers at any time by asking me to switch trackers.`

Then return control to `run.md`.

Do not run `workflows/search_jobs.md` from this file.
