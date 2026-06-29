# Zeno

A browser-based control plane for local Docker development: container lifecycle management, live and historical metrics, centralized logging, alerting, multi-user access control, and one-click provisioning for databases, Ubuntu sandboxes, and web servers.

The current **stable release is 2.0**. See [Release history](#release-history) below and [docs/USER.md](docs/USER.md) for feature details.

## Documentation

| Guide | Audience |
|-------|----------|
| [docs/USER.md](docs/USER.md) | End users — features, roles, workflows, troubleshooting |
| [docs/DEVELOPER.md](docs/DEVELOPER.md) | Engineers — architecture, API, data model, operations |

## Quick start

Clone the repository and start the stack:

```bash
git clone git@github.com:SangeethRohan/zeno.git
cd zeno
docker compose -f docker-compose.yml -f docker-compose.mongo.yml up -d --build
```

Open **http://localhost:9090** and sign in with **admin** / **admin**. Change these credentials immediately in any shared or production environment.

MongoDB is required for authentication, per-user settings, metrics history, alerts, activity audit logs, and dashboard layouts.

## What Zeno provides

| Area | Capabilities |
|------|----------------|
| **Dashboard** | Grouped container view, start/stop/restart/delete, expandable logs, live stats, 24h CPU/RAM/disk charts, in-container CLI |
| **Host** | System CPU, memory, disk, load average; live sparklines and 24h history mode |
| **Provisioning** | Postgres, MySQL, MongoDB, Redis, Ubuntu dev servers, Nginx/Apache/Caddy/Traefik |
| **Observability** | Timeline (24h), Central Logs (activity + container logs), Alerts with configurable thresholds |
| **Access control** | MongoDB-backed users, admin/user roles, Core/Pro/Elite tiers, feature gating, Core Apps view-only for users |
| **Personalization** | Per-user drag-and-drop group layouts, per-user alert notification preferences |
| **Administration** | User management, bulk tier assignment, tier feature matrix, forensic activity logs |

## Stack

| Component | Technology |
|-----------|------------|
| API & static UI | Python 3.12, Flask 3 |
| Container control | Docker Engine API (`docker` Python SDK) |
| Persistence | MongoDB 7 |
| Host metrics | `psutil` |
| Frontend | Vanilla HTML/CSS/JS (no build step) |

## Security notice

The dashboard mounts `/var/run/docker.sock` and can manage every container on the host. Treat dashboard credentials like root access. Do not expose port **9090** to the public internet without TLS, strong passwords, and network restrictions.

## Repository layout

```
zeno/                               # repository root
├── docker-compose.yml              # Dashboard service (port 9090, Docker socket)
├── docker-compose.mongo.yml        # MongoDB overlay (zeno_mongo)
├── compose-snippet.yml             # Embed dashboard in another compose project
├── server/                         # Flask API + static frontend
│   ├── app.py
│   ├── db.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── static/
├── docs/
│   ├── USER.md
│   └── DEVELOPER.md
└── README.md
```

See [docs/DEVELOPER.md](docs/DEVELOPER.md) for environment variables, API reference, and deployment guidance.

## Release history

| Release | Previous version | What was already there | What is new |
|---------|------------------|------------------------|-------------|
| **1.0** | — | — | Single-page Flask dashboard; Docker socket integration; list/start/stop/restart containers; tail logs; static container grouping; environment-variable basic auth; quick-open links for common dev UIs |
| **1.1** | 1.0 | All 1.0 features | **Open ↗** quick-launch for web-facing containers; compose and container-ordering fixes |
| **2.0** *(stable)* | 1.1 | Container dashboard with open links and basic auth | **Zeno** rebrand; MongoDB-backed multi-user auth and registration; admin/user roles and primary admin protection; Core/Pro/Elite edition tiers with feature gating; provision databases, Ubuntu servers, and web servers from the UI; per-user Manage Groups layouts; Host view with live and 24h charts; container 24h metric history (7-day TTL); background metrics collector; Timeline (operations, state changes, alerts); Central Logs (personal activity audit + group/container log search); Alerts (CPU, memory, crash loop, port failure) with configurable thresholds; per-user notification toggles for dashboard badge/banner; Settings, Profile, Manage Users; tier feature matrix for admins; Docker CLI terminal with persistent cwd; activity logging and admin forensics |
