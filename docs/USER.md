# Zeno — User Guide

Zeno is a local container dashboard for managing Docker workloads from your browser — provisioning dev databases and servers, monitoring resource usage, reviewing logs, and responding to alerts without using the command line for routine tasks.

The current **stable release is 2.0**.

## Release history

| Release | Previous version | What was already there | What is new |
|---------|------------------|------------------------|-------------|
| **1.0** | — | — | Browser dashboard for Docker containers; start, stop, restart; view logs; static groups; basic login |
| **1.1** | 1.0 | All 1.0 features | **Open ↗** button to launch web UIs for running containers |
| **2.0** *(stable)* | 1.1 | Basic single-user container dashboard | Full multi-user platform: accounts, editions, provisioning, groups, host metrics, timeline, central logs, alerts, settings, and admin tools (see [Features](#features) below) |

---

## Getting started

1. Start the stack (see [Developer Guide](DEVELOPER.md) for setup).
2. Open **http://localhost:9090**
3. Sign in with your account, or use **Create account** on the login page to register as a normal user.

Default admin credentials on first install: **admin** / **admin** — change these before sharing the host.

---

## Features

### Container dashboard

- View all containers grouped by type or your **custom groups** (from Manage Groups).
- **Start**, **stop**, **restart**, and **delete** containers (when permitted).
- Expand a row for:
  - **Live logs** with tail control
  - **Live stats** — CPU, memory, network, block I/O
  - **24h charts** — CPU %, RAM %, disk I/O (with hover tooltips showing time and value)
  - **Docker CLI** — run commands inside the container (when permitted)
- **Open ↗** — jump to a container’s web UI when a port mapping is available.

The top-bar **Alerts** badge shows how many **enabled** alert types are currently active: **grey** when the count is zero, **red** when one or more. Critical enabled alerts also appear as a banner on the dashboard.

### Host observability

- **Host** sidebar view: CPU, memory, and disk usage with load average.
- Toggle **Live** (sparklines) or **24 hrs** (historical charts from stored metrics).
- Detailed system information: OS, kernel, Python, Docker version, disk layout.

### Timeline

- Unified **24-hour feed** of operations, container state changes, and alerts.
- Filter by **All**, **Operations**, **State**, or **Alerts**.
- Click an event to jump to the related container on the dashboard.

### Central logs

- **Your activity log** — permanent audit trail of your container actions (cannot be deleted).
- **Container logs** — select a **group** (from your saved Manage Groups layout) and **container**, search log lines, choose tail size, and refresh. Choose **All groups** to pick from every container.

### Alerts

Dedicated **Alerts** page for container-only monitoring:

| Alert type | Condition |
|------------|-----------|
| **CPU high** | Container CPU above threshold (default 90%) for two consecutive checks |
| **Memory high** | Container memory above threshold (default 90%) for two consecutive checks |
| **Crash loop** | Container stuck in a restart loop |
| **Port failure** | Published host port unreachable while the container is running |

- Adjust CPU and memory thresholds with **− / +** steppers; click **Save thresholds**.
- Filter the alert list by type.
- Each alert records CPU and memory % at the time it fired.

**Notification preferences** (Settings): toggle each alert type on or off for the **dashboard** badge and banner. Disabled types still appear on the Alerts page.

### Resource provisioning

Create flows appear in the sidebar when your edition tier allows them:

| Flow | What you get |
|------|----------------|
| **Create Database** | Postgres, MySQL, MongoDB, or Redis with optional schema/tables |
| **Create Ubuntu Server** | Dev sandbox; optional language toolchains and samples in `/workspace` |
| **Create Web Server** | Nginx, Apache, Caddy, or Traefik |

New containers appear under the appropriate group (e.g. My Databases, My Servers).

### Manage groups

Each user has their own dashboard layout, persisted to their account:

- **Drag containers** between groups (drop on a group zone or on another container to insert before it).
- **Reorder containers** within a group.
- **Reorder groups** using the **⋮⋮** handle on group headers.
- **Create** custom groups; **delete** non-locked groups.
- **Core Apps** is locked — containers there cannot be moved.
- Click **Save layout** to persist changes.

### Profile and settings

| Page | Purpose |
|------|---------|
| **Profile** | Username, role, edition tier |
| **Settings** | App version, host, database status, change password, notification toggles; admins can set personal edition |
| **Manage Users** | Admin only — user dashboard, create/delete users, bulk tier assignment, activity forensics |
| **Logout** | End session |

---

## Roles and permissions

| Role | Capabilities |
|------|----------------|
| **Admin** | Full container control, user management, tier features, all activity logs, edition settings |
| **User** | Manage own containers; **Core Apps are view only**; create resources allowed by tier |

### Core Apps (view only for normal users)

Containers in **Core Apps** (e.g. `zeno_dashboard`, `zeno_mongo`) are **view only** for normal users — you can see logs and stats but cannot start, stop, restart, delete, or use the Docker CLI on them.

All other containers (your databases, Ubuntu servers, web servers, and the rest of the stack) can be **started, stopped, restarted, deleted**, and controlled via **Docker CLI** when permitted.

---

## Editions (Core / Pro / Elite)

Each user has an edition tier shown next to **Zeno** in the sidebar.

- New registrations receive **Core** by default.
- Admins assign tiers per user under **Manage Users** (row dropdown or bulk **Apply tier**).
- Admins can set their own personal edition under **Settings → Admin edition** (primary admin tier is fixed).

### Tier features (admin)

Under **Tier Features** in the sidebar, admins enable or disable create flows per edition:

| Feature | Description |
|---------|-------------|
| Create Database | Database provisioning flow |
| Create Ubuntu Server | Ubuntu sandbox creation |
| Create Web Server | Web server creation |

Disabled features are hidden from the sidebar and blocked on the API. Admins always have all features.

---

## Manage Users (admin)

Open from **Profile → Manage Users**:

- **Summary cards** — total users, containers created/deleted, operations logged
- **Create user** — username, password, role, tier
- **User table** — multi-select (primary admin excluded)
- **Bulk actions** — apply tier or delete selected users
- **Per-user tier** — change from row dropdown
- **Password reset** — any user, including primary admin
- **Activity log** — click a username to see timestamped operations

### Primary admin account

The seed admin (`DASHBOARD_USER`, usually **admin**) is marked **primary**:

- Cannot be deleted or have tier/role changed
- Only **Password** reset available in the user table
- Shown with a **primary** badge

---

## Settings

| Section | Description |
|---------|-------------|
| **Application** | Product version, host, MongoDB status, edition, role, dashboard URL |
| **Change password** | Requires current password; minimum 4 characters |
| **Notifications** | Per alert-type toggles for dashboard badge/banner (saved immediately) |
| **Admin edition** | Admins (non-primary): set personal Core/Pro/Elite edition |

---

## Creating resources

### Database

Pick engine, name, port (1024–65535), credentials, and optional tables/collections. Containers appear under **My Databases**.

### Ubuntu server

Optional languages install toolchains and sample files under `/workspace/samples`. Use **Open terminal** on the container row.

### Web server

Choose type and port (or leave empty for auto-assignment). Use **Open ↗** when the server is running.

---

## Security notes

- The dashboard mounts the Docker socket and can control the host. Do not expose port 9090 without strong authentication and network restrictions.
- Registration creates **user** role accounts only.
- Change the default admin password after install.
- Use HTTPS in production; sessions use HttpOnly cookies.

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| Port in use | Pick another host port or stop the conflicting service |
| Cannot stop/restart Core App | Normal users: Core Apps are view only; ask an admin |
| Terminal unavailable | Container must be running; Core Apps have no CLI for users |
| Create menu missing | Your tier may not include that feature; ask an admin |
| MongoDB connecting | Ensure `zeno_mongo` is healthy (`docker compose ps`) |
| Wrong password on change | Settings requires the correct current password |
| CPU/memory alert firing | Check container load; alerts resolve when usage drops ~10% below threshold |
| Port failure alert | Verify the service inside the container listens on the published port |
| No metric charts yet | Historical data collects every ~60s; wait a few minutes after first start |
| Logs search empty | Broaden search or increase tail size |
| Alert badge still red after disabling type | Hard-refresh; only **enabled** types count toward the badge |
| Group dropdown empty in Central Logs | Save a layout under **Manage Groups** first |

For technical setup and API details, see [Developer Guide](DEVELOPER.md).
