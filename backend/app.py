import os
import re
import time
import socket
import functools
from flask import Flask, jsonify, request, Response, send_from_directory
import docker
from docker.errors import NotFound, APIError, ImageNotFound
import psutil

app = Flask(__name__, static_folder="static", static_url_path="")
client = docker.from_env()

DASH_USER = os.environ.get("DASHBOARD_USER")
DASH_PASS = os.environ.get("DASHBOARD_PASS")

# Optional quick-open links for containers that expose a web UI.
OPEN_LINKS = {
    "dev_nginx": 8080,
    "dev_adminer": 8081,
    "dev_pgadmin": 5050,
    "dev_redisinsight": 5540,
    "dev_n8n": 5678,
    "dev_portainer": 9000,
}

# Static grouping for the original compose-defined containers.
GROUPS = {
    "dev_db_postgreSQL": "Databases",
    "dev_db_mysql": "Databases",
    "dev_db_mongo": "Databases",
    "dev_db_redis": "Databases",
    "dev_db_memcached": "Databases",
    "dev_nginx": "Tools & UI",
    "dev_adminer": "Tools & UI",
    "dev_pgadmin": "Tools & UI",
    "dev_redisinsight": "Tools & UI",
    "dev_n8n": "Automation",
}

LABEL_KIND = "stackcontrol.kind"
LABEL_ENGINE = "stackcontrol.engine"
USER_DB_LABEL_VALUE = "user-db"

# Per-engine defaults for spinning up a new database container.
ENGINE_DEFAULTS = {
    "postgres": {"image": "postgres:16", "port": 5432, "volume_path": "/var/lib/postgresql/data"},
    "mysql":    {"image": "mysql:8",     "port": 3306, "volume_path": "/var/lib/mysql"},
    "mongo":    {"image": "mongo:latest","port": 27017,"volume_path": "/data/db"},
    "redis":    {"image": "redis:latest","port": 6379, "volume_path": "/data"},
}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


def requires_auth(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not DASH_USER:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != DASH_USER or auth.password != DASH_PASS:
            return Response(
                "Authentication required", 401,
                {"WWW-Authenticate": 'Basic realm="Dashboard"'}
            )
        return f(*args, **kwargs)
    return wrapped


def port_in_use(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def serialize(container):
    container.reload()
    attrs = container.attrs
    ports = []
    for cport, bindings in (attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
        if bindings:
            for b in bindings:
                ports.append(f'{b.get("HostPort")}->{cport}')
    name = container.name
    labels = attrs.get("Config", {}).get("Labels") or {}
    is_user_db = labels.get(LABEL_KIND) == USER_DB_LABEL_VALUE
    group = "My Databases" if is_user_db else GROUPS.get(name, "Other")
    # default static mapping first
    open_port = OPEN_LINKS.get(name)

    # fallback: detect from docker port bindings
    if open_port is None:
        network_ports = attrs.get("NetworkSettings", {}).get("Ports") or {}

        for container_port, bindings in network_ports.items():
            if bindings:
                host_port = bindings[0].get("HostPort")
                if host_port:
                    open_port = int(host_port)
                    break
    return {
        "id": container.short_id,
        "name": name,
        "image": (attrs.get("Config", {}).get("Image") or ""),
        "status": container.status,
        "health": (attrs.get("State", {}).get("Health", {}) or {}).get("Status"),
        "started_at": attrs.get("State", {}).get("StartedAt"),
        "ports": ports,
        "group": group,
        "open_port": open_port,
        "is_user_db": is_user_db,
        "engine": labels.get(LABEL_ENGINE),
        "persistent": labels.get("stackcontrol.persistent", "true") == "true",
    }


# ---------------------------------------------------------------------------
# Existing container management
# ---------------------------------------------------------------------------

@app.route("/api/containers", methods=["GET"])
@requires_auth
def list_containers():
    containers = client.containers.list(all=True)
    data = [serialize(c) for c in containers]
    data.sort(key=lambda c: (c["group"], c["name"]))
    return jsonify(data)


@app.route("/api/containers/<name>/<action>", methods=["POST"])
@requires_auth
def container_action(name, action):
    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": f"No container named {name}"}), 404
    try:
        if action == "start":
            c.start()
        elif action == "stop":
            c.stop()
        elif action == "restart":
            c.restart()
        elif action == "pause":
            c.pause()
        elif action == "unpause":
            c.unpause()
        else:
            return jsonify({"error": f"Unknown action {action}"}), 400
    except APIError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(serialize(c))

@app.route("/api/containers/<name>", methods=["DELETE"])
@requires_auth
def delete_container(name):
    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": f"No container named {name}"}), 404

    c.reload()

    if c.status == "running":
        return jsonify({
            "error": "Stop the container before deleting it."
        }), 400

    try:
        c.remove()
    except APIError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"deleted": name})

@app.route("/api/containers/<name>/logs", methods=["GET"])
@requires_auth
def container_logs(name):
    tail = request.args.get("tail", 200)
    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": f"No container named {name}"}), 404
    try:
        logs = c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
    except APIError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"name": name, "logs": logs})

