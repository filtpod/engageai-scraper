# Scraper Job (DigitalOcean App Platform)

This folder contains a **standalone DigitalOcean App Platform Job** that runs the LinkedIn scraping workflow on a schedule and exits.

## What this does

- Connects to the same managed MySQL database used by the rest of this repo (via env vars).
- Iterates eligible users, fetches prospects, scrapes recent LinkedIn posts, stores submissions, updates prospect profiles.
- Optionally pushes timeline events to HubSpot (if HubSpot OAuth env vars are set).
- Sends an admin notification email at the end via Postmark.

## Files

- `scrape.py`: job entrypoint
- `services.py`: LinkedIn helpers + Postmark admin email
- `openai_api.py`: Azure OpenAI + DeepSeek summarization helpers
- `requirements.txt`: Python deps
- `Dockerfile`: container image for the job
- `do-app.yaml`: **separate** App Platform app spec for this job

## Required environment variables

At minimum, the scraper needs DB access:

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`

If you want all integrations enabled:

- **DeepSeek**: `DEEPSEEK_API_KEY` (used by default in `scrape.py`)
- **Azure OpenAI** (optional): `AZUREAI_API_KEY`, `AZUREAI_ENDPOINT`, `AZUREAI_DEPLOYMENT`
- **HubSpot** (optional): `HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET`
- **Postmark admin email**: `POSTMARK_SERVER_TOKEN`, optional `ADMIN_EMAIL`
- **Scrape-it** (optional fallback helper): `SCRAPE_IT_API_KEY`
- **Batch size** (optional): `SCRAPE_MAX_USERS_PER_RUN` (default `5`); runtime cap: `SCRAPE_MAX_RUNTIME_SECONDS`
- **Parallel prospect scraping** (optional): `SCRAPE_MAX_WORKERS` (default `4`), `SCRAPE_WRITE_QUEUE_SIZE` (default `50`)

Example `.env`:

```env
DB_HOST=db-mysql-xxxxxx.db.ondigitalocean.com
DB_PORT=25060
DB_NAME=defaultdb
DB_USER=doadmin
DB_PASSWORD=your_password_here

DEEPSEEK_API_KEY=your_deepseek_key

AZUREAI_API_KEY=your_azure_openai_key
AZUREAI_ENDPOINT=https://your-resource-name.openai.azure.com/
AZUREAI_DEPLOYMENT=gpt-4o-mini

HUBSPOT_CLIENT_ID=your_hubspot_oauth_client_id
HUBSPOT_CLIENT_SECRET=your_hubspot_oauth_client_secret

POSTMARK_SERVER_TOKEN=your_postmark_server_token
ADMIN_EMAIL=hello@engage-ai.co

SCRAPE_IT_API_KEY=your_scrape_it_key
```

## Deploy as a separate App Platform App (Job)

1. Ensure `scraper/do-app.yaml` points at the correct GitHub repo/branch.
2. Create the app:

```bash
doctl apps create --spec scraper/do-app.yaml
```

3. In the DigitalOcean Control Panel:
   - Go to **Apps** → your new app (`engageai-scraper-job`)
   - Select the **Job** component `prospect-scraper`
   - Set the environment variables listed above
   - Add this App as a **Trusted Source** on your Managed Database:
     - Database → Settings → Trusted sources → Quick select → Apps

### Schedule

The schedule is defined in `scraper/do-app.yaml`:

- Cron: `*/10 * * * *` (every 10 minutes)
- Time zone: `Australia/Sydney`

### Batch size

Each run loads at most **`SCRAPE_MAX_USERS_PER_RUN`** eligible users (default **5**), `ORDER BY last_login DESC`. This keeps runs short and easy to observe in logs; the same “most recently active” slice is processed on every run unless you change ordering or add a cursor later.

- **`SCRAPE_MAX_RUNTIME_SECONDS`**: Stops the run before the App Platform ~30m job limit (default `1500`). If a run exits early, remaining users in that batch are skipped until the next scheduled invocation.

### Parallelism (safe throughput)

Prospects are scraped concurrently using a worker pool, while database writes are serialized through a single writer loop (so you should not see concurrency-related DB corruption).

- **`SCRAPE_MAX_WORKERS`** (default `4`): number of concurrent prospect workers per run.
  - Rollback: set `SCRAPE_MAX_WORKERS=1` to effectively disable parallelism while keeping the same code path.
- **`SCRAPE_WRITE_QUEUE_SIZE`** (default `50`): bounded in-memory queue size between workers and the single writer.
  - If you increase workers, keep this conservative first to avoid large memory spikes.

## Run once manually (recommended)

After the app is created and env vars are set:

1. In the app page, open **Activity** → **Jobs**
2. Trigger a manual run (UI action is typically “Run job” / “Run now”)
3. Inspect logs for errors
4. Confirm side-effects:
   - New records in `podserver_prospectssubmissions`
   - Updated fields in `podserver_prospectsprofile`
   - Admin email sent via Postmark

## Local testing

### Run with Python

```bash
cd scraper
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ensure env vars are set in your shell, or use a loader like direnv.
python scrape.py
```

### Run with Docker (closest to DO runtime)

```bash
cd scraper
docker build -t engageai-scraper .

# If you have an env file at the repo root:
docker run --env-file ../.env engageai-scraper
```

