# Host-installed files

Things the production box needs *outside* the containers. Like
`backend/nginx/9xaipal.conf`, they are kept here so they are versioned and
reviewed, but nothing in `deploy.yml` can install them — they need `sudo`, by
hand, once (and again when a file here changes; the deploy prints a notice
when one does). Background and the incident that produced them:
`DEPLOYMENT-PRODUCTION.md` §9–10.

| File | Installed as | What it does |
|---|---|---|
| `disk-watchdog` | `/usr/local/sbin/disk-watchdog` | Every 15 min: email at 80 % (warning) and 90 % (critical) root-disk usage, a daily reminder while it stays there, one mail on recovery. `--status` prints the level, `--test` sends a test mail. |
| `disk-watchdog.cron` | `/etc/cron.d/disk-watchdog` | The schedule; logs to `/var/log/disk-watchdog.log`. |
| `disk-watchdog.env.example` | `/etc/disk-watchdog.env` (0600) | SMTP settings. Copy, fill in `SMTP_PASS` (a Gmail App Password). |
| `docker-prune.weekly` | `/etc/cron.weekly/docker-prune` | The deploy's own image/build-cache cleanup, for weeks with no deploy. |
| `docker-daemon.json` | `/etc/docker/daemon.json` | Bounds BuildKit's cache: **`maxUsedSpace` 28 GB** is the ceiling, `reservedSpace` 18 GB the floor it prunes back to (see §9 — `reservedSpace` alone is *not* a cap). Needs `systemctl restart docker` — `systemctl reload docker` does **not** apply builder GC — and that restarts every container on the box. |

```bash
cd backend/host
sudo install -m 755 disk-watchdog /usr/local/sbin/disk-watchdog
sudo install -m 644 disk-watchdog.cron /etc/cron.d/disk-watchdog
sudo install -m 755 docker-prune.weekly /etc/cron.weekly/docker-prune
sudo install -m 600 disk-watchdog.env.example /etc/disk-watchdog.env   # then edit SMTP_PASS
sudo install -m 644 docker-daemon.json /etc/docker/daemon.json && sudo systemctl restart docker
sudo disk-watchdog --test    # a mail should arrive within seconds
```

The application side of the same incident — refusing ingestions at 90 %
(`ingestion_disk_refuse_percent`, HTTP 507) — lives in
`app/services/ingestion.py::check_disk_headroom` and needs nothing here.
