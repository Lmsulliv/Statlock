# Production deployment runbook (single VPS)

This is the step-by-step for putting the tracker on a public host with HTTPS and
continuous backups. It targets a single **Hetzner CX22** (2 vCPU / 4 GB / 40 GB),
but any small Ubuntu VPS works. For local/dev usage stick with
[deploy.md](deploy.md) — this document only covers production concerns (TLS,
backups, firewall, restore drills).

The production stack is [docker-compose.prod.yml](../docker-compose.prod.yml):

| Service | Role |
|---------|------|
| `caddy` | HTTPS reverse proxy on :80/:443, automatic Let's Encrypt certs |
| `api` | FastAPI serving the JSON API + built SPA on one origin (not published to the host) |
| `worker` | ingestion daemon |
| `litestream` | streams the SQLite WAL to S3-compatible storage continuously |

> **New to some of these terms?**
> - **Reverse proxy** — a server that sits in front of your app, terminates TLS
>   (HTTPS), and forwards requests to it. Caddy also fetches and renews the
>   certificate for you.
> - **Sidecar** — a helper container that runs beside the app and shares a volume
>   with it. Litestream is a sidecar: it watches the database file and backs it
>   up without the app knowing it exists.
> - **WAL (write-ahead log)** — SQLite's journal of recent writes. Litestream
>   ships the WAL as it grows, which is why a crash loses at most a second or two.

---

## 1. Create the server

1. In the Hetzner Cloud console, create a project, then a server:
   - **Image:** Ubuntu 24.04
   - **Type:** CX22 (shared vCPU is plenty for one user)
   - **SSH key:** add your public key at creation (paste `~/.ssh/id_ed25519.pub`).
     Prefer this over a root password.
2. Note the server's public IPv4 address. SSH in as root:
   ```sh
   ssh root@<SERVER_IP>
   ```
3. Create a non-root user and give it sudo + Docker access later:
   ```sh
   adduser deadlock
   usermod -aG sudo deadlock
   # Copy your SSH key so you can log in as the new user:
   rsync --archive --chown=deadlock:deadlock ~/.ssh /home/deadlock
   ```
4. Harden SSH (optional but recommended): edit `/etc/ssh/sshd_config` to set
   `PermitRootLogin no` and `PasswordAuthentication no`, then
   `systemctl restart ssh`. Confirm you can still log in as `deadlock` in a
   **second** terminal before closing the first.

From here on, work as `deadlock`: `ssh deadlock@<SERVER_IP>`.

## 2. Point DNS at the server

Create an **A record** for your domain (e.g. `stats.example.com`) pointing at
`<SERVER_IP>`. Caddy needs this resolvable **before** first boot — it proves
domain ownership to Let's Encrypt over HTTP on port 80. Verify propagation:

```sh
dig +short stats.example.com    # should print <SERVER_IP>
```

