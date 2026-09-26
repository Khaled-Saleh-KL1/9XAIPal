# Pipeline Tracing Runbook

## What it records

Pipeline tracing captures a complete audit trail of every request the app handles: each chat question and each ingestion (paper, book, article, URL). The trace viewer shows what was processed, what steps were taken, what was produced, how long each step took, and whether any step failed.

Each trace is a tree of spans (blocks) that records:
- **Chat traces** (`/ask` endpoint): routing decision, context building, retrieval, web search, agent tool loops, every LLM call, latency, token counts, and final answer
- **Ingestion traces** (document upload): extraction, chunking, embeddings, section summaries, figure descriptions, reading-order reconstruction, and status

Traces include real content — prompts, model answers, retrieved chunks, search results, extracted text — truncated to a size cap and visible only behind the trace site's login. Traces are deleted automatically after the retention period (default 14 days).

## Enabling tracing

### Step 1: Deploy

After merging this branch and deploying, the Phoenix service starts automatically, and its database (`phoenix` on the shared Postgres) is created by the deploy step:

```bash
cd backend
docker compose -f docker-compose.prod.yml up -d
```

Confirm Phoenix is running:

```bash
docker compose -f docker-compose.prod.yml ps phoenix
```

Phoenix is now ready and listening on `127.0.0.1:6006` (internal only). Tracing in the app is **off by default** (`TRACE_ENABLED=false`), so the app behaves exactly as before.

### Step 2: Install the nginx site and get a certificate

Add the DNS A record for `9xaipal-trace.kl1.site` to your registrar (point to the same IP as `9xaipal.kl1.site`).

Copy the nginx server block to your host:

```bash
sudo cp backend/nginx/9xaipal-trace.conf /etc/nginx/sites-available/9xaipal-trace.conf
sudo ln -s /etc/nginx/sites-available/9xaipal-trace.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Obtain a TLS certificate from Let's Encrypt:

```bash
sudo certbot --nginx -d 9xaipal-trace.kl1.site
```

The site is now reachable at `https://9xaipal-trace.kl1.site`, but login is required.

### Step 3: Create the API key and set it in .env

Log in to the Phoenix UI at `https://9xaipal-trace.kl1.site` with the initial admin credentials:
- Username: `admin`
- Password: (the value of `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` in `backend/.env`)

Change the admin password immediately.

Create a system API key:
1. Go to **Settings** → **System keys**
2. Create a new key (copy it immediately — it is shown only once)
3. Add it to `backend/.env`:

```bash
echo 'PHOENIX_API_KEY=<paste-the-key-here>' >> backend/.env
```

### Step 4: Enable tracing and verify

Set tracing on and restart the API and worker:

```bash
cd backend
# Edit .env and set TRACE_ENABLED=true
echo 'TRACE_ENABLED=true' >> .env
docker compose -f docker-compose.prod.yml up -d api celery_worker
```

Verify that one upload and one chat question each produce a complete trace:
1. Upload a paper or document
2. Ask a question about it
3. Go to `https://9xaipal-trace.kl1.site` and check that the traces appear

Both traces should be visible in the Phoenix UI with full span details.

## Disabling tracing

If tracing needs to be turned off temporarily (e.g., for performance tuning or to save storage), set the kill switch:

```bash
cd backend
echo 'TRACE_ENABLED=false' >> .env
docker compose -f docker-compose.prod.yml up -d api celery_worker
```

The Phoenix service stays running. Existing traces remain visible. The app stops recording new traces immediately.

## Rotating the Phoenix API key

If the API key is compromised or rotated regularly:

1. Create a new key in Phoenix Settings → System keys
2. Update `backend/.env`:

```bash
cd backend
# Edit .env and update PHOENIX_API_KEY
sed -i '' 's/^PHOENIX_API_KEY=.*/PHOENIX_API_KEY=<new-key>/' .env
```

3. Restart the API and worker:

```bash
docker compose -f docker-compose.prod.yml up -d api celery_worker
```

## Changing retention policy

To keep traces for longer (or shorter), edit the retention days in `backend/.env`:

```bash
PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=30  # Keep traces for 30 days
```

Restart Phoenix:

```bash
docker compose -f docker-compose.prod.yml up -d phoenix
```

The retention policy applies to new traces. Existing traces older than the new retention period are deleted by Phoenix's background cleanup job.

## Where the data lives

Traces are stored in the `phoenix` database on the same Postgres instance that holds the app's main database (`9xaipal`). Both databases share a single volume (`9xaipal_postgres_data`).

To back up traces:

```bash
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U 9xaipal phoenix | gzip > phoenix-backup.sql.gz
```

To restore:

```bash
docker compose -f docker-compose.prod.yml exec -T postgres sh -c \
  'gunzip < phoenix-backup.sql.gz | psql -U 9xaipal'
```

## Resource limits

Phoenix runs with:
- **Memory:** 512 MB limit
- **CPU:** 1.0 core limit
- **Storage:** Grows with trace retention (14 days → ~100 MB; adjust `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` if storage becomes an issue)

Monitor Phoenix's resource usage:

```bash
docker stats 9xaipal-phoenix
```

## Verification

Pending Task 7 results.
