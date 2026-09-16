# Dashboard Predictivo — Interactive Analytics Dashboard + Predictive Agent

A reusable platform for any company that runs high-volume operational
decisions against a partner/marketplace API (pricing, allocation, eligibility)
and wants a live, embeddable dashboard plus a rules-based recommendation
agent on top of it — without depending on a manual BI tool that only
refreshes after someone reconciles a spreadsheet.

It reads from your partner platform's API, stores what it reads, applies a
deterministic rules engine to every decision, and serves both a JSON API and
an embeddable dashboard (iframe-ready) that stays live in real time.

## What it replaces

Most ops teams doing this today either:
1. Review each case by hand against a spreadsheet of rules, or
2. Wait on a static BI report that only reflects yesterday's reconciled data.

This project automates step 1 with auditable, deterministic logic (never a
black-box model for the actual decision), and replaces step 2 with a
dashboard that reads live data and tells you plainly when it disagrees with
a slower, reconciled report — instead of silently showing a different
number.

## Architecture

```
config/            -- project settings, urls, health checks, JSON logging
intake/             -- receives and normalizes incoming records from the partner API
rules_engine/       -- eligibility, profit-floor, exclusion rules, ranking logic
orchestrator/       -- read-only pollers that sync the partner API into local storage,
                       plus the scheduled rollups the dashboard reads for long windows
dashboard/          -- read-only JSON API + embeddable HTML dashboard (metrics, charts,
                       an agent-recommendation popup, active-alarm popup)
audit/              -- decision log: what was decided, why, and with what input data
```

## Governance principles (non-negotiable)

- Nothing is auto-executed against the partner platform until a human
  approves it, or an explicit "auto-safe" threshold has been signed off by
  the business.
- The actual decision (profit %, exclusion rules, ranking) is **deterministic
  Python logic**, fully auditable — it never depends on a model.
- A language model, if used at all, is restricted to two narrow tasks:
  writing a natural-language explanation of a decision, and breaking ties on
  cases the rule engine genuinely can't resolve alone. It never decides the
  outcome.
- Any profit/eligibility floor is configurable but has an absolute minimum
  nobody can bypass without explicit, logged business approval.

## Build plan — standing this up for a new company

The phases below are the order that worked building this the first time;
follow them in sequence for a new deployment rather than building the
dashboard first against no real data.

**Phase 0 — Connect the data source.** Write an `intake/` client against your
partner's real API (auth, pagination, rate limits — confirm all of this
empirically, never assume from docs alone). Land the raw records in your own
models so the rest of the system never depends on the partner API being up.

**Phase 1 — Rules engine.** Encode the actual business rules (eligibility,
profit floor, exclusions, ranking) as deterministic Python — not a model.
Write these against real historical cases before wiring them to anything
live, and get the thresholds confirmed by whoever owns the business decision
today.

**Phase 2 — Orchestrator (read-only pollers).** Background workers that poll
the partner API on an interval and write append-only snapshots. Never let a
poller write back to the partner platform in this phase — read-only until
the rules engine has been validated against real outcomes.

**Phase 3 — Audit log.** Every rules-engine evaluation gets logged with its
inputs and verdict, append-only, before anything downstream reads it. This
is what makes the dashboard's numbers explainable later, and what backtests
the rules against what a human would have decided.

**Phase 4 — Dashboard.** A read-only JSON contract (`/api/dashboard-metrics/`)
consumed by an embeddable single-file HTML dashboard: KPIs, charts, a
period filter (year/month/day, not just a handful of fixed presets), and a
"why does this differ from our other report" note wherever your source data
and a separately-reconciled report can legitimately disagree — don't hide
that gap, explain it.

