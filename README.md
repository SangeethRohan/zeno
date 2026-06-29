# Stack Control — local container dashboard

A small dashboard for your dev stack: see every container's status at a
glance, start/stop/restart it, view live logs, and jump straight to its
web UI (Adminer, pgAdmin, n8n, etc.) — all from one page.

It's two pieces:
- `backend/` — a Flask app that talks to the Docker socket (list/start/stop/
  restart containers, fetch logs).
- `backend/static/index.html` — the dashboard page itself, served by the
  same Flask app.

## 1. Drop it next to your existing compose file

Copy the `container-dashboard/` folder into the same directory as your
`docker-compose.yml`, so you have:

```
your-project/
├── docker-compose.yml
└── container-dashboard/
    ├── compose-snippet.yml
    └── backend/
        ├── app.py
        ├── requirements.txt
        ├── Dockerfile
        └── static/index.html
```

## 2. Add the service to your docker-compose.yml

Open `container-dashboard/compose-snippet.yml` and paste its content into
your existing `docker-compose.yml`, under the `services:` block:

```yaml
  dashboard:
    build: ./container-dashboard/backend
    container_name: dev_dashboard
    restart: always
    ports:
      - "9090:9090"
    environment:
      DASHBOARD_USER: admin
      DASHBOARD_PASS: admin
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

You don't need to add anything to the `volumes:` section at the bottom —
this service doesn't need a named volume.

## 3. Build and start it

```bash
docker compose up -d --build dashboard
```

## 4. Open it

```
http://localhost:9090
```

(or `http://<your-server-ip>:9090` if you're running this on a remote box).

You'll see every container from your compose file grouped into
**Databases**, **Tools & UI**, and **Automation**, each with a live status
dot, Start/Stop/Restart buttons, and an "Open ↗" link for anything with a
web UI. Click a row to expand it and see its recent logs.

## Notes on the basic-auth login

`DASHBOARD_USER` / `DASHBOARD_PASS` in the compose file turn on a simple
browser login prompt. Leave both unset (delete those two lines) to disable
auth entirely — fine if this only binds to `localhost` and never leaves
your machine.

## A word on the Docker socket

This dashboard works by mounting `/var/run/docker.sock` into its own
container, which is what gives it the power to start/stop/restart things.
That also means anything running inside that container has effectively
root-level control over your whole Docker host. That's normal for a tool
like this and fine on a local dev machine, but:

- Don't expose port `9090` to the public internet without the basic-auth
  enabled (or a reverse proxy with its own auth in front of it).
- Treat the `DASHBOARD_USER`/`PASS` credentials the same way you'd treat
  root access to the host.

## Customizing

- **Quick-open links**: edit the `OPEN_LINKS` dict at the top of
  `backend/app.py` if you rename containers or change ports.
- **Grouping**: edit the `GROUPS` dict the same way to change which
  section a container shows up under.
- After editing `app.py`, rebuild with:
  ```bash
  docker compose up -d --build dashboard
  ```
