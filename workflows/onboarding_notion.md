# Workflow: Onboarding Notion

## Objective
Walk the user through creating a Notion integration, connecting it to their Applications database,
and saving the credentials to `.env`. This is a one-time step.

## When This Runs
Called from `onboarding.md` when `NOTION_API_KEY` or `NOTION_DATABASE_ID` are missing from `.env`.

---

## Step 1: Explain What Notion Is Used For

Tell the user:

> This project tracks every job it finds — title, company, salary, link, and your tailored resume —
> in a Notion database. To connect to your Notion account, you'll need to create a free integration
> (an API key) and share your Applications database with it. This takes about 3 minutes.

---

## Step 2: Create the Notion Integration

Tell the user:

> First, open this link in your browser:
> **https://www.notion.so/my-integrations**
>
> Click **"New integration"**, give it any name (e.g. `JobSearchAutomation`), and set the
> associated workspace to the one that contains your job tracker.
>
> On the next screen, copy the **Internal Integration Token** — it starts with `secret_`.
> Paste it here.

Wait for the user to paste the token.

Save it:
```
python tools/onboarding.py --action write_env --key NOTION_API_KEY --value "<pasted token>"
```

---

## Step 3: Locate the Applications Database

Tell the user:

> Now open your Notion Applications database (or create one — see the note below).
>
> Click the **...** menu in the top-right corner of the page, then choose **"Connections"** and
> add the integration you just created. This gives the integration permission to read and write
> that database.
>
> Next, copy the database ID from the URL. The URL looks like:
> `https://www.notion.so/yourworkspace/<database-id>?v=...`
>
> The database ID is the long string of letters and numbers before the `?`. It looks like:
> `ad43c0d3017f4d55b3e79eb2f94c28b7`
>
> Paste it here.

Wait for the user to paste the database ID.

Save it:
```
python tools/onboarding.py --action write_env --key NOTION_DATABASE_ID --value "<pasted id>"
```

---

## Step 4: Verify the Connection

Run a quick check to confirm the credentials work:

```
python tools/notion.py --action get_job_hashes
```

- If the command exits with code 0 (even returning `[]`): the connection works. Tell the user:
  `Notion is connected.`
- If it fails with an authorization error: the integration was not added to the database.
  Tell the user to go back to the database, open **...** > **Connections**, and add the integration.
- If it fails with an API key error: the token was entered incorrectly. Ask the user to paste it again.

---

## Note: Required Database Properties

The Applications database must have these properties for the tool to work correctly.
If you're creating a new database, add these columns:

| Property Name   | Type       | Notes                                      |
|-----------------|------------|--------------------------------------------|
| Opportunity     | Title      | Job title — this is the page name          |
| Company / School| Rich text  |                                            |
| Role / Program  | Rich text  |                                            |
| Location        | Rich text  |                                            |
| Link            | URL        | Job posting URL                            |
| Salary          | Rich text  |                                            |
| Date Posted     | Rich text  |                                            |
| Job Hash        | Rich text  | Used for deduplication — do not rename     |
| Resume          | URL        | Filled in after resume tailoring           |
| Type            | Select     | Must have an option called "Job"           |
| Status          | Status     | Must have a status called "Interested"     |

If your database uses different property names, update the property mapping in `tools/notion.py`.