@app.route("/api/containers/<name>/stats", methods=["GET"])
@requires_auth
def container_stats(name):
    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": "Container not found"}), 404

    if c.status != "running":
        return jsonify({
            "cpu": 0,
            "memory": "Stopped",
            "network_rx": "-",
            "network_tx": "-",
            "block_read": "-",
            "block_write": "-"
        })

    s = c.stats(stream=False)

    cpu_delta = (
        s["cpu_stats"]["cpu_usage"]["total_usage"] -
        s["precpu_stats"]["cpu_usage"]["total_usage"]
    )

    sys_delta = (
        s["cpu_stats"]["system_cpu_usage"] -
        s["precpu_stats"]["system_cpu_usage"]
    )

    cpus = len(
        s["cpu_stats"]["cpu_usage"].get("percpu_usage", [])
    ) or 1

    cpu = (cpu_delta / sys_delta * cpus * 100) if sys_delta > 0 else 0

    mem = s["memory_stats"]["usage"]
    mem_limit = s["memory_stats"]["limit"]

    mem_str = f"{mem/1024/1024:.1f} MB / {mem_limit/1024/1024:.1f} MB"

    rx = tx = 0
    for n in s.get("networks", {}).values():
        rx += n["rx_bytes"]
        tx += n["tx_bytes"]

    blk_read = blk_write = 0
    for io in s.get("blkio_stats", {}).get("io_service_bytes_recursive", []):
        if io["op"] == "Read":
            blk_read += io["value"]
        elif io["op"] == "Write":
            blk_write += io["value"]

    return jsonify({
        "cpu": round(cpu, 2),
        "memory": mem_str,
        "network_rx": f"{rx/1024:.1f} KB",
        "network_tx": f"{tx/1024:.1f} KB",
        "block_read": f"{blk_read/1024:.1f} KB",
        "block_write": f"{blk_write/1024:.1f} KB"
    })

# ---------------------------------------------------------------------------
# Host stats
# ---------------------------------------------------------------------------

@app.route("/api/host/stats", methods=["GET"])
@requires_auth
def host_stats():
    cpu = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = None
    return jsonify({
        "cpu_percent": cpu,
        "mem_percent": mem.percent,
        "mem_used_gb": round(mem.used / 1024**3, 2),
        "mem_total_gb": round(mem.total / 1024**3, 2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / 1024**3, 2),
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "load_avg": [load1, load5, load15],
        "timestamp": time.time(),
    })


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@app.route("/api/profile", methods=["GET"])
@requires_auth
def profile():
    return jsonify({
        "username": DASH_USER or "root",
        "host": socket.gethostname(),
    })


# ---------------------------------------------------------------------------
# Dynamic database creation
# ---------------------------------------------------------------------------

