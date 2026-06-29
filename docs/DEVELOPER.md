# Zeno — Developer Guide

Technical reference for engineers deploying, extending, and operating the Zeno container dashboard.

The application version is defined in `server/app.py` as `APP_VERSION`. The current **stable release is 2.0**.

## Release history

| Release | Previous version | What was already there | What is new |
|---------|------------------|------------------------|-------------|
| **1.0** | — | — | Flask app + Docker socket; `GET/POST` container APIs; static grouping; env basic auth; single `index.html` |
| **1.1** | 1.0 | 1.0 stack | `OPEN_LINKS` quick-open; compose integration fixes; container ordering |
| **2.0** *(stable)* | 1.1 | Monolithic dashboard | MongoDB persistence; REST API v1; multi-user RBAC; tier/feature gating; provisioning; metrics collector; alerts; timeline; modular frontend; admin tooling |

---

## Table of contents

1. [Repository layout](#repository-layout)
2. [Runtime architecture](#runtime-architecture)
3. [Quick start and configuration](#quick-start-and-configuration)
4. [Authentication and sessions](#authentication-and-sessions)
5. [Authorization model](#authorization-model)
6. [Data model (MongoDB)](#data-model-mongodb)
7. [Background monitoring](#background-monitoring)
8. [Container labeling and grouping](#container-labeling-and-grouping)
9. [API reference](#api-reference)
10. [Frontend architecture](#frontend-architecture)
11. [Extension guide](#extension-guide)
12. [Build and deployment](#build-and-deployment)
13. [Security considerations](#security-considerations)

---

## Repository layout

```
zeno/
├── docker-compose.yml              # Dashboard service (port 9090, Docker socket)
├── docker-compose.mongo.yml          # MongoDB 7 overlay (zeno_mongo)
├── compose-snippet.yml             # Embed dashboard in an existing compose stack
├── server/
│   ├── app.py                      # Flask application, Docker integration, collector thread
│   ├── db.py                       # MongoDB access layer, business logic, schemas
│   ├── Dockerfile                  # Python 3.12 slim image; static assets baked in
│   ├── requirements.txt            # Pinned Python dependencies
│   └── static/
│       ├── index.html              # Main SPA shell (dashboard views)
│       ├── login.html              # Login / registration
│       ├── settings.html           # Settings page
│       ├── manage-users.html       # Admin user management
│       ├── profile.html            # User profile
│       ├── css/                    # base, dashboard, login, pages
│       └── js/                     # app, login, settings, manage-users
├── docs/
│   ├── USER.md                     # End-user documentation
│   └── DEVELOPER.md                # This file
└── README.md
```

---

## Runtime architecture

```
┌─────────────┐     HTTPS/HTTP      ┌──────────────────┐
│   Browser   │ ◄─────────────────► │  zeno_dashboard  │
│  (static +  │   session cookie    │  Flask :9090     │
│   fetch)    │                     │  app.py          │
└─────────────┘                     └────────┬─────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
                    ▼                        ▼                        ▼
           /var/run/docker.sock      zeno_mongo:27017          psutil (host)
           Docker Engine API          MongoDB 7                 metrics
```

### Request flow

1. Static assets and HTML pages are served by Flask (`send_from_directory` / static folder).
2. JSON API calls use prefix `/api/v1` with Flask server-side sessions (`SECRET_KEY`-signed cookie).
3. Container mutations invoke the Docker SDK (`docker.from_env()`), which reads the mounted socket.
4. Persistent state (users, layouts, metrics, alerts) is read/written via `db.py` → PyMongo.

### Process model

| Component | Location | Notes |
|-----------|----------|-------|
| HTTP server | `app.py` | `gunicorn` or Flask dev server in container |
| Metrics collector | `app.py` daemon thread | Started when `METRICS_ENABLED=true`; interval `MONITOR_INTERVAL_SEC` |
| MongoDB init | `db.init_db()` | Called at startup; seeds admin, indexes, default settings |

---

## Quick start and configuration

```bash
docker compose -f docker-compose.yml -f docker-compose.mongo.yml up -d --build
```

Dashboard: **http://localhost:9090**

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DASHBOARD_USER` | `admin` | Seed admin username; stored with `is_primary: true` |
| `DASHBOARD_PASS` | `admin` | Seed admin password (hashed on insert) |
| `SECRET_KEY` | dev key in compose | Flask session signing; **must** be unique in production |
| `MONGO_URI` | `mongodb://zeno:zenopass@zeno_mongo:27017/zeno?authSource=admin` | MongoDB connection string |
| `MONGO_DB` | `zeno` | Database name |
| `APP_TIER` | `Core` | Default tier for migrations and new seed users |
| `METRICS_ENABLED` | `true` | Enable background metrics + alert collector |
| `MONITOR_INTERVAL_SEC` | `60` | Collector sleep interval (seconds) |

### Health check

`GET /api/info` returns `{ "name": "Zeno", "version": "2.0" }` — used by Docker `healthcheck` in `docker-compose.yml`.

---

## Authentication and sessions

### Login flow

```
POST /api/v1/login  { username, password }
  → verify password_hash (werkzeug pbkdf2:sha256)
  → session["username"], session["role"], session["tier"]
  → 200 + user payload
```

### Registration

```
POST /api/v1/register  { username, password }
  → role: "user", tier: DEFAULT_TIER (Core)
  → alert_notifications: all enabled
```

### Session storage

- Server-side Flask session (signed cookie).
- Client uses `fetch(..., { credentials: "include" })`.
- Unauthenticated API access returns **401**; frontend redirects to `/login`.

### Password change

- Self-service: `POST /api/v1/account/password` with `current_password` + `new_password`.
- Admin reset: `PATCH /api/v1/users/<username>/password`.

---

## Authorization model

### Roles

| Role | `role` value | Scope |
|------|--------------|-------|
| Administrator | `admin` | All containers, users, settings, tier features |
| User | `user` | Own containers; Core Apps view-only |

### Container-level permissions

`can_manage_container_group(group_name)` in `app.py`:

- **Admins**: always `true`.
- **Users**: `false` when `group === "Core Apps"`; `true` otherwise.

Exposed to the frontend as `can_manage` on each item from `GET /api/v1/containers`. The UI hides lifecycle buttons, delete, and terminal when `can_manage` is false.

### Feature gating (edition tiers)

Non-admin users require tier-enabled features for create endpoints:

| Feature key | Endpoint |
|-------------|----------|
| `create_database` | `POST /api/v1/databases` |
| `create_ubuntu` | `POST /api/v1/servers/ubuntu` |
| `create_web_server` | `POST /api/v1/servers/web` |

Enforced via `@require_feature("...")` decorator. Admins bypass all feature checks.

Feature matrix stored in `settings` document `key: tier_features`. Defaults in `db.default_tier_features()`.

### Primary admin

The seed user (`DASHBOARD_USER`) receives `is_primary: true` and `tier: Elite`:

- Cannot be deleted (`delete_user` raises).
- `tier` and `role` cannot change via `PATCH /users/<username>`.
- Excluded from bulk tier updates.
- Password reset still allowed.

---

## Data model (MongoDB)

### Collections overview

| Collection | Purpose | Key indexes |
|------------|---------|-------------|
| `users` | Authentication, role, tier, notifications | — |
| `settings` | App tier default, alert thresholds, tier features | `key` |
| `activity_log` | Audit trail | `username+ts`, `ts` (TTL none — permanent) |
| `group_layouts` | Per-user dashboard layout | `username` (unique) |
| `metrics_history` | Time-series snapshots | `container+ts`, `ts` (TTL 7 days) |
| `alerts` | Alert events | `container+ts`, `rule+container+resolved` |

### `users`

```json
{
  "username": "alice",
  "password_hash": "pbkdf2:sha256:...",
  "role": "user",
  "tier": "Core",
  "is_primary": false,
  "alert_notifications": {
    "cpu_high": true,
    "mem_high": true,
    "crash_loop": true,
    "port_failure": true
  },
  "created_at": "2026-06-29T12:00:00+00:00",
  "created_by": "admin"
}
```

### `group_layouts`

```json
{
  "username": "alice",
  "layout": {
    "groups": [
      { "id": "core-apps", "name": "Core Apps", "order": 0, "locked": true },
      { "id": "my-databases", "name": "My Databases", "order": 1 }
    ],
    "assignments": { "zeno_userdb_app": "my-databases" },
    "container_order": { "my-databases": ["zeno_userdb_app"] }
  }
}
```

- `core-apps` is always locked; core container assignments are enforced on save (`db.save_group_layout`).
- Layout is merged with live container list on `GET /groups/layout`.

### `metrics_history`

```json
{
  "container": "zeno_userdb_app",
  "ts": "2026-06-29T14:00:00+00:00",
  "cpu": 12.5,
  "mem_used_mb": 128.0,
  "mem_limit_mb": 512.0,
  "mem_percent": 25.0,
  "block_read_bytes": 4096,
  "block_write_bytes": 0
}
```

Host snapshots use `container: "__host__"` (`HOST_METRICS_CONTAINER` in `db.py`).

TTL: `METRICS_TTL_SECONDS` = 7 days via MongoDB TTL index on `ts`.

### `alerts`

```json
{
  "rule": "cpu_high",
  "container": "zeno_web_app",
  "message": "CPU above 90% (92.1%) on zeno_web_app",
  "severity": "warning",
  "cpu_percent": 92.1,
  "mem_percent": 45.2,
  "ts": "2026-06-29T14:00:00+00:00",
  "resolved": false,
  "resolved_at": null
}
```

**Rules:** `cpu_high`, `mem_high`, `crash_loop`, `port_failure`.

**Dedup:** `find_unresolved_alert()` suppresses duplicate inserts within `ALERT_DEDUP_MINUTES` (15) per `rule+container` while unresolved.

### `activity_log`

```json
{
  "username": "alice",
  "action": "create",
  "container": "zeno_ubuntu_dev",
  "container_image": "ubuntu:24.04",
  "details": "ubuntu:python,go",
  "ts": "2026-06-29T14:00:00+00:00"
}
```

Actions: `create`, `delete`, `start`, `stop`, `restart`, `exec`, `state_change`.

---

## Background monitoring

Daemon thread (`_metrics_collector_loop` in `app.py`):

```
every MONITOR_INTERVAL_SEC:
  1. Sample host stats (psutil) → metrics_history (__host__)
  2. For each running container:
       a. Sample Docker stats → metrics_history
       b. Detect status transitions → activity_log (state_change)
       c. Evaluate alert rules (_check_container_alerts)
  3. Evaluate host memory threshold (optional host-level mem_high)
```

### Alert evaluation logic

| Rule | Trigger | Resolve |
|------|---------|---------|
| `cpu_high` | CPU > threshold for 2 consecutive samples | CPU < threshold − 10% |
| `mem_high` | Memory % > threshold for 2 consecutive samples | Memory % < threshold − 10% |
| `crash_loop` | Status `restarting` for 2+ consecutive samples | Status leaves restarting |
| `port_failure` | TCP connect to `127.0.0.1:hostPort` fails while running | Port becomes reachable |

Thresholds loaded from `settings.alert_thresholds` (cached 30s in-process). Defaults: CPU 90%, memory 90%.

### Notification preferences

Per-user `alert_notifications` dict filters dashboard badge/banner on the frontend only. The Alerts API returns all alerts regardless of preferences.

---

## Container labeling and grouping

User-created containers receive Docker labels:

| Label | Example | Purpose |
|-------|---------|---------|
| `zeno.app.kind` | `user-db`, `user-server`, `user-web` | Provisioning category |
| `zeno.created_by` | `alice` | Owning user |
| `zeno.version` | `2.0` | Provisioning version |

### Name prefixes

| Kind | Prefix | Default group |
|------|--------|---------------|
| Database | `zeno_userdb_` | My Databases |
| Ubuntu | `zeno_ubuntu_` | My Servers |
| Web | `zeno_web_` | My Web Servers |

### Core Apps

`CORE_APP_NAMES = {"zeno_dashboard", "zeno_mongo"}` — always grouped under **Core Apps**, view-only for normal users.

### OPEN_LINKS

Map in `app.py` for quick **Open ↗** when host port is known:

```python
OPEN_LINKS = {
    "zeno_web_myapp": 8080,
}
```

---

## API reference

**Base path:** `/api/v1`  
**Auth:** session cookie (`credentials: include`)  
**Content-Type:** `application/json` for request bodies  
**Errors:** `{ "error": "message" }` with appropriate HTTP status

### Auth and account

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/login` | — | `{ username, password }` → session |
| POST | `/logout` | ✓ | Clear session |
| POST | `/register` | — | Public signup → `user` role |
| GET | `/me` | ✓ | User, tier, `is_admin`, `features`, `alert_notifications` |
| GET | `/profile` | ✓ | Profile payload |
| POST | `/account/password` | ✓ | `{ current_password, new_password }` |
| PUT | `/account/tier` | admin | Set own personal edition `{ tier }`; blocked for primary |
| PUT | `/account/notifications` | ✓ | `{ alert_notifications: { cpu_high: bool, ... } }` |

### Containers

| Method | Path | Description |
|--------|------|-------------|
| GET | `/containers` | All containers; `can_manage`, `group`, `group_id`, `created_by` |
| POST | `/containers/<name>/<action>` | `start` \| `stop` \| `restart` |
| DELETE | `/containers/<name>` | Remove container (must be stopped) |
| GET | `/containers/<name>/logs` | `?tail=200` |
| GET | `/containers/<name>/stats` | Live CPU, memory, network, block I/O |
| POST | `/containers/<name>/exec` | `{ command }` → `{ output, cwd }` |

**Exec cwd:** per `(username, container)` dict `_terminal_cwd` in `app.py`. `cd` and `pwd` handled server-side.

### Observability

| Method | Path | Query params | Description |
|--------|------|--------------|-------------|
| GET | `/metrics/history` | `container`, `hours` | Time-series points |
| GET | `/timeline` | `hours`, `limit` | Merged activity, state, alerts |
| GET | `/activity/me` | `limit` | Current user's audit log |
| GET | `/alerts` | `hours`, `active_only`, `containers_only` | Alert list + thresholds |
| GET | `/alerts/thresholds` | — | `{ cpu_percent, mem_percent }` |
| PUT | `/alerts/thresholds` | — | `{ cpu_percent, mem_percent }` |
| GET | `/logs/central` | `containers`, `search`, `tail` | Multi-container log search |

### Host

| Method | Path | Description |
|--------|------|-------------|
| GET | `/host/stats` | CPU %, memory %, disk %, load average |
| GET | `/host/details` | OS, kernel, Docker version, disks |

### Provisioning

Requires `@require_feature` unless admin.

| Method | Path | Body |
|--------|------|------|
| GET | `/databases` | List user databases |
| POST | `/databases` | `engine`, `name`, `host_port`, credentials, `tables`, `persistent` |
| DELETE | `/databases/<name>` | `?remove_volume=true` |
| POST | `/servers/ubuntu` | `name`, `languages[]`, `persistent` |
| POST | `/servers/web` | `name`, `type`, `host_port?`, `persistent` |

### Group layouts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/groups/layout` | User layout + container list for editor |
| PUT | `/groups/layout` | `{ layout: { groups, assignments, container_order } }` |
| POST | `/groups` | `{ name }` → new group |
| DELETE | `/groups/<id>` | Delete group; reassigns containers |

### Users and admin

| Method | Path | Description |
|--------|------|-------------|
| GET | `/users` | All users with stats |
| GET | `/users/dashboard` | Summary + recent activity |
| POST | `/users` | Create `{ username, password, role, tier }` |
| PATCH | `/users/<username>` | `{ tier?, role? }` |
| PATCH | `/users/<username>/password` | `{ password }` |
| DELETE | `/users/<username>` | Delete user |
| POST | `/users/bulk` | `{ action: "delete"\|"set_tier", usernames[], tier? }` |
| GET | `/activity` | `?username=&limit=&skip=` |
| GET | `/admin/tier-features` | Feature matrix |
| PUT | `/admin/tier-features` | `{ tier_features: { ... } }` |
| GET | `/settings` | App info, tier, notifications metadata |
| PUT | `/settings/tier` | Set default tier for new registrations |

### Example: fetch containers

```bash
curl -s -b cookies.txt http://localhost:9090/api/v1/containers | jq '.[0]'
```

```json
{
  "name": "zeno_userdb_app",
  "status": "running",
  "image": "postgres:16",
  "group": "My Databases",
  "group_id": "my-databases",
  "can_manage": true,
  "created_by": "alice",
  "ports": ["5432:5432"]
}
```

---

## Frontend architecture

No bundler or framework. Views are sections toggled via `data-view` navigation in `index.html`.

| File | Responsibility |
|------|----------------|
| `app.js` | Dashboard, host charts, timeline, central logs, alerts, groups editor, tier features, create flows, terminal, alert badge |
| `manage-users.js` | Admin user table, bulk ops, activity drawer |
| `login.js` | Login and registration |
| `settings.js` | Settings, password, notifications, admin edition |
| `dashboard.css` | Main shell, components, charts, forms (`ui-input`, `ui-select`) |
| `pages.css` | Standalone pages (settings, manage-users) |

### Key client state (`app.js`)

| Variable | Purpose |
|----------|---------|
| `groupLayout` | Cached layout from `/groups/layout` |
| `alertNotificationPrefs` | Per-user alert visibility for dashboard badge |
| `containerLiveHistory` | In-browser buffer merged with API history for live charts |
| `hostChartMode` | `"live"` \| `"24h"` for host view |

### Polling intervals

| Timer | Interval | Target |
|-------|----------|--------|
| Host stats | 1s | `/host/stats` |
| Container list | 10s | `/containers` |
| Open row stats | 1s | `/containers/<name>/stats` |
| Alerts | 30s | `/alerts?active_only=true` |

---

## Extension guide

### Add a gated feature

1. Add key to `FEATURE_KEYS` / `FEATURE_LABELS` in `db.py`.
2. Set defaults in `default_tier_features()`.
3. Decorate route: `@require_feature("your_key")`.
4. Add nav item in `index.html` with `id="nav-create-..."`.
5. Map in `applyFeatureNav()` in `app.js`.
6. Tier Features editor picks up keys automatically from API.

### Add a web server type

1. Add image and default port to `WEB_SERVER_DEFAULTS` in `app.py`.
2. Add option in `#web-type-grid` in `index.html`.
3. Rebuild image.

### Add an alert rule

1. Add rule to `ALERT_NOTIFICATION_RULES` and `ALERT_NOTIFICATION_LABELS` in `db.py`.
2. Implement evaluation in `_check_container_alerts()` in `app.py`.
3. Add filter chip on Alerts page and notification toggle in settings.
4. Add label to `ALERT_RULE_LABELS` and `alertsVisibleOnDashboard()` defaults in `app.js`.

### Change Core Apps

Edit `CORE_APP_NAMES` and grouping in container serialization (`serialize()` / group assignment logic in `app.py`).

---

## Build and deployment

### Rebuild after code changes

```bash
docker compose -f docker-compose.yml -f docker-compose.mongo.yml up -d --build dashboard
```

Static assets are **copied into the image** at build time — there is no bind mount for `static/` in production compose.

### Production checklist

- [ ] Set strong `DASHBOARD_PASS`, `SECRET_KEY`, MongoDB credentials
- [ ] Place reverse proxy with TLS in front of port 9090
- [ ] Restrict network access to dashboard and MongoDB
- [ ] Treat Docker socket mount as root-equivalent
- [ ] Review `activity_log` for unexpected `exec` commands
- [ ] Limit admin accounts; protect primary admin credentials
- [ ] Set `METRICS_ENABLED` intentionally (collector adds steady Docker API load)

### Dependencies (`requirements.txt`)

Pin versions in the file. Rebuild image after any dependency change. Run `pip audit` or equivalent in CI if available.

---

## Security considerations

| Risk | Mitigation |
|------|------------|
| Docker socket access | Non-root container user where possible; `no-new-privileges`; never expose dashboard publicly without auth |
| Session hijacking | `HttpOnly` cookies; use HTTPS; rotate `SECRET_KEY` |
| Password storage | Werkzeug `generate_password_hash` (pbkdf2:sha256) |
| Container exec | Logged to `activity_log`; restricted by `can_manage` |
| Input injection | Commands passed to Docker exec API; validate container names on routes |
| MongoDB exposure | Do not publish port 27017; use compose internal network |

---

End-user documentation: [USER.md](USER.md)

Project overview: [README.md](../README.md)