**Phase 5 — Predictive agent.** A deterministic v1 (e.g. "this supplier has
healthy volume and conversion, propose a 5% CPI reduction, here's the
projected gain") that surfaces as an on-screen popup, never auto-applies,
and always routes to human approval. Swap the deterministic logic for a
model later without changing the popup contract.

**Phase 6 — Production deployment.** Move off a laptop + tunnel setup onto a
real server before calling anything a stable version — see
[Deployment](#deployment) below. A dashboard that goes dark whenever someone's
laptop sleeps isn't production, no matter how correct its numbers are.

**Multi-tenant, later.** This repo is single-tenant by default — one
deployment, one partner-platform credential set, one database. Two ways to
serve multiple companies: (a) one deployment per company (already works
today, zero code changes — just a fresh `.env` and database per company), or
(b) one shared deployment with a `Tenant` model and every query scoped by
tenant (a real architecture project, not a config change) if you want a
single shared instance serving everyone. Don't build (b) until (a) stops
being good enough.

## Local setup

```bash
python -m venv venv
venv/Scripts/activate          # Windows
pip install -r requirements.txt
cp .env.example .env           # fill in your partner API token, API_KEY, etc.
python manage.py migrate
python manage.py runserver     # API + dashboard on http://127.0.0.1:8000
```

Key routes: `/api/docs/` (Swagger), `/api/dashboard-metrics/` and
`/api/business-summary/` (need `X-API-Key: <API_KEY>`), `/dashboard/` (the
embeddable HTML view), `/health/live/`, `/health/ready/`, `/health/worker/`.

Long-running pollers (never write back to the partner platform):

```bash
python manage.py run_allocator --once --limit 5           # quick pass
python manage.py run_earnings_tracker --once --limit 20
python manage.py build_dashboard_rollups --days 3          # daily rollup for fast long-range charts
```

### Embedding the dashboard

`/dashboard/` serves a single-file HTML page designed to run inside an
`<iframe>` on your own platform. Set `DASHBOARD_FRAME_ANCESTORS` (comma-separated
origins) in your environment and the page sends the matching
`Content-Security-Policy: frame-ancestors` header automatically — no CORS
configuration needed since the API and the page share the same origin.

## Environment variables (`.env`, never committed)

Full template with comments: [`.env.example`](.env.example).

| Variable | Use |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django cryptographic key. ≥ 50 chars in production. |
| `DJANGO_DEBUG` | `True` = local (SQLite); `False` = production (Postgres). |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts. No `*` in production. |
| `DJANGO_SECURE_SSL_REDIRECT` / `DJANGO_SESSION_COOKIE_SECURE` / `DJANGO_CSRF_COOKIE_SECURE` | Default to `True` when `DEBUG=False`. |
| Partner API token(s) | Credentials for your upstream partner platform's API — see `.env.example` for the exact variable names and what each endpoint needs. |
| `API_KEY` | Value consumers must send in `X-API-Key` to reach the dashboard/decisions API. Different per environment. |
| `DASHBOARD_FRAME_ANCESTORS` | Comma-separated origins allowed to embed `/dashboard/` in an `<iframe>`. |
| Email / chat alert settings | Optional performance-alert channels — empty disables that channel, never fatal. |
| `DB_ENGINE` / `DB_HOSTNAME` / `DB_PORT` / `DB_NAME` / `DB_USERNAME` / `DB_PASSWORD` / `DB_SSLMODE` | Postgres connection, required when `DEBUG=False`. Empty = SQLite local. |

Generate safe values with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"  # DJANGO_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"  # API_KEY
```

## Deployment

The `deploy/` directory ships Nginx + systemd units for a plain **Ubuntu
24.04 server** — this works the same on any VPS provider (Hetzner,
DigitalOcean, Linode, a bare EC2 instance, etc.), not tied to one cloud.

- Infrastructure checklist (works for any Postgres-reachable host, AWS RDS
  being one option among several): [`docs/AWS_REQUIREMENTS.md`](docs/AWS_REQUIREMENTS.md)
- First deploy, updates, rollback, diagnostics: [`docs/EC2_DEPLOYMENT.md`](docs/EC2_DEPLOYMENT.md)
- Versioned scripts and unit files: [`deploy/README.md`](deploy/README.md)

```bash
cd /opt/<your-app>
sudo SERVER_NAME=<domain> bash deploy/scripts/bootstrap-ubuntu.sh
sudoedit /etc/<your-app>/app.env
sudo bash deploy/scripts/deploy.sh
sudo certbot --nginx -d <domain>
sudo bash deploy/scripts/start-worker.sh run_allocator --hours 24
sudo bash deploy/scripts/start-worker.sh run_earnings_tracker --hours 24
```

A poller that only runs while a laptop happens to be on and a tunnel happens
to be up will lose data during every outage, permanently — there is no
catch-up once the gap has passed. Put the pollers and the database on a
server with a real uptime story before treating any deployment as production.

## Branching (`CONTRIBUTING.md`)

`main` = production, protected. `develop` = staging. Work happens on
`feature/XXX-name` branches off `develop`, merged via reviewed PR. See
`CONTRIBUTING.md` for the full workflow and commit format.