def _build_sql_create_table(table, dialect):
    cols = table.get("columns") or []
    if not cols:
        cols = [{"name": "id", "type": "INTEGER"}]
    col_sql = ", ".join(f'{c["name"]} {c["type"]}' for c in cols)
    return f'CREATE TABLE IF NOT EXISTS {table["name"]} ({col_sql});'


def _wait_and_exec(container, attempts, delay, cmd, env=None):
    """Retry an exec_run until it succeeds (exit_code 0) or attempts run out."""
    last_output = b""
    for _ in range(attempts):
        try:
            result = container.exec_run(cmd, environment=env)
            if result.exit_code == 0:
                return True, result.output.decode("utf-8", errors="replace")
            last_output = result.output
        except APIError as e:
            last_output = str(e).encode()
        time.sleep(delay)
    return False, last_output.decode("utf-8", errors="replace") if isinstance(last_output, bytes) else str(last_output)


def _create_tables_or_collections(container, engine, username, password, db_name, tables):
    if not tables:
        return []
    results = []
    for table in tables:
        tname = table["name"]
        if engine == "postgres":
            sql = _build_sql_create_table(table, "postgres")
            ok, out = _wait_and_exec(
                container, 1, 0,
                ["psql", "-U", username, "-d", db_name, "-c", sql],
                env={"PGPASSWORD": password},
            )
        elif engine == "mysql":
            sql = _build_sql_create_table(table, "mysql")
            ok, out = _wait_and_exec(
                container, 1, 0,
                ["mysql", "-u", "root", f"-p{password}", db_name, "-e", sql],
            )
        elif engine == "mongo":
            ok, out = _wait_and_exec(
                container, 1, 0,
                ["mongosh", "--quiet", db_name,
                 "-u", username, "-p", password, "--authenticationDatabase", "admin",
                 "--eval", f"db.createCollection('{tname}')"],
            )
        else:
            ok, out = False, "Tables are not applicable for this engine."
        results.append({"table": tname, "ok": ok, "detail": out.strip()[:500]})
    return results


def _wait_until_ready(container, engine, username, password, db_name):
    if engine == "postgres":
        ok, _ = _wait_and_exec(container, 25, 1, ["pg_isready", "-U", username])
    elif engine == "mysql":
        ok, _ = _wait_and_exec(container, 40, 1.5, ["mysqladmin", "ping", "-h", "127.0.0.1", "-u", "root", f"-p{password}"])
    elif engine == "mongo":
        ok, _ = _wait_and_exec(
            container, 30, 1,
            ["mongosh", "--quiet", "--eval", "db.runCommand({ping:1})",
             "-u", username, "-p", password, "--authenticationDatabase", "admin"],
        )
    elif engine == "redis":
        ok, _ = _wait_and_exec(container, 15, 1, ["redis-cli", "-a", password, "ping"])
    else:
        ok = True
    return ok


@app.route("/api/databases", methods=["GET"])
@requires_auth
def list_databases():
    containers = client.containers.list(all=True, filters={"label": f"{LABEL_KIND}={USER_DB_LABEL_VALUE}"})
    return jsonify([serialize(c) for c in containers])


