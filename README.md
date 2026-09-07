# Statlock

Statlock is a self-hosted statistics tracker for [Deadlock](https://store.steampowered.com/app/1422450/Deadlock/),
built around one goal: **helping you actually improve**. Instead of dumping raw
winrates, it treats your match history as a small statistical sample and is
honest about what that sample can and cannot say: matchup and item verdicts use
Wilson confidence intervals and Bayesian shrinkage toward population baselines,
and anything without enough games says "not enough data" instead of pretending
five matches are a trend. It runs as a small web app (in-app title: *Deadlock
Stat Tracker*) that continuously ingests your matches and turns them into
evidence you can act on.

<!-- screenshots: drop 1-2 images here before publishing, e.g.
![Overview](docs/img/overview.png)
![Heroes](docs/img/heroes.png)
-->

## Before you start: get your matches ingested

Statlock reads match data from [deadlock-api.com](https://deadlock-api.com), and
deadlock-api can only serve full metadata for matches whose identifiers have
been submitted to it. Players make sure their own matches are covered by running
the community's [deadlock-api-ingest](https://github.com/deadlock-api/deadlock-api-ingest)
tool, a lightweight background app that watches Steam's local HTTP cache and
submits match IDs and salts as you play. If you prefer not to run it, there is a
web-upload alternative at <https://deadlock-api.com/ingest-cache>. Without this,
older or uncovered matches show up in Statlock as pending or unavailable rather
than fully analyzed.

## What's inside

- **Overview**: rank over time and recent matches, with sync status at a glance.
- **Heroes**: your matchups (as and against every hero) and item performance,
  each with a confidence-aware verdict instead of a bare winrate.
- **Performance**: per-hero metrics (KDA, souls, damage, laning) compared
  against the population baseline at your rank, plus trends over time.
- **Insights**: death patterns (who kills you, and when in the game), plus
  session and tilt analysis and how you perform alongside recurring teammates.
- **Improvement**: the honest summary of where the evidence says you're strong
  or weak, and what remains simply unproven.

Any profile is shareable read-only at `/player/<account-id>`; pasted links
unfurl with the player's name and rank badge.

## Stack

Python 3.12 + FastAPI over SQLite, with a separate ingestion worker that drains
match data from deadlock-api.com under a strict rate limit. The frontend is
React + Vite + TypeScript, served by the API from a single origin, so there is
no separate frontend server and no CORS.

## Quickstart (local, single user)

Requires Docker. From the repo root:

```sh
echo DEADLOCK_OPEN_WRITES=1 > .env    # private single-user mode, no login
docker compose up --build
```

Then register your account so the worker has something to ingest:

```sh
docker compose run --rm worker python -m ingest --db /data/tracker.db \
  add-account <account-id-or-SteamID64-or-profile-url> --self
```

The app is at <http://localhost:8000>; the worker backfills your match history
in the background (politely: one API request per 5 seconds). See
[docs/deploy.md](docs/deploy.md) for all environment variables and the
bare-metal setup.

## Deploying for real

For a public VPS deployment (Steam login, HTTPS via Caddy, Litestream backups,
and a full runbook), see [docs/deploy-production.md](docs/deploy-production.md).

## Credits

All match data comes from the excellent community-run
**[deadlock-api.com](https://deadlock-api.com)**; this project would not exist
without it. Please be considerate of their service if you self-host.

Statlock is a fan project. It is **not affiliated with Valve Corporation** or
with deadlock-api.com. Deadlock and its assets are trademarks and/or copyrights
of Valve Corporation.

## License

[MIT](LICENSE).
