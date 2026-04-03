# Scraper Job

Runs the LinkedIn scraping workflow: eligible users from MySQL, recent posts per prospect, DeepSeek summaries, DB writes, and admin email at end.

**Recommended production setup:** a **DigitalOcean Droplet** running the **Docker** image on a **cron** schedule (no 30-minute job limit, tunable parallelism). The repo includes **`do-app.yaml`**, a **cloud-init** user-data file to install Docker, clone the repo, build the image, and install cron (not an App Platform spec).

## Architecture

**Parallelize by user, not by prospect:** each user has a **distinct LinkedIn cookie** (rate-limit bucket). Many users are processed concurrently; **within one user**, prospects run **one after another** so the same cookie is not hammered with parallel LinkedIn calls.

```text
ThreadPoolExecutor (SCRAPE_MAX_WORKERS users at once)
  └── user_worker(user)
        └── for each prospect: LinkedIn + DeepSeek → enqueue writes

Single writer thread + Queue (SCRAPE_WRITE_QUEUE_SIZE)
  └── INSERT submissions, UPDATE prospect profile (serialized commits)
```

User work is **batched** when submitting to the pool so tens of thousands of `Future` objects are not created at once.

## Files

- `scrape.py`: job entrypoint
- `services.py`: LinkedIn helpers + Postmark admin email
- `openai_api.py`: Azure OpenAI + DeepSeek summarization helpers
- `requirements.txt`: Python deps
- `Dockerfile`: container image
- `do-app.yaml`: Droplet **cloud-init** (user data) — see below

## Environment variables

**Database (required):** `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`

**Integrations:**

- **DeepSeek**: `DEEPSEEK_API_KEY` (used in `scrape.py`)
- **Azure OpenAI** (optional): `AZUREAI_API_KEY`, `AZUREAI_ENDPOINT`, `AZUREAI_DEPLOYMENT`
- **Postmark**: `POSTMARK_SERVER_TOKEN`, optional `ADMIN_EMAIL`
- **Scrape-it** (optional helper in `services.py`): `SCRAPE_IT_API_KEY`

**Parallelism (defaults tuned for a 4 GB droplet):**

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCRAPE_MAX_WORKERS` | `32` | Concurrent **users** (each user’s prospects run sequentially). |
| `SCRAPE_WRITE_QUEUE_SIZE` | `200` | Max queued write batches before workers block. |

**Lock file (overlapping cron runs):**

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCRAPE_LOCK_FILE` | `/tmp/scraper.lock` | PID file; second run exits if the first is still alive. |
| `SCRAPE_LOCK_DISABLED` | unset | Set to `1` / `true` / `yes` to disable the lock (e.g. local dev). |

**Optional:** `SCRAPE_RUN_NUMBER` for log correlation.

Example `.env`:

```env
DB_HOST=db-mysql-xxxxxx.db.ondigitalocean.com
DB_PORT=25060
DB_NAME=defaultdb
DB_USER=doadmin
DB_PASSWORD=your_password_here

DEEPSEEK_API_KEY=your_deepseek_key

POSTMARK_SERVER_TOKEN=your_postmark_server_token
ADMIN_EMAIL=hello@engage-ai.co

SCRAPE_MAX_WORKERS=32
SCRAPE_WRITE_QUEUE_SIZE=200
```

## Deploy on a DigitalOcean Droplet (recommended)

**Droplet size:** start with **`s-2vcpu-4gb`** (Sydney or your DB region). Workload is I/O-bound; 2 vCPUs and 4 GB RAM are enough for ~32 concurrent user workers. Increase `SCRAPE_MAX_WORKERS` only if memory stays comfortable and APIs/DB allow it.

### Droplet cloud-init (`do-app.yaml`)

`do-app.yaml` is **[cloud-init](https://cloudinit.readthedocs.io/)** user data. The first line must stay `#cloud-config`. Edit the `git clone` URL and branch inside the `runcmd` script if your fork or default branch differs.

**Create via UI:** New Droplet → **Advanced options** → **Add Initialization scripts** → paste the contents of `do-app.yaml`.

**Create via CLI:**

```bash
doctl compute droplet create scraper-1 \
  --image ubuntu-22-04-x64 \
  --size s-2vcpu-4gb \
  --region syd \
  --user-data-file do-app.yaml \
  --ssh-keys <your-key-id>
```

After the droplet boots: **SSH in**, edit `/opt/engageai-scraper/.env` with real values (the bootstrap only creates a placeholder), then rebuild if needed: `docker build -t engageai-scraper /opt/engageai-scraper`. Add the droplet’s IP to **Managed Database → Trusted sources**.

**Logs:** `tail -f /var/log/engageai-scraper.log`

Private Git repos need a [deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys/deploy-keys) or other clone method; adjust the `runcmd` clone step accordingly.

### Manual setup (same layout as cloud-init)

1. Create a droplet (Ubuntu; install Docker if not using cloud-init).
2. Add the droplet IP to your **Managed Database trusted sources** (if applicable).
3. On the droplet:

```bash
git clone <your-repo> /opt/engageai-scraper && cd /opt/engageai-scraper
docker build -t engageai-scraper .
# Put secrets in /opt/engageai-scraper/.env (see above)
```

4. **Cron** (overlap is prevented by the lock file); match what `do-app.yaml` installs:

```cron
0 */12 * * * docker run --rm --env-file /opt/engageai-scraper/.env engageai-scraper >> /var/log/engageai-scraper.log 2>&1
```

5. **Logs:** `tail -f /var/log/engageai-scraper.log` or your log shipper.

**Tuning:** If LinkedIn or DeepSeek throttles, lower `SCRAPE_MAX_WORKERS`. If the writer falls behind (queue full / workers block), raise `SCRAPE_WRITE_QUEUE_SIZE` slightly or check DB latency.

## Deploy as App Platform Job (optional)

You can run this workload as a **scheduled Job** on App Platform instead of a Droplet: create an app in the control panel (or `doctl apps create` with a separate **App spec** YAML), point the job at this repo’s `Dockerfile`, set environment variables there, and add the app as a DB trusted source.

Note: App Platform scheduled jobs often have an **execution time limit** (on the order of tens of minutes); long full-fleet runs are usually better on a droplet.

## Local testing

### Python

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SCRAPE_LOCK_DISABLED=1
# set DB_* and other vars
python scrape.py
```

### Docker

```bash
docker build -t engageai-scraper .
docker run --rm --env-file .env -e SCRAPE_LOCK_DISABLED=1 engageai-scraper
```

## Manual run on App Platform

Use **Activity → Jobs → Run** and watch logs for errors; confirm rows in `podserver_prospectssubmissions` / `podserver_prospectsprofile` and the admin email.
