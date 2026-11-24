# Composio + Deep Agents template

A small starting point for an agent that works in your connected apps (Gmail and Google Drive by default) through Composio, built on [Deep Agents](https://github.com/langchain-ai/deepagents) (`create_deep_agent`). It plans with a todo list, keeps notes in a virtual filesystem, and hands read-heavy lookups to an `app-researcher` subagent. Before any send, share, delete, edit or upload, it stops and asks you to approve.

## Files
| File | What it does |
|---|---|
| `composio_setup.py` | Creates the Composio client and one session per user |
| `connect.py` | Optional: connect an account from the terminal (the web app has its own sign-in) |
| `auth.py` | Web sign-in: device cookies and the confirmed connection per user |
| `db.py` | Database connection (Cloudflare D1 or MySQL) and table definitions |
| `d1.py` | Cloudflare D1 driver: runs SQL over D1's HTTPS API for SQLAlchemy |
| `migrate_to_d1.py` | One-time copy of all MySQL data into D1 |
| `summaries.py` | Daily and monthly summaries: storage, the `month-summarizer` subagent, and the nightly/month-end job |
| `company_holidays.py` | Company holidays: reading PDF/Excel/CSV lists, the `holidays/` folder, storage |
| `day_mail.py` | `read_day_emails`: reads a day's received and sent mail completely (every thread, every message, quoted and forwarded parts) and returns notes per conversation |
| `eod.py` | Sending EOD reports: fixed recipients, the reporting manager in Cc, the `send_eod_report` tool |
| `db_tools.py` | The assistant's database tools: fetch, insert, update (scoped to the signed-in user) |
| `agent.py` | `create_deep_agent(...)` with the Composio tools, an `app-researcher` subagent and an `interrupt_on` approval gate |
| `chat.py` | Terminal chat that shows the approval prompt |
| `server.py` | Web server: JSON API for the agent, and serves `frontend/` |
| `frontend/` | Browser UI (Next.js + JavaScript): day rail, conversation, approval previews, notes panel |
| `datetime_tools.py` | India-time date tools (see below) |
| `memory.py` | Long-term memory per user in `memories/<id>/AGENTS.md` (see below) |

## Setup
Requires Python 3.10 or newer.

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # Windows: copy .env.example .env
```

Fill in `.env`:
- `COMPOSIO_API_KEY`: from https://platform.composio.dev, under Settings -> API Keys
- `AZURE_OPENAI_*` and `OPENAI_API_VERSION`: your Azure OpenAI resource and deployment. If your endpoint ends in `/openai/v1`, the API version is ignored.
- `COMPOSIO_TOOLKITS`: the apps the agent may use, comma-separated (default `gmail,googledrive`)

## 1. Use the web app (easiest)
The UI is Next.js with plain JavaScript and needs Node.js 20.9 or newer. Build it once, and again after you change anything in `frontend/`:
```bash
cd frontend
npm install
npm run build      # writes a static site to frontend/out
cd ..
python server.py
```
Open http://127.0.0.1:8000.

**Signing in (anyone, from their own device)**
1. Enter your work email, pick your role, and answer **Do you have a reporting manager?** If yes, enter their email: they go in Cc on your EOD reports.
2. Click **Continue with Google**. This opens Composio's sign-in page, where you sign in with Google and allow access. It connects your Gmail for the assistant and brings you back.
3. The server asks Gmail which account just signed in. If it isn't the email you typed, sign-in is refused and that connection is removed, so nobody can get into someone else's inbox by typing their address.
   The role and manager are saved to your `users` row only after this check, so nobody can set them for someone else. People who signed up before these questions existed are asked once.
4. Connect any other apps in `COMPOSIO_TOOLKITS` (for example Google Drive) with one click each.

That browser then stays signed in for 90 days (a secure, httpOnly cookie). A new device or browser repeats step 2. **Sign out** in the left rail ends it. Signed-in devices and each person's confirmed connection are stored in MySQL (see below). The assistant only ever uses that confirmed connection.

**Letting other people use it**: run the server where their devices can reach it and tell it its address:
- Same Wi-Fi/office network: set `HOST=0.0.0.0` and `PUBLIC_URL=http://<this computer's IP>:8000` in `.env`.
- Over the internet: put it behind HTTPS (a hosting provider, or a reverse proxy) and set `PUBLIC_URL=https://your-domain`. Use HTTPS for anything beyond your own network, because the sign-in cookie must not travel unencrypted.

Each person signs in with their own Google account, and their memory, chats and notes stay separate.
- **Summarize a day**: click a day in the left rail (the last 7 days are listed), or pick another date. Fill in "Only from" to limit it to one sender. You can also just type in the box at the bottom.
- **Write today's EOD**: the big card on the welcome screen (and the chip above the message box) asks for a detailed EOD report from today's emails. See [EOD reports](#eod-reports) below.
- **Approvals**: before any send, share, delete, edit or upload, a "This needs your OK" block shows a preview of exactly what will happen (for an email: To, Subject and the message). Approve it, or Decline and say what should change.
- **Notes**: click **Add to notes** under any reply. The Notes panel opens on the right with the summary as editable text, a to-do checklist and a free-text box. **Save notes** writes `outputs/notes-<date>-<title>.md`. **Share with assistant** sends your version back so it uses it from now on.

**Run it locally with one command** (live reload for the UI):
```bash
cd frontend
npm install        # first time only
npm run dev
```
Open http://localhost:3000. `npm run dev` runs `frontend/scripts/dev.mjs`, which:
- reuses the Python API if `server.py` is already running on http://127.0.0.1:8000, or otherwise starts it with the project's `venv` Python (its log shows in the same window);
- starts the Next.js dev server on port 3000, which forwards `/api` calls to the Python API (see `frontend/next.config.mjs`).

Ctrl+C stops both. `npm run dev:web` starts only the web app. Set `API_URL` to use the API on another address or port.

| Frontend file | What it does |
|---|---|
| `frontend/app/layout.js` | Page shell: title, icon, fonts, global styles |
| `frontend/app/page.js` | Loads the app in the browser |
| `frontend/app/globals.css` | All styling. Colors and fonts are variables at the top. Light and dark themes |
| `frontend/components/App.jsx` | Main screen: sign-in, conversation, composer; holds the app state |
| `frontend/components/Rail.jsx` | Left rail: new conversation, last 7 days, another date, sender filter |
| `frontend/components/Messages.jsx` | Welcome screen, your messages, assistant replies, errors, the working indicator |
| `frontend/components/Confirm.jsx` | The "needs your OK" block with action previews |
| `frontend/components/NotesPanel.jsx` | Notes panel: summary, to-do checklist, save and share |
| `frontend/components/SignIn.jsx`, `Icon.jsx` | Sign-in screen and icons |
| `frontend/lib/api.js` | Calls to `server.py` |
| `frontend/lib/utils.js` | Helpers: markdown and email HTML rendering (sanitized), dates, storage |

Fonts (Newsreader and IBM Plex Sans) are bundled through npm, so the app works offline and loads nothing from a CDN.

The server only listens on your own computer (127.0.0.1) and has no login, so don't expose it to a network as-is.

## 2. Or chat in the terminal
The terminal chat skips the web sign-in, so connect the account once first:
```bash
python connect.py <your-id> gmail googledrive
```
```bash
python chat.py <your-id>
```
Use the same id you used with `connect.py`. Example request:
`find the latest email from <sender> and save its PDF attachment to Drive`

Each session is also saved as markdown in `outputs/<your-id>-<date>-<time>.md`: your message, the agent's reply and its plan for every turn. The folder is git-ignored because it can contain email and file content.

Reads run right away. Any write pauses with `Approve? [y/N]`. If you decline, you can say what to change.

## What the deep agent adds
- **Planning**: `write_todos` keeps a task list for multi-step requests. `chat.py` prints it after each reply.
- **Virtual filesystem**: `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep` store scratch notes in the agent's state (the default `StateBackend`). Nothing touches your disk or your Drive. To use a real folder, pass `backend=FilesystemBackend(root_dir="./workspace")` in `build_agent`.
- **Subagents**: the `task` tool starts `app-researcher` (defined in `agent.py`) or the built-in `general-purpose` subagent in a fresh context, so long email or file listings don't fill the main conversation. Subagents inherit the approval gate.

## Database (Cloudflare D1 for testing, or MySQL)
### Cloudflare D1 (current, for testing)
D1 is Cloudflare's SQL database (SQLite underneath). The app reaches it over Cloudflare's HTTPS API through `d1.py`, a small driver that lets SQLAlchemy's SQLite dialect run every existing query unchanged.

1. Cloudflare dashboard -> **Storage & Databases -> D1** -> **Create database** (e.g. `eod-automation`). Copy its **Database ID**.
2. Your **Account ID** is on the right side of the dashboard's Overview page (or in the URL).
3. **My Profile -> API Tokens -> Create Token -> Custom token**, permission **Account · D1 · Edit**, limited to your account.
4. In `.env`:
   ```
   DB_BACKEND=d1
   CLOUDFLARE_ACCOUNT_ID=...
   CLOUDFLARE_D1_DATABASE_ID=...
   CLOUDFLARE_API_TOKEN=...
   ```
5. Copy your existing MySQL data across (keeps the `MYSQL_*` settings as the source; safe to run again, nothing is duplicated):
   ```bash
   python migrate_to_d1.py          # copies every table, then compares row counts
   python migrate_to_d1.py --check  # only compare
   ```
6. `python server.py` prints `Database: Cloudflare D1 (SQLite 3.x), database ...`. Tables are created automatically.

Good to know for testing: D1 over HTTP has no transactions (each statement is saved on its own), and every query is a network round trip, so pages that read a lot are a little slower than with local MySQL. Set `DB_BACKEND=mysql` to switch back; nothing else changes.

You can look at the data in the dashboard (D1 -> your database -> **Console**), with the same queries as below minus the `` `eod-automation`. `` prefix, e.g. `SELECT id, role, manager_email FROM users;`. SQLite stores dates as text (`2026-11-24`), so use `strftime('%d-%m-%Y', summary_date)` instead of `DATE_FORMAT`.

### MySQL
Set `DB_BACKEND=mysql` (or leave it out) and the app stores its data in MySQL. Set these in `.env` (the schema must already exist; tables are created automatically on first start and never dropped):
```
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=...
MYSQL_DATABASE=eod-automation
```
`python server.py` prints `Database: MySQL 8.x, schema ...` when it connects, or a plain explanation if it can't (wrong password, unknown schema, service not running).

| Table | What's in it |
|---|---|
| `users` | One row per person (their verified email), their `role`, `has_manager` and `manager_email` (Cc on EOD reports), first seen and last sign-in |
| `devices` | Signed-in browsers. Only a SHA-256 hash of the cookie is stored, so the table can't be used to log in |
| `connected_accounts` | The Composio connection each person confirmed per app; the assistant only uses these |
| `conversations`, `messages` | Every chat and both sides of each turn |
| `notes` | Notes and to-dos saved from the Notes panel |
| `memories` | Each person's long-term memory (the AGENTS.md document) |
| `daily_summaries` | One summary of each person's email per day. Key: (email, date) |
| `monthly_summaries` | One consolidated summary per person per month, built from that month's daily rows. Key: (email, month) |
| `leave_days` | Days a person marked as leave. Key: (email, date) |
| `holidays` | Company holidays, the same for everyone: date, name, source file, who uploaded it. Key: date |

Deleting a row in `users` removes everything of theirs. The markdown copies in `outputs/` are still written for convenience. Older `data/*.json` sign-in files are imported once and renamed to `*.json.imported`.

## EOD reports
Click **Write today's EOD**. The assistant calls `read_day_emails` (in `day_mail.py`), which does the reading in code so nothing can be skipped:
1. Searches the day's **received** and **sent** mail, every page.
2. Opens every conversation from both, and every message in it, with its full text. HTML-only messages are converted to text. Quoted replies and forwarded chains inside a message are part of its body, so they are read line by line too.
3. Gives each whole conversation to the model separately (6 at a time) to pull out every work detail: what was asked, what you did or replied, figures, decisions, approvals, problems, deadlines, next steps. Newsletters and notifications are marked "not work".
4. Returns those notes plus a coverage line, e.g. "Read 22 of 22 conversations in full (25 messages)". If any conversation couldn't be read, the assistant retries once, then tells you instead of sending a report with gaps.

Then it writes the report in the usual shape (numbered topic sections with 3-4 lines each, Next Plan of Action, Regards) and hands it to `send_eod_report`. Nothing is sent yet: a "This needs your OK" card shows the exact email with To, Cc and Subject.
- **Approve & send** sends it from your Gmail.
- **Add points** opens a list with a + button. Jot a few words per point. The assistant puts each point into the section it belongs to (or a new numbered section), writes it out in full, and shows the new version for approval again. Repeat as often as you like.
- **Decline** stops, optionally with what to change.

Recipients are set by the server, never by the model:
- **To**: always `ranjitha@armstrong-cap.com` and `manju@armstrong-cap.com` (change with `EOD_RECIPIENTS` in `.env`).
- **Cc**: your reporting manager, if you said you have one. Change it any time under **Your EOD goes to** in the left rail (`POST /api/manager`).

The assistant can only add someone else in Cc if you ask it to, and they show on the card before anything is sent. Leave days and holidays are skipped as usual.

Check who gets each person's EOD:
```sql
SELECT id, role, has_manager, manager_email FROM `eod-automation`.users;
```

## Daily and monthly summaries
Each person's email is summarized per day and consolidated per month, in MySQL:

- **`daily_summaries`**: one row per person per day: `user_id` (their email), `summary_date`, `summary`, and `source` (`chat` if they asked for it, `auto` if the nightly job made it). Asking again for the same day replaces that day's row.
- **`monthly_summaries`**: one row per person per month: `user_id`, `month` (stored as the 1st, e.g. `2026-11-01` for November 2026), the consolidated `summary` and `days_included`.

The email is the primary key of `users`. The two summary tables key on (email, date) and (email, month), because each person has many days and months. Dates are stored as MySQL `DATE` values and shown as `DD-MM-YYYY` (for example `16-11-2026`).

How rows get filled:
1. **When someone summarizes a day**, from the day rail or by asking ("summarize 16 nov"), the assistant saves that summary for that date.
2. **Every night after 23:00 India time**, the server summarizes the day for everyone who signed in with Google and has no row yet, and catches up yesterday if the server was off. This reads mail and costs model calls per person per day. Turn it off with `AUTO_DAILY_SUMMARIES=false`.
3. **When a month ends**, the `month-summarizer` subagent consolidates its daily rows into `monthly_summaries`: an overview, the main topics, key dates and anything still open. It reads only the saved daily summaries, not the mailbox. If more days are saved later, the month is rebuilt. The same subagent handles "summarize November" or "consolidate this month" in chat.

In chat people can also ask "how was November?" (answered from saved summaries first) or "consolidate this month".

### Leave days
People can mark days they were (or will be) on leave:
- **In the left rail**: hover a day and click **Leave** (click **×** to undo). For any other date, including future ones, pick it under **Another date** and click **Mark as leave**.
- **In chat**: "I'm on leave 22 to 26 Dec" or "I wasn't off on the 14th". The assistant uses its `mark_leave` tool.

Leave days are stored in `leave_days` and skipped everywhere: they can't be summarized from the rail, the nightly job doesn't summarize them, EOD and weekly reports leave them out, and the monthly summary doesn't count them as missing (it lists them in one line at the end). If you ask about a leave day directly, the assistant says it's marked as leave and asks whether to summarize it anyway.

### Company holidays
One holiday list for the whole company, in the `holidays` table.
- **From the `holidays/` folder**: every PDF, Excel (.xlsx) or CSV file in it is read when the server starts (oldest first, so the newest file for a year wins). The 2026 list is already there.
- **HR upload**: people whose role is **Human Resources** get an **Upload holiday list** button under *Upcoming holidays* in the left rail. The file is read straight away, replaces the holidays for the years it covers, and is saved into `holidays/`. Other roles can't upload: the server checks the role, not just the UI.
- **File format**: one holiday per line or row with a date and a name, e.g. `16 February 2026 Monday Maha Shivaratri`, `16-02-2026, Maha Shivaratri` or `2026-02-16,Maha Shivaratri`. Numeric dates are read day first.

Holidays show in the calendar with their name, can't be summarized, and are skipped like leave days: no nightly summary, not part of EOD, weekly or monthly reports, and never counted as missing. The monthly summary lists them in one line. Everyone sees the next few holidays in the rail. To keep the files elsewhere, set `HOLIDAYS_DIR`.

### Database tools the assistant can use
The assistant doesn't write raw SQL. It has three structured tools, always limited to the signed-in person's own rows (the code adds `user_id`, the model can't choose it):

| Tool | What it does | Tables |
|---|---|---|
| `fetch_records` | Read rows with filters (`column`, `__gte`, `__lte`, `__gt`, `__lt`, `__contains`, `__in`), sorting and a limit (max 100) | `daily_summaries`, `monthly_summaries`, `notes`, `conversations`, `messages` |
| `insert_record` | Add a row | `daily_summaries`, `monthly_summaries`, `notes` |
| `update_records` | Change rows. **Asks for approval first**, showing what will change | `daily_summaries`, `monthly_summaries`, `notes` |

Sign-in tables (`users`, `devices`, `connected_accounts`) and `memories` are never reachable from these tools, and there is no delete. Dates can be given as `16-11-2026`, `16 nov` or `today`, and come back as `DD-MM-YYYY`. To allow another table or column, add it to `TABLES` in `db_tools.py`.

Settings in `.env`: `DAILY_SUMMARY_HOUR` (default 23), `AUTO_DAILY_SUMMARIES` (default true) and `SUMMARY_SCHEDULER` (`off` disables the background job). The job runs inside `python server.py`, every 30 minutes, so the server must be running for it to happen.

## Memory across chats
The assistant remembers things between chats and restarts, separately for each user, in the MySQL table `memories` (one markdown document per person):
- **People**: who someone is and their email, e.g. "Alisha (manager): alisha@…". Once it knows, it won't ask again.
- **About me**: your name and how you sign emails.
- **Preferences**: how you like summaries, reports and emails written, and corrections you've given.
- **Recurring tasks**: e.g. where your weekly EOD report goes.

It saves these quietly while it works. The file is read at the start of each new conversation, so something learned now is used from your next conversation on (and right away in the current one).

The assistant works on a local copy, `memories/<your-id>/AGENTS.md`, which is refreshed from MySQL when it starts and saved back after every turn. To change what it knows, edit the `content` column in `memories` (with the server stopped), or just tell it "forget X" or "Alisha's email is now …". It never stores passwords, OTPs, bank or card numbers, or email contents.

## Dates and time zone
Composio returns times in UTC. `datetime_tools.py` gives the agent three tools so it works in India time (`Asia/Kolkata`) instead:
- `current_datetime`: today's date and time in IST.
- `resolve_date_range`: turns "16 nov", "16/11", "yesterday" or "1 nov to 15 nov" into exact IST day bounds, with a ready Gmail filter (`after:<epoch> before:<epoch>`) and Drive filter. A date without a year means the current year.
- `to_local_time`: converts UTC timestamps in app results to IST.

To use a different zone, set `AGENT_TIMEZONE` in `.env` (for example `Asia/Dubai`). These tools never ask for approval.

## How approval works
The model gets Composio's meta tools (`COMPOSIO_SEARCH_TOOLS`, `COMPOSIO_MULTI_EXECUTE_TOOL`, ...) instead of hundreds of app tools, which keeps its context small. `build_interrupt_on` in `agent.py` gives Deep Agents' `interrupt_on` a `when` check that looks at every action inside `COMPOSIO_MULTI_EXECUTE_TOOL`:
- The verb after the app prefix decides it. `GMAIL_FETCH_EMAILS` and `GOOGLEDRIVE_FIND_FILE` run right away. `GMAIL_SEND_EMAIL` and `GOOGLEDRIVE_UPLOAD_FILE` ask first.
- To skip the prompt for a specific action, add its exact slug to `AUTO_APPROVE`, for example `GMAIL_CREATE_EMAIL_DRAFT`.

## Adding apps
Add Composio slugs to `COMPOSIO_TOOLKITS` (for example `gmail,googledrive,googlecalendar,slack,notion`), then restart the server. People are asked to connect the new app the next time they open the web app.

## Notes
- Chat history lives in memory and resets each time you start `chat.py`. To keep it, pass a persistent checkpointer to `make_agent` such as `langgraph-checkpoint-postgres`.
- Google tokens are stored by Composio, not on your machine. Never commit `.env`.