Wait until it does before deploying, or cert issuance will fail (Caddy retries,
so it self-heals once DNS is correct, but it's cleaner to wait).

## 3. Install Docker + the compose plugin

```sh
# Docker's official convenience script (Ubuntu 24.04):
curl -fsSL https://get.docker.com | sudo sh
# Let the deadlock user run docker without sudo:
sudo usermod -aG docker deadlock
# Log out and back in so the group membership takes effect, then verify:
docker compose version
```

## 4. Firewall: only 22, 80, 443

```sh
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 80/tcp      # HTTP (Caddy; ACME challenge + redirect to HTTPS)
sudo ufw allow 443/tcp     # HTTPS
sudo ufw enable
sudo ufw status
```

> **Note on Docker + ufw:** Docker normally manipulates iptables directly and can
> publish container ports *past* ufw. This stack is safe because the **only**
> service that publishes to the host is Caddy (80/443) — the `api` port 8000 is
> deliberately not published (it's reachable only on the internal compose
> network). So there is nothing else exposed for ufw to need to block. Do not add
> a host port mapping to `api` in prod.

## 5. Clone and first deploy

```sh
git clone <YOUR_REPO_URL> deadlock-tracker
cd deadlock-tracker
cp .env.example .env
nano .env          # fill in the values — see below
```

Minimum production `.env`:

```dotenv
DOMAIN=stats.example.com
DEADLOCK_BASE_URL=https://stats.example.com   # turns Steam login on (writes protected)
STEAM_API_KEY=...                             # optional; persona display names
DEADLOCK_API_KEY=...                          # optional; sent to deadlock-api if you have a key
# DEADLOCK_REQUESTS_PER_SECOND=0.2            # optional; default 1 req/5 s -- raise ONLY with maintainers' blessing
# Litestream (see step 8 to create the bucket/keys):
LITESTREAM_BUCKET=deadlock-tracker-backups
LITESTREAM_ENDPOINT=https://s3.us-west-004.backblazeb2.com
LITESTREAM_ACCESS_KEY_ID=...
LITESTREAM_SECRET_ACCESS_KEY=...
```

> **Do NOT set `DEADLOCK_OPEN_WRITES` on a public deploy.** It opens every write
> with no login. Use `DEADLOCK_BASE_URL` (Steam login) instead. With neither set,
> writes fail closed (403) — a safe default, but then nobody can add accounts.

Build and start everything detached:

```sh
docker compose -f docker-compose.prod.yml up -d --build
```

Watch it come up. Caddy will fetch a certificate within a few seconds of the
first HTTPS request; `api` runs its DB migration on boot.

```sh
docker compose -f docker-compose.prod.yml ps      # STATUS should show (healthy)
docker compose -f docker-compose.prod.yml logs -f caddy   # watch cert issuance
```

Both `api` and `worker` should read `healthy` within a minute or two (the api
after its migration, the worker after its first daemon loop writes the
heartbeat). Then load `https://stats.example.com` in a browser — you should see
the SPA, and `https://stats.example.com/api/sync-status` should return JSON.

## 6. Confirm Steam login works

With `DEADLOCK_BASE_URL` set to your `https://` origin, the site requires a Steam
login for writes. Click **Log in** in the app and complete the Steam OpenID flow;
you should be redirected back to `https://stats.example.com/api/auth/callback` and
land logged in. If the redirect fails, the usual cause is `DEADLOCK_BASE_URL` not
exactly matching the browser's URL (scheme, host, no trailing slash).

## 7. Add the first account

Register the account to track (accepts an account id, a SteamID64, or a profile
URL). Run it as a one-off container against the running stack:

```sh
docker compose -f docker-compose.prod.yml run --rm worker \
  python -m ingest --db /data/tracker.db add-account <id-or-SteamID64-or-url> --self
```

The daemon picks up brand-new accounts within one loop, so match data starts
appearing within a minute (subject to the 1-request-per-5-seconds API budget).
Check progress:

```sh
docker compose -f docker-compose.prod.yml run --rm worker \
  python -m ingest --db /data/tracker.db status
```

## 8. Backups: set up Litestream and drill the restore

### Create the bucket and keys

Using Backblaze B2 (Cloudflare R2 is equivalent — swap the endpoint):

1. Create a **private** bucket, e.g. `deadlock-tracker-backups`.
2. Create an **application key** scoped to that bucket. Copy the keyID and the
   secret into `.env` as `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY`.
3. Set `LITESTREAM_ENDPOINT` to your bucket's S3 endpoint
   (`https://s3.<region>.backblazeb2.com` for B2,
   `https://<account-id>.r2.cloudflarestorage.com` for R2).
4. Recreate the litestream service to pick up the values:
   ```sh
   docker compose -f docker-compose.prod.yml up -d litestream
   docker compose -f docker-compose.prod.yml logs litestream   # should show "replicating"
   ```

### Verify replication

```sh
docker compose -f docker-compose.prod.yml exec litestream \
  litestream snapshots -config /etc/litestream.yml /data/tracker.db
```

You should see at least one snapshot with a recent timestamp. If the list is
empty or the logs show auth errors, fix the credentials/endpoint before relying
on the backup.

### Restore drill (do this at least once — a backup you've never restored is a guess)

Restore into a **scratch** path inside the litestream container, verify it, then
delete it. This never touches the live database:

```sh
# 1. Restore the latest replicated state to a scratch file.
docker compose -f docker-compose.prod.yml exec litestream \
  litestream restore -config /etc/litestream.yml -o /data/restore-drill.db /data/tracker.db

# 2. SQLite's own integrity check must print "ok".
docker compose -f docker-compose.prod.yml exec api \
  python -c "import sqlite3; print(sqlite3.connect('/data/restore-drill.db').execute('PRAGMA integrity_check').fetchone()[0])"

# 3. The app's own status command must run against it (schema + data are sane).
docker compose -f docker-compose.prod.yml exec api \
  python -m ingest --db /data/restore-drill.db status

# 4. Clean up the scratch file.
docker compose -f docker-compose.prod.yml exec litestream rm /data/restore-drill.db
```

If step 2 prints `ok` and step 3 lists your accounts and queue, the backup is
good.

### Disaster recovery (rebuilding on a fresh server or volume)

If the server or the `deadlock-data` volume is lost, restore to the **real** path
**before** the api starts (so nothing writes an empty DB first):

```sh
# On the fresh host, after `git clone` + filling in .env with the SAME Litestream
# credentials, create the volume and restore into it WITHOUT starting the app:
docker volume create deadlock-tracker_deadlock-data   # name = <project>_<volume>

# Run a one-off litestream container to restore straight onto the volume:
docker compose -f docker-compose.prod.yml run --rm --no-deps litestream \
  litestream restore -config /etc/litestream.yml -o /data/tracker.db /data/tracker.db

# Now bring the stack up normally; the api migrates the restored DB (idempotent).
docker compose -f docker-compose.prod.yml up -d --build
```

> The volume name is `<compose-project>_<volume>`. The compose project defaults to
> the directory name (`deadlock-tracker` here). Confirm with
> `docker volume ls | grep deadlock-data`.

## 9. Updating

```sh
cd deadlock-tracker
git pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

Compose recreates only the changed containers. The api re-runs the (idempotent)
migration on boot, so schema changes apply automatically. The database persists
on the `deadlock-data` volume across the whole cycle.

## 10. Monitoring

Point a free uptime pinger (e.g. UptimeRobot, Better Stack) at:

```
https://stats.example.com/api/sync-status
```

It's an always-open read that returns JSON, so a non-200 or a timeout means the
site is down and you get an alert. A 5-minute interval is plenty for a personal
deploy. This is the same endpoint the api container's own healthcheck uses.

## Optional: per-IP rate limiting

The stock `caddy:2-alpine` image has **no** rate-limit handler, so
[deploy/Caddyfile](../deploy/Caddyfile) does not attempt one. If you want to cap
requests per client IP (e.g. to blunt scraping of `/api/`), build a custom Caddy
image that bundles the third-party
[`caddy-ratelimit`](https://github.com/mholt/caddy-ratelimit) module with
`xcaddy`, then add a `rate_limit` directive to the Caddyfile.

Custom image (`deploy/Caddy.Dockerfile`):

```dockerfile
FROM caddy:2-builder AS build
RUN xcaddy build --with github.com/mholt/caddy-ratelimit
FROM caddy:2-alpine
COPY --from=build /usr/bin/caddy /usr/bin/caddy
```

Point the `caddy` service at it with a `build:` block instead of `image:`, then
add inside the site block in the Caddyfile:

```
rate_limit {
	zone api {
		match { path /api/* }
		key    {remote_host}
		events 60
		window 1m
	}
}
```

This is optional and unbuilt by default; the reverse proxy works without it.