@app.route("/api/databases", methods=["POST"])
@requires_auth
def create_database():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip().lower()
    engine = (body.get("engine") or "").strip().lower()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    db_name = (body.get("db_name") or "").strip()
    host_port = body.get("host_port")
    tables = body.get("tables") or []
    persistent = body.get("persistent", True)

    if engine not in ENGINE_DEFAULTS:
        return jsonify({"error": f"Unknown engine '{engine}'. Choose postgres, mysql, mongo, or redis."}), 400
    if not NAME_RE.match(name):
        return jsonify({"error": "Name must be lowercase letters/numbers/_/- , 2-40 chars."}), 400
    if engine != "redis" and not username:
        return jsonify({"error": "Username is required for this engine."}), 400
    if not password or len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400
    if engine in ("postgres", "mysql", "mongo") and not db_name:
        return jsonify({"error": "Database name is required for this engine."}), 400
    try:
        host_port = int(host_port)
    except (TypeError, ValueError):
        return jsonify({"error": "host_port must be a number."}), 400
    if not (1024 <= host_port <= 65535):
        return jsonify({"error": "host_port must be between 1024 and 65535."}), 400

    container_name = f"userdb_{name}"
    volume_name = f"{container_name}_data"

    try:
        client.containers.get(container_name)
        return jsonify({"error": f"A database named '{name}' already exists."}), 409
    except NotFound:
        pass

    if port_in_use(host_port):
        return jsonify({"error": f"Port {host_port} is already in use on this host."}), 409

    defaults = ENGINE_DEFAULTS[engine]
    image = defaults["image"]
    container_port = defaults["port"]
    volume_path = defaults["volume_path"]

    env = {}
    command = None
    if engine == "postgres":
        env = {"POSTGRES_USER": username, "POSTGRES_PASSWORD": password, "POSTGRES_DB": db_name}
    elif engine == "mysql":
        env = {"MYSQL_ROOT_PASSWORD": password, "MYSQL_DATABASE": db_name,
               "MYSQL_USER": username, "MYSQL_PASSWORD": password}
    elif engine == "mongo":
        env = {"MONGO_INITDB_ROOT_USERNAME": username, "MONGO_INITDB_ROOT_PASSWORD": password}
    elif engine == "redis":
        command = ["redis-server", "--requirepass", password]

    try:
        client.images.get(image)
    except ImageNotFound:
        try:
            client.images.pull(image)
        except APIError as e:
            return jsonify({"error": f"Could not pull image {image}: {e}"}), 500

    try:
        run_args = {
            "image": image,
            "name": container_name,
            "command": command,
            "environment": env,
            "ports": {f"{container_port}/tcp": host_port},
            "labels": {
                LABEL_KIND: USER_DB_LABEL_VALUE,
                LABEL_ENGINE: engine,
                "stackcontrol.persistent": str(persistent).lower(),
            },
            "detach": True,
        }

        if persistent:
            client.volumes.create(name=volume_name)
            run_args["volumes"] = {
                volume_name: {
                    "bind": volume_path,
                    "mode": "rw"
                }
            }
            run_args["restart_policy"] = {
                "Name": "unless-stopped"
            }

        container = client.containers.run(**run_args)
    except APIError as e:
        return jsonify({"error": f"Failed to create container: {e}"}), 500

    ready = _wait_until_ready(container, engine, username, password, db_name)
    table_results = []
    if ready and tables:
        table_results = _create_tables_or_collections(container, engine, username, password, db_name, tables)

    return jsonify({
        "container": serialize(container),
        "ready": ready,
        "tables": table_results,
        "warning": None if ready else "Container is starting but did not respond to a health check in time. It may still come up — check its logs in a moment.",
    }), 201


@app.route("/api/databases/<name>", methods=["DELETE"])
@requires_auth
def delete_database(name):
    container_name = name if name.startswith("userdb_") else f"userdb_{name}"
    remove_volume = request.args.get("remove_volume", "false").lower() == "true"
    try:
        c = client.containers.get(container_name)
    except NotFound:
        return jsonify({"error": f"No database container named {name}"}), 404

    labels = c.attrs.get("Config", {}).get("Labels") or {}
    if labels.get(LABEL_KIND) != USER_DB_LABEL_VALUE:
        return jsonify({"error": "This container was not created by the dashboard; refusing to delete."}), 400

    try:
        c.reload()

        if c.status == "running":
            return jsonify({
                "error": "Stop the database before deleting it."
            }), 400

        c.remove()
        if remove_volume:
            try:
                client.volumes.get(f"{container_name}_data").remove()
            except NotFound:
                pass
    except APIError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"deleted": container_name, "volume_removed": remove_volume})


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9090)