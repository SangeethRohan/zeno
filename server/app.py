import os
import re
import time
import socket
import json
import shlex
import posixpath
import platform
import functools
import uuid
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, send_from_directory, session, redirect
import docker
from docker.errors import NotFound, APIError, ImageNotFound
import psutil
from dotenv import load_dotenv

import db as zeno_db

load_dotenv()

APP_NAME = "Zeno"
APP_VERSION = "2.0"
APP_TIERS = zeno_db.APP_TIERS
API_PREFIX = "/api/v1"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.environ.get("SECRET_KEY", "super-secret-key")
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

WEB_UI_IMAGES = {
    "dvwa",
    "web-dvwa",
    "juice-shop",
    "webgoat",
    "mutillidae",
    "nginx",
    "apache",
    "httpd",
    "php",
    "wordpress",
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

LABEL_APP = "zeno.app"
LABEL_KIND = "zeno.app.kind"
LABEL_ENGINE = "zeno.app.engine"
USER_DB_LABEL_VALUE = "user-db"
USER_SERVER_LABEL_VALUE = "user-server"
USER_WEB_LABEL_VALUE = "user-web"

WEB_SERVER_DEFAULTS = {
    "nginx": {"image": "nginx:alpine", "port": 80},
    "apache": {"image": "httpd:alpine", "port": 80},
    "caddy": {"image": "caddy:alpine", "port": 80},
    "traefik": {"image": "traefik:v3.0", "port": 80},
}

UBUNTU_IMAGE = "ubuntu:24.04"
VALID_LANGUAGES = {"python", "java", "c", "cpp", "go", "node", "rust"}

# Per-engine defaults for spinning up a new database container.
ENGINE_DEFAULTS = {
    "postgres": {"image": "postgres:16", "port": 5432, "volume_path": "/var/lib/postgresql/data"},
    "mysql":    {"image": "mysql:8",     "port": 3306, "volume_path": "/var/lib/mysql"},
    "mongo":    {"image": "mongo:latest","port": 27017,"volume_path": "/data/db"},
    "redis":    {"image": "redis:latest","port": 6379, "volume_path": "/data"},
}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
USER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{2,31}$")
CORE_APP_NAMES = {"zeno_dashboard", "zeno_mongo"}

_terminal_cwd = {}
_terminal_shells = {}
INTERACTIVE_SHELL_PROGRAMS = {
    "mongosh", "mongo", "mysql", "mariadb", "psql", "redis-cli", "bash", "sh",
}
_container_status_cache = {}
_cpu_high_streak = {}
_mem_high_streak = {}
_restart_streak = {}
_user_thresholds_cache = {}
_user_thresholds_cache_at = 0
METRICS_ENABLED = os.environ.get("METRICS_ENABLED", "true").lower() != "false"
MONITOR_INTERVAL_SEC = int(os.environ.get("MONITOR_INTERVAL_SEC", "60"))


def get_app_tier():
    try:
        return zeno_db.get_app_tier()
    except Exception:
        return zeno_db.DEFAULT_TIER


def set_app_tier(tier):
    zeno_db.set_app_tier(tier)


def current_username():
    return session.get("username") or "guest"


def current_role():
    return session.get("role") or "user"


def is_admin():
    return current_role() == "admin"


def current_user_tier():
    try:
        return zeno_db.get_user_tier(current_username())
    except Exception:
        return zeno_db.DEFAULT_TIER


def tier_payload():
    return {
        "tier": current_user_tier(),
        "default_tier": get_app_tier(),
        "tiers": list(APP_TIERS),
        "is_admin": is_admin(),
        "is_primary": zeno_db.is_primary_user(current_username()),
        "role": current_role(),
    }


def can_manage_container_group(group):
    if is_admin():
        return True
    return group != "Core Apps"


def can_manage_container_obj(container):
    return can_manage_container_group(serialize(container)["group"])


def log_container_activity(action, container, details=None):
    try:
        container.reload()
    except NotFound:
        pass
    attrs = getattr(container, "attrs", None) or {}
    image = (attrs.get("Config", {}) or {}).get("Image", "")
    name = getattr(container, "name", "")
    zeno_db.log_activity(
        current_username(),
        action,
        container=name,
        container_image=image,
        details=details,
    )


def user_payload():
    return {
        "username": current_username(),
        "role": current_role(),
        "is_admin": is_admin(),
    }


def features_payload():
    return {
        "features": zeno_db.features_for_user(
            current_username(), current_role()
        ),
        "feature_labels": zeno_db.FEATURE_LABELS,
    }


def require_feature(feature_key):
    def decorator(f):
        @functools.wraps(f)
        def wrapped(*args, **kwargs):
            feats = zeno_db.features_for_user(
                current_username(), current_role()
            )
            if not feats.get(feature_key):
                label = zeno_db.FEATURE_LABELS.get(feature_key, feature_key)
                return jsonify({
                    "error": f"Your edition does not include: {label}."
                }), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator


def requires_auth(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if session.get("logged_in") is True:
            return f(*args, **kwargs)

        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401

        return redirect("/login")

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


def port_in_use_error(port: int):
    if port_in_use(port):
        return f"Port {port} is already in use on this host."
    return None


def find_free_port(start: int, end: int):
    for port in range(start, end + 1):
        if not port_in_use(port):
            return port
    return None


def _terminal_session_key(container_name):
    return (current_username(), container_name)


def _resolve_cd(current, command):
    parts = command.strip().split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg or arg == "~":
        return "/"
    if arg == "-":
        return current
    if arg.startswith("/"):
        path = arg
    else:
        path = posixpath.join(current or "/", arg)
    normalized = posixpath.normpath(path)
    return normalized if normalized.startswith("/") else "/" + normalized


def _validate_dir_in_container(container, path):
    quoted = shlex.quote(path)
    result = container.exec_run(
        ["/bin/sh", "-c", f"test -d {quoted} || test -L {quoted}"],
        workdir="/",
    )
    return result.exit_code == 0


def _close_terminal_shell(session_key):
    _terminal_shells.pop(session_key, None)


def _decode_exec_output(result):
    stdout = (result.output[0] or b"").decode("utf-8", errors="replace")
    stderr = (result.output[1] or b"").decode("utf-8", errors="replace")
    return stdout + stderr


MONGO_USE_RE = re.compile(r"^\s*use\s+(\S+)\s*;?\s*$", re.IGNORECASE)


def _mongo_env_credentials(container):
    env_list = (container.attrs.get("Config", {}) or {}).get("Env") or []
    env = {}
    for item in env_list:
        if "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    user = env.get("MONGO_INITDB_ROOT_USERNAME")
    password = env.get("MONGO_INITDB_ROOT_PASSWORD")
    if user and password:
        return ["-u", user, "-p", password, "--authenticationDatabase", "admin"]
    return []


def _mongo_prompt(shell):
    return f"{shell.get('mongo_db', 'test')}>"


def _clean_mongosh_output(output):
    cleaned = []
    for line in output.splitlines():
        stripped = line.strip()
        if re.match(r"^[a-zA-Z0-9_-]+>\s*$", stripped):
            continue
        if stripped.startswith("test>") or stripped.startswith("admin>"):
            stripped = re.sub(r"^[a-zA-Z0-9_-]+>\s*", "", line).rstrip()
            if not stripped:
                continue
            cleaned.append(stripped)
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _shell_prompt(shell):
    program = shell.get("program")
    if program in ("mongosh", "mongo"):
        return _mongo_prompt(shell)
    if program == "redis-cli":
        return "127.0.0.1:6379>"
    if program in ("mysql", "mariadb"):
        return "mysql>"
    if program == "psql":
        return "psql>"
    return f"{program}>"


def _build_virtual_shell_cmd(shell, command):
    program = shell.get("program")
    if program in ("mongosh", "mongo"):
        args = shell.get("args", [])
        mongo_db = shell.get("mongo_db", "test")
        command = command.rstrip(";")
        if MONGO_USE_RE.match(command):
            lines = [command]
        else:
            lines = [f"use {mongo_db}", command]
        script_body = "\n".join(lines)
        mongosh_parts = [program, "--quiet", *args]
        mongosh_cmd = " ".join(shlex.quote(part) for part in mongosh_parts)
        script = f"printf '%s\\n' {shlex.quote(script_body)} | {mongosh_cmd}"
        return ["/bin/sh", "-c", script]
    if program in ("mysql", "mariadb"):
        args = shell.get("args", [])
        client = " ".join(shlex.quote(part) for part in [program, *args])
        script = f"printf '%s\\n' {shlex.quote(command)} | {client}"
        return ["/bin/sh", "-c", script]
    if program == "psql":
        return ["psql", "-c", command]
    if program == "redis-cli":
        return ["redis-cli", *shlex.split(command)]
    if program in ("bash", "sh"):
        return ["/bin/sh", "-c", command]
    return [program, *shlex.split(command)]


def _virtual_shell_banner(program, shell):
    if program in ("mongosh", "mongo"):
        db_name = shell.get("mongo_db", "test")
        auth_note = ""
        if shell.get("args"):
            auth_note = " (authenticated)"
        return (
            f"Current Mongosh Log ID:\t{uuid.uuid4().hex[:24]}\n"
            f"Connecting to:\t\tmongodb://127.0.0.1:27017/?directConnection=true\n"
            f"Using Mongosh:\t\tvirtual session{auth_note}\n\n"
            f"For mongosh info see: https://www.mongodb.com/docs/mongodb-shell/\n\n"
            f"{db_name}>"
        )
    return f"Connected to {program}. Type commands below (type exit to leave).\n"


def _is_interactive_shell_command(command):
    try:
        parts = shlex.split(command)
    except ValueError:
        return False, None
    if not parts:
        return False, None
    program = posixpath.basename(parts[0])
    if program in INTERACTIVE_SHELL_PROGRAMS:
        return True, parts
    return False, None


def _is_shell_exit_command(command):
    cmd = command.strip().lower()
    return cmd in {"exit", "quit", "\\q", "logout"}


def _exec_in_terminal_shell(container, session_key, command, cwd):
    shell = _terminal_shells.get(session_key)
    if not shell:
        return None
    program = shell.get("program")
    if _is_shell_exit_command(command):
        _close_terminal_shell(session_key)
        return {
            "exit_code": 0,
            "output": "",
            "shell": None,
            "prompt": None,
        }
    if program in ("mongosh", "mongo"):
        match = MONGO_USE_RE.match(command)
        if match:
            shell["mongo_db"] = match.group(1)
    try:
        exec_cmd = _build_virtual_shell_cmd(shell, command)
        result = container.exec_run(exec_cmd, workdir=cwd, demux=True)
        output = _decode_exec_output(result)
        if program in ("mongosh", "mongo"):
            output = _clean_mongosh_output(output)
        prompt = _shell_prompt(shell)
        shell["prompt"] = prompt
        return {
            "exit_code": result.exit_code,
            "output": output or "(no output)",
            "shell": program,
            "prompt": prompt,
        }
    except APIError as exc:
        return {
            "exit_code": 1,
            "output": str(exc) + "\n",
            "shell": program,
            "prompt": shell.get("prompt") or _shell_prompt(shell),
        }


def _start_terminal_shell(container, session_key, parts, cwd):
    program = posixpath.basename(parts[0])
    try:
        check = container.exec_run(
            ["sh", "-c", f"command -v {shlex.quote(program)} >/dev/null 2>&1"],
            workdir=cwd,
            demux=True,
        )
        if check.exit_code != 0:
            return {
                "exit_code": 127,
                "output": f"/bin/sh: {program}: not found\n",
                "shell": None,
                "prompt": None,
            }
    except APIError as exc:
        return {
            "exit_code": 1,
            "output": str(exc) + "\n",
            "shell": None,
            "prompt": None,
        }

    shell_args = parts[1:]
    if program in ("mongosh", "mongo"):
        try:
            container.reload()
        except NotFound:
            pass
        if not shell_args:
            shell_args = _mongo_env_credentials(container)

    mongo_db = "test"
    if program in ("mongosh", "mongo") and shell_args:
        mongo_db = "admin"

    shell = {
        "program": program,
        "args": shell_args,
        "mongo_db": mongo_db,
        "virtual": True,
    }
    shell["prompt"] = _shell_prompt(shell)
    _terminal_shells[session_key] = shell
    banner = _virtual_shell_banner(program, shell)
    return {
        "exit_code": 0,
        "output": banner or "",
        "shell": program,
        "prompt": shell["prompt"],
    }


def serialize(container):
    container.reload()
    attrs = container.attrs

    # -------------------------
    # PORTS (safe format)
    # -------------------------
    ports = []
    network_ports = (attrs.get("NetworkSettings", {}).get("Ports") or {})
    seen_ports = set()

    for cport, bindings in network_ports.items():
        if bindings:
            for b in bindings:
                host = b.get("HostPort")
                if host:
                    entry = f"{host}->{cport}"
                    if entry not in seen_ports:
                        seen_ports.add(entry)
                        ports.append(entry)

    # -------------------------
    # BASIC METADATA
    # -------------------------
    name = container.name
    labels = attrs.get("Config", {}).get("Labels") or {}

    kind = labels.get(LABEL_KIND)
    is_user_db = kind == USER_DB_LABEL_VALUE
    is_user_server = kind == USER_SERVER_LABEL_VALUE
    is_user_web = kind == USER_WEB_LABEL_VALUE

    if name in CORE_APP_NAMES or "zeno_dashboard" in name:
        group = "Core Apps"
    elif is_user_db:
        group = "My Databases"
    elif is_user_server:
        group = "My Servers"
    elif is_user_web:
        group = "My Web Servers"
    else:
        group = GROUPS.get(name, "Other")

    # -------------------------
    # WEB UI DETECTION (IMPORTANT FIX)
    # -------------------------
    image = (attrs.get("Config", {}).get("Image") or "").lower()
    cname = name.lower()

    WEB_UI_KEYWORDS = {
        "dvwa",
        "webgoat",
        "juice",
        "mutillidae",
        "nowasp",
        "bwa",
        "nginx",
        "apache",
        "httpd",
        "wordpress",
    }

    is_web_ui = any(
        kw in image or kw in cname
        for kw in WEB_UI_KEYWORDS
    )

    # -------------------------
    # OPEN PORT LOGIC (FIXED)
    # -------------------------
    open_port = None

    if is_user_web:
        for cport, bindings in network_ports.items():
            if bindings:
                host = bindings[0].get("HostPort")
                if host:
                    open_port = int(host)
                    break

    # ONLY web UIs can have open button
    elif is_web_ui:
        # 1. try static mapping
        open_port = OPEN_LINKS.get(cname)

        # 2. fallback to docker port mapping
        if open_port is None:
            for cport, bindings in network_ports.items():
                if bindings:
                    host = bindings[0].get("HostPort")
                    if host:
                        open_port = int(host)
                        break

    # -------------------------
    # RETURN
    # -------------------------
    is_core_app = group == "Core Apps"
    return {
        "id": container.short_id,
        "name": name,
        "image": attrs.get("Config", {}).get("Image") or "",
        "status": container.status,
        "health": (attrs.get("State", {}).get("Health", {}) or {}).get("Status"),
        "started_at": attrs.get("State", {}).get("StartedAt"),
        "ports": ports,
        "group": group,
        "default_group": group,
        "is_core_app": is_core_app,
        "open_port": open_port,
        "is_user_db": is_user_db,
        "is_user_server": is_user_server,
        "is_user_web": is_user_web,
        "engine": labels.get(LABEL_ENGINE),
        "persistent": labels.get("stackcontrol.persistent", "true") == "true",
        "created_by": labels.get("zeno.created_by"),
    }


# ---------------------------------------------------------------------------
# Existing container management
# ---------------------------------------------------------------------------

@app.route("/api/info", methods=["GET"])
def info():
    return jsonify({
        "name": APP_NAME,
        "version": APP_VERSION
    })

@app.route(f"{API_PREFIX}/me", methods=["GET"])
@requires_auth
def me():
    return jsonify({
        "host": socket.gethostname(),
        "product": APP_NAME,
        "alert_notifications": zeno_db.get_alert_notifications(current_username()),
        **user_payload(),
        **tier_payload(),
        **features_payload(),
    })

@app.route("/login")
def login_page():
    return send_from_directory(app.static_folder, "login.html")

@app.route(f"{API_PREFIX}/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"success": False, "error": "Invalid username or password"}), 401

    user = zeno_db.authenticate(username, password)
    if not user:
        return jsonify({"success": False, "error": "Invalid username or password"}), 401

    session["logged_in"] = True
    session["username"] = user["username"]
    session["role"] = user.get("role", "user")
    session["tier"] = user.get("tier") or zeno_db.get_user_tier(user["username"])
    return jsonify({"success": True})


@app.route("/profile")
@requires_auth
def profile_page():
    return send_from_directory(app.static_folder, "profile.html")

@app.route(f"{API_PREFIX}/profile", methods=["GET"])
@requires_auth
def profile():
    return jsonify({
        "product": APP_NAME,
        "username": current_username(),
        "host": socket.gethostname(),
        **tier_payload(),
    })


@app.route(f"{API_PREFIX}/settings", methods=["GET"])
@requires_auth
def settings_info():
    return jsonify({
        "product": APP_NAME,
        "version": APP_VERSION,
        "auth_enabled": True,
        "host": socket.gethostname(),
        "mongo_ready": zeno_db.is_ready(),
        "alert_notifications": zeno_db.get_alert_notifications(current_username()),
        "alert_notification_rules": list(zeno_db.ALERT_NOTIFICATION_RULES),
        "alert_notification_labels": zeno_db.ALERT_NOTIFICATION_LABELS,
        **tier_payload(),
    })


@app.route(f"{API_PREFIX}/users", methods=["GET"])
@requires_auth
def list_users_api():
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403
    return jsonify(zeno_db.list_users_with_stats())


@app.route(f"{API_PREFIX}/register", methods=["POST"])
def register_api():
    body = request.get_json(force=True, silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not USER_RE.match(username):
        return jsonify({
            "error": "Username must be 3-32 chars: letters, numbers, _ or -"
        }), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400

    try:
        zeno_db.create_user(username, password, role="user", created_by="register")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"created": username, "role": "user"}), 201


@app.route(f"{API_PREFIX}/users", methods=["POST"])
@requires_auth
def create_user_api():
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403

    body = request.get_json(force=True, silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = (body.get("role") or "user").strip()
    tier = (body.get("tier") or get_app_tier()).strip()

    if not USER_RE.match(username):
        return jsonify({
            "error": "Username must be 3-32 chars: letters, numbers, _ or -"
        }), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400
    if role not in ("admin", "user"):
        return jsonify({"error": "Role must be admin or user."}), 400
    if tier not in APP_TIERS:
        return jsonify({"error": "Tier must be Core, Pro, or Elite."}), 400

    try:
        zeno_db.create_user(
            username, password, role=role, created_by=current_username(), tier=tier
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"created": username, "role": role, "tier": tier}), 201


@app.route(f"{API_PREFIX}/users/<username>", methods=["PATCH"])
@requires_auth
def patch_user_api(username):
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403

    body = request.get_json(force=True, silent=True) or {}
    tier = body.get("tier")
    role = body.get("role")

    if tier is not None:
        tier = str(tier).strip()
    if role is not None:
        role = str(role).strip()

    try:
        zeno_db.update_user(username, tier=tier, role=role)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    user = zeno_db.get_user(username)
    return jsonify(user)


@app.route(f"{API_PREFIX}/users/<username>/password", methods=["PATCH"])
@requires_auth
def admin_reset_password(username):
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403
    body = request.get_json(force=True, silent=True) or {}
    password = body.get("password") or ""
    try:
        zeno_db.admin_set_password(username, password)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"updated": username})


@app.route(f"{API_PREFIX}/account/password", methods=["POST"])
@requires_auth
def change_own_password():
    body = request.get_json(force=True, silent=True) or {}
    current_password = body.get("current_password") or ""
    new_password = body.get("new_password") or ""
    try:
        zeno_db.change_password(
            current_username(), current_password, new_password
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"success": True})


@app.route(f"{API_PREFIX}/account/tier", methods=["PUT"])
@requires_auth
def change_own_tier():
    if not is_admin():
        return jsonify({"error": "Only admins can change edition here."}), 403
    if zeno_db.is_primary_user(current_username()):
        return jsonify({"error": "Primary admin tier cannot be changed."}), 400

    body = request.get_json(force=True, silent=True) or {}
    tier = (body.get("tier") or "").strip()
    if tier not in APP_TIERS:
        return jsonify({"error": "Choose Core, Pro, or Elite."}), 400

    try:
        zeno_db.update_user(current_username(), tier=tier)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    session["tier"] = tier
    return jsonify({**tier_payload()})


@app.route(f"{API_PREFIX}/account/notifications", methods=["PUT"])
@requires_auth
def update_alert_notifications():
    body = request.get_json(force=True, silent=True) or {}
    notifications = body.get("alert_notifications") or body
    if not isinstance(notifications, dict):
        return jsonify({"error": "Invalid notification settings."}), 400
    try:
        updated = zeno_db.set_alert_notifications(
            current_username(), notifications
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({
        "alert_notifications": updated,
        "alert_notification_labels": zeno_db.ALERT_NOTIFICATION_LABELS,
    })


@app.route(f"{API_PREFIX}/admin/tier-features", methods=["GET"])
@requires_auth
def get_tier_features_api():
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403
    return jsonify({
        "tiers": list(APP_TIERS),
        "features": zeno_db.FEATURE_KEYS,
        "feature_labels": zeno_db.FEATURE_LABELS,
        "tier_features": zeno_db.get_tier_features_map(),
    })


@app.route(f"{API_PREFIX}/admin/tier-features", methods=["PUT"])
@requires_auth
def put_tier_features_api():
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403
    body = request.get_json(force=True, silent=True) or {}
    try:
        updated = zeno_db.set_tier_features_map(body.get("tier_features") or body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"tier_features": updated})


@app.route(f"{API_PREFIX}/users/bulk", methods=["POST"])
@requires_auth
def bulk_users_api():
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403

    body = request.get_json(force=True, silent=True) or {}
    action = (body.get("action") or "").strip()
    usernames = body.get("usernames") or []
    if not isinstance(usernames, list) or not usernames:
        return jsonify({"error": "Select at least one user."}), 400

    usernames = [str(u).strip() for u in usernames if str(u).strip()]
    if current_username() in usernames and action == "delete":
        return jsonify({"error": "You cannot delete your own account."}), 400

    if action == "delete":
        deleted, errors = zeno_db.bulk_delete_users(usernames)
        return jsonify({"deleted": deleted, "errors": errors})

    if action == "set_tier":
        tier = (body.get("tier") or "").strip()
        if tier not in APP_TIERS:
            return jsonify({"error": "Tier must be Core, Pro, or Elite."}), 400
        updated, errors = zeno_db.bulk_set_tier(usernames, tier)
        return jsonify({"updated": updated, "tier": tier, "errors": errors})

    return jsonify({"error": "Unknown action. Use delete or set_tier."}), 400


@app.route(f"{API_PREFIX}/activity", methods=["GET"])
@requires_auth
def activity_log_api():
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403

    username = request.args.get("username")
    limit = request.args.get("limit", 100)
    skip = request.args.get("skip", 0)
    entries = zeno_db.list_activity(username=username, limit=limit, skip=skip)
    return jsonify({
        "entries": entries,
        "username": username,
        "limit": int(limit),
        "skip": int(skip),
    })


@app.route(f"{API_PREFIX}/activity/me", methods=["GET"])
@requires_auth
def my_activity_api():
    limit = request.args.get("limit", 200)
    skip = request.args.get("skip", 0)
    entries = zeno_db.list_activity(
        username=current_username(), limit=limit, skip=skip
    )
    return jsonify({
        "entries": entries,
        "username": current_username(),
        "limit": int(limit),
        "skip": int(skip),
    })


@app.route(f"{API_PREFIX}/users/dashboard", methods=["GET"])
@requires_auth
def users_dashboard_api():
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403
    return jsonify({
        "users": zeno_db.activity_summary(),
        "recent": zeno_db.list_activity(limit=25),
    })


@app.route(f"{API_PREFIX}/users/<username>", methods=["DELETE"])
@requires_auth
def delete_user_api(username):
    if not is_admin():
        return jsonify({"error": "Admin access required."}), 403
    if username == current_username():
        return jsonify({"error": "You cannot delete your own account."}), 400
    try:
        zeno_db.delete_user(username)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"deleted": username})


@app.route(f"{API_PREFIX}/settings/tier", methods=["PUT"])
@requires_auth
def update_tier():
    if not is_admin():
        return jsonify({"error": "Only admin can change the app edition."}), 403

    body = request.get_json(force=True, silent=True) or {}
    tier = (body.get("tier") or "").strip()
    if tier not in APP_TIERS:
        return jsonify({"error": "Choose Core, Pro, or Elite."}), 400

    try:
        set_app_tier(tier)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({**tier_payload(), "default_tier": tier})


@app.route("/settings")
@requires_auth
def settings_page():
    return send_from_directory(app.static_folder, "settings.html")


@app.route("/manage-users")
@requires_auth
def manage_users_page():
    if not is_admin():
        return redirect("/")
    return send_from_directory(app.static_folder, "manage-users.html")

@app.route(f"{API_PREFIX}/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({
        "success": True,
        "redirect": "/login"
    })
    
@app.route(f"{API_PREFIX}/containers", methods=["GET"])
@requires_auth
def list_containers():
    containers = client.containers.list(all=True)
    data = [serialize(c) for c in containers]
    layout = zeno_db.get_or_create_layout(current_username(), data)
    zeno_db.apply_layout_to_containers(data, layout)
    for item in data:
        item["can_manage"] = can_manage_container_group(item["group"])

    group_order = {g["id"]: i for i, g in enumerate(layout.get("groups", []))}

    def sort_key(c):
        gid = c.get("group_id") or "other"
        return (group_order.get(gid, 999), c["name"])

    data.sort(key=sort_key)
    return jsonify(data)


@app.route(f"{API_PREFIX}/groups/layout", methods=["GET"])
@requires_auth
def get_groups_layout():
    containers = client.containers.list(all=True)
    data = [serialize(c) for c in containers]
    layout = zeno_db.get_or_create_layout(current_username(), data)
    return jsonify({
        "layout": layout,
        "containers": [
            {
                "name": c["name"],
                "image": c["image"],
                "default_group": c["default_group"],
                "is_core_app": c["is_core_app"],
                "group_id": layout["assignments"].get(c["name"]),
            }
            for c in data
        ],
    })


@app.route(f"{API_PREFIX}/groups/layout", methods=["PUT"])
@requires_auth
def put_groups_layout():
    body = request.get_json(force=True, silent=True) or {}
    layout = body.get("layout") or body

    groups = layout.get("groups") or []
    assignments = layout.get("assignments") or {}

    containers = client.containers.list(all=True)
    core_names = {
        serialize(c)["name"]
        for c in containers
        if serialize(c).get("is_core_app")
    }

    for name in core_names:
        assignments[name] = zeno_db.CORE_GROUP_ID

    for g in groups:
        if g.get("id") == zeno_db.CORE_GROUP_ID:
            g["locked"] = True
            g["name"] = zeno_db.CORE_GROUP_NAME

    for i, g in enumerate(groups):
        g["order"] = i

    container_order = zeno_db._sync_container_order(
        assignments, layout.get("container_order", {})
    )
    layout = {
        "groups": groups,
        "assignments": assignments,
        "container_order": container_order,
    }

    try:
        zeno_db.save_group_layout(current_username(), layout)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"layout": layout})


@app.route(f"{API_PREFIX}/groups", methods=["POST"])
@requires_auth
def create_group():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name or len(name) > 48:
        return jsonify({"error": "Group name must be 1-48 characters."}), 400
    if name == zeno_db.CORE_GROUP_NAME:
        return jsonify({"error": "That group name is reserved."}), 400

    containers = client.containers.list(all=True)
    data = [serialize(c) for c in containers]
    layout = zeno_db.get_or_create_layout(current_username(), data)

    gid = zeno_db._slug_group(name)
    existing_ids = {g["id"] for g in layout["groups"]}
    if gid in existing_ids:
        gid = f"{gid}-{uuid.uuid4().hex[:6]}"

    max_order = max((g.get("order", 0) for g in layout["groups"]), default=0)
    layout["groups"].append({
        "id": gid,
        "name": name,
        "locked": False,
        "order": max_order + 1,
    })
    zeno_db.save_group_layout(current_username(), layout)
    return jsonify({"group": layout["groups"][-1], "layout": layout}), 201


@app.route(f"{API_PREFIX}/groups/<group_id>", methods=["DELETE"])
@requires_auth
def delete_group(group_id):
    if group_id == zeno_db.CORE_GROUP_ID:
        return jsonify({"error": "Core Apps cannot be deleted."}), 400

    containers = client.containers.list(all=True)
    data = [serialize(c) for c in containers]
    layout = zeno_db.get_or_create_layout(current_username(), data)

    grp = next((g for g in layout["groups"] if g["id"] == group_id), None)
    if not grp:
        return jsonify({"error": "Group not found."}), 404
    if grp.get("locked"):
        return jsonify({"error": "This group is locked."}), 400

    fallback = next(
        (g["id"] for g in layout["groups"] if g["id"] != group_id and not g.get("locked")),
        None,
    )
    for cname, gid in list(layout["assignments"].items()):
        if gid == group_id:
            if fallback:
                layout["assignments"][cname] = fallback
            else:
                layout["assignments"].pop(cname, None)

    layout["groups"] = [g for g in layout["groups"] if g["id"] != group_id]
    for i, g in enumerate(layout["groups"]):
        g["order"] = i

    zeno_db.save_group_layout(current_username(), layout)
    return jsonify({"deleted": group_id, "layout": layout})


@app.route(f"{API_PREFIX}/containers/<name>/<action>", methods=["POST"])
@requires_auth
def container_action(name, action):
    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": f"No container named {name}"}), 404

    if not can_manage_container_obj(c):
        return jsonify({
            "error": "Core Apps containers are view only."
        }), 403

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

    log_container_activity(action, c)
    return jsonify(serialize(c))

@app.route(f"{API_PREFIX}/containers/<name>", methods=["DELETE"])
@requires_auth
def delete_container(name):
    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": f"No container named {name}"}), 404

    if not can_manage_container_obj(c):
        return jsonify({
            "error": "Core Apps containers are view only."
        }), 403

    c.reload()

    if c.status == "running":
        return jsonify({
            "error": "Stop the container before deleting it."
        }), 400

    try:
        log_container_activity("delete", c)
        c.remove()
    except APIError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"deleted": name})

@app.route(f"{API_PREFIX}/containers/<name>/logs", methods=["GET"])
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

def _fmt_io_bytes(n):
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.2f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


def _container_stats_raw(container):
    """Return numeric stats for a running container, or None if stopped."""
    if container.status != "running":
        return None

    s = container.stats(stream=False)

    cpu_delta = (
        s["cpu_stats"]["cpu_usage"]["total_usage"] -
        s["precpu_stats"]["cpu_usage"]["total_usage"]
    )
    sys_delta = (
        s["cpu_stats"]["system_cpu_usage"] -
        s["precpu_stats"]["system_cpu_usage"]
    )
    cpus = len(s["cpu_stats"]["cpu_usage"].get("percpu_usage", [])) or 1
    cpu = (cpu_delta / sys_delta * cpus * 100) if sys_delta > 0 else 0

    mem = s["memory_stats"]["usage"]
    mem_limit = s["memory_stats"]["limit"]
    mem_used_mb = mem / 1024 / 1024
    mem_limit_mb = mem_limit / 1024 / 1024

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

    return {
        "cpu": round(cpu, 3),
        "memory": f"{mem_used_mb:.1f} MB / {mem_limit_mb:.1f} MB",
        "mem_used_mb": round(mem_used_mb, 2),
        "mem_limit_mb": round(mem_limit_mb, 2),
        "network_rx": _fmt_io_bytes(rx),
        "network_tx": _fmt_io_bytes(tx),
        "block_read": _fmt_io_bytes(blk_read),
        "block_write": _fmt_io_bytes(blk_write),
        "block_read_bytes": blk_read,
        "block_write_bytes": blk_write,
    }


def _published_host_ports(container):
    container.reload()
    attrs = container.attrs or {}
    ports = []
    network_ports = (attrs.get("NetworkSettings", {}).get("Ports") or {})
    for bindings in network_ports.values():
        if bindings:
            for b in bindings:
                host = b.get("HostPort")
                if host:
                    try:
                        ports.append(int(host))
                    except (TypeError, ValueError):
                        pass
    return ports


def _port_reachable(host_port):
    try:
        with socket.create_connection(("127.0.0.1", host_port), timeout=2):
            return True
    except OSError:
        return False


def _get_user_alert_thresholds(username):
    global _user_thresholds_cache, _user_thresholds_cache_at
    now = time.time()
    cached = _user_thresholds_cache.get(username)
    if cached and now - _user_thresholds_cache_at < 30:
        return cached
    try:
        thresholds = zeno_db.get_user_alert_thresholds(username)
    except Exception:
        thresholds = dict(zeno_db.DEFAULT_ALERT_THRESHOLDS)
    _user_thresholds_cache[username] = thresholds
    _user_thresholds_cache_at = now
    return thresholds


def _streak_key(username, container_name):
    return (username, container_name)


def _check_container_alerts_for_user(username, name, raw_stats, container):
    thresholds = _get_user_alert_thresholds(username)
    cpu_limit = thresholds["cpu_percent"]
    mem_limit_pct = thresholds["mem_percent"]
    resolve_cpu_below = max(cpu_limit - 10, 1)
    resolve_mem_below = max(mem_limit_pct - 10, 1)
    streak_cpu = _streak_key(username, name)
    streak_mem = _streak_key(username, name)
    streak_restart = _streak_key(username, name)

    status = container.status
    if status == "restarting":
        _restart_streak[streak_restart] = _restart_streak.get(streak_restart, 0) + 1
        if _restart_streak[streak_restart] >= 2:
            zeno_db.insert_alert(
                "crash_loop",
                name,
                f"Container {name} is in a crash loop (restarting)",
                severity="critical",
                username=username,
            )
    else:
        if _restart_streak.pop(streak_restart, 0) >= 2:
            zeno_db.resolve_alerts("crash_loop", name, username=username)

    if not raw_stats:
        _cpu_high_streak.pop(streak_cpu, None)
        _mem_high_streak.pop(streak_mem, None)
        return

    cpu = raw_stats["cpu"]
    mem_pct = 0
    if raw_stats.get("mem_limit_mb"):
        mem_pct = (raw_stats["mem_used_mb"] / raw_stats["mem_limit_mb"]) * 100

    if cpu > cpu_limit:
        _cpu_high_streak[streak_cpu] = _cpu_high_streak.get(streak_cpu, 0) + 1
        if _cpu_high_streak[streak_cpu] >= 2:
            zeno_db.insert_alert(
                "cpu_high",
                name,
                f"CPU above {cpu_limit}% ({cpu:.1f}%) on {name}",
                severity="warning",
                cpu_percent=round(cpu, 2),
                mem_percent=round(mem_pct, 2),
                username=username,
            )
    elif cpu < resolve_cpu_below:
        _cpu_high_streak.pop(streak_cpu, None)
        zeno_db.resolve_alerts("cpu_high", name, username=username)

    if mem_pct > mem_limit_pct:
        _mem_high_streak[streak_mem] = _mem_high_streak.get(streak_mem, 0) + 1
        if _mem_high_streak[streak_mem] >= 2:
            zeno_db.insert_alert(
                "mem_high",
                name,
                f"Memory above {mem_limit_pct}% ({mem_pct:.1f}%) on {name}",
                severity="warning",
                cpu_percent=round(cpu, 2),
                mem_percent=round(mem_pct, 2),
                username=username,
            )
    elif mem_pct < resolve_mem_below:
        _mem_high_streak.pop(streak_mem, None)
        zeno_db.resolve_alerts("mem_high", name, username=username)

    failed_ports = []
    for host_port in _published_host_ports(container):
        if not _port_reachable(host_port):
            failed_ports.append(host_port)
    if failed_ports:
        zeno_db.insert_alert(
            "port_failure",
            name,
            f"Port(s) {', '.join(map(str, failed_ports))} unreachable on {name}",
            severity="critical",
            cpu_percent=round(cpu, 2),
            mem_percent=round(mem_pct, 2),
            username=username,
        )
    else:
        zeno_db.resolve_alerts("port_failure", name, username=username)


def _check_container_alerts(name, raw_stats, container):
    try:
        usernames = zeno_db.list_usernames()
    except Exception:
        usernames = []
    if not usernames:
        usernames = ["admin"]
    for username in usernames:
        _check_container_alerts_for_user(username, name, raw_stats, container)


def _check_host_thresholds_for_user(username, cpu, mem_percent):
    thresholds = _get_user_alert_thresholds(username)
    cpu_limit = thresholds["cpu_percent"]
    mem_limit_pct = thresholds["mem_percent"]
    host_name = zeno_db.HOST_METRICS_CONTAINER

    if cpu > cpu_limit:
        zeno_db.insert_alert(
            "cpu_high",
            host_name,
            f"Host CPU above {cpu_limit}% ({cpu:.1f}%)",
            severity="warning",
            cpu_percent=round(cpu, 2),
            mem_percent=round(mem_percent, 2),
            username=username,
        )
    else:
        zeno_db.resolve_alerts("cpu_high", host_name, username=username)

    if mem_percent > mem_limit_pct:
        zeno_db.insert_alert(
            "mem_high",
            host_name,
            f"Host memory above {mem_limit_pct}% ({mem_percent:.1f}%)",
            severity="warning",
            cpu_percent=round(cpu, 2),
            mem_percent=round(mem_percent, 2),
            username=username,
        )
    else:
        zeno_db.resolve_alerts("mem_high", host_name, username=username)


def _check_host_thresholds(cpu, mem_percent):
    try:
        usernames = zeno_db.list_usernames()
    except Exception:
        usernames = []
    if not usernames:
        usernames = ["admin"]
    for username in usernames:
        _check_host_thresholds_for_user(username, cpu, mem_percent)


def _detect_state_changes():
    global _container_status_cache
    try:
        current = {c.name: c.status for c in client.containers.list(all=True)}
    except APIError:
        return

    for name, status in current.items():
        prev = _container_status_cache.get(name)
        if prev is not None and prev != status:
            zeno_db.log_system_activity(
                "state_change",
                container=name,
                details=f"{prev} → {status}",
            )
    _container_status_cache = current


def _collect_host_metrics():
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    mem_pct = mem.percent
    zeno_db.insert_metric_snapshot(
        zeno_db.HOST_METRICS_CONTAINER,
        cpu,
        mem.used / 1024 / 1024,
        mem.total / 1024 / 1024,
        disk.used,
        disk.total,
    )
    _check_host_thresholds(cpu, mem_pct)


def _collect_metrics_and_alerts():
    if not zeno_db.is_ready():
        return
    try:
        _collect_host_metrics()
        _detect_state_changes()
        for container in client.containers.list(all=True):
            name = container.name
            try:
                if container.status == "restarting":
                    _check_container_alerts(name, None, container)
                    continue
                if container.status != "running":
                    continue
                raw = _container_stats_raw(container)
                if not raw:
                    continue
                zeno_db.insert_metric_snapshot(
                    name,
                    raw["cpu"],
                    raw["mem_used_mb"],
                    raw["mem_limit_mb"],
                    raw["block_read_bytes"],
                    raw["block_write_bytes"],
                )
                _check_container_alerts(name, raw, container)
            except Exception as exc:
                print(f"monitor: {name}: {exc}")
    except Exception as exc:
        print(f"monitor loop error: {exc}")


def _monitoring_loop():
    while True:
        if METRICS_ENABLED:
            _collect_metrics_and_alerts()
        time.sleep(MONITOR_INTERVAL_SEC)


def _start_monitoring():
    if not METRICS_ENABLED:
        return
    t = threading.Thread(target=_monitoring_loop, daemon=True)
    t.start()


@app.route(f"{API_PREFIX}/containers/<name>/stats", methods=["GET"])
@requires_auth
def container_stats(name):
    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": "Container not found"}), 404

    raw = _container_stats_raw(c)
    if raw is None:
        return jsonify({
            "cpu": 0,
            "memory": "Stopped",
            "network_rx": "-",
            "network_tx": "-",
            "block_read": "-",
            "block_write": "-",
        })

    return jsonify({
        "cpu": raw["cpu"],
        "memory": raw["memory"],
        "mem_used_mb": raw["mem_used_mb"],
        "mem_limit_mb": raw["mem_limit_mb"],
        "mem_percent": round(
            (raw["mem_used_mb"] / raw["mem_limit_mb"] * 100)
            if raw["mem_limit_mb"] else 0,
            2,
        ),
        "network_rx": raw["network_rx"],
        "network_tx": raw["network_tx"],
        "block_read": raw["block_read"],
        "block_write": raw["block_write"],
        "block_read_bytes": raw["block_read_bytes"],
        "block_write_bytes": raw["block_write_bytes"],
    })


@app.route(f"{API_PREFIX}/metrics/history", methods=["GET"])
@requires_auth
def metrics_history_api():
    container = (request.args.get("container") or "").strip()
    if not container:
        return jsonify({"error": "container is required"}), 400
    hours = request.args.get("hours", 24)
    try:
        points = zeno_db.list_metric_history(container, hours=hours)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid hours parameter"}), 400
    return jsonify({"container": container, "hours": int(hours), "points": points})


@app.route(f"{API_PREFIX}/timeline", methods=["GET"])
@requires_auth
def timeline_api():
    hours = request.args.get("hours", 24)
    limit = request.args.get("limit", 200)
    try:
        events = zeno_db.list_timeline(hours=hours, limit=limit, username=current_username())
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid query parameters"}), 400
    return jsonify({"hours": int(hours), "events": events})


@app.route(f"{API_PREFIX}/alerts", methods=["GET"])
@requires_auth
def alerts_api():
    hours = request.args.get("hours", 24)
    active_only = request.args.get("active_only", "false").lower() == "true"
    containers_only = request.args.get("containers_only", "true").lower() == "true"
    username = current_username()
    try:
        alerts = zeno_db.list_alerts(
            hours=hours,
            active_only=active_only,
            containers_only=containers_only,
            username=username,
        )
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid query parameters"}), 400
    return jsonify({
        "hours": int(hours),
        "thresholds": _get_user_alert_thresholds(username),
        "alerts": alerts,
    })


@app.route(f"{API_PREFIX}/alerts/thresholds", methods=["GET"])
@requires_auth
def get_alert_thresholds_api():
    username = current_username()
    return jsonify({"thresholds": _get_user_alert_thresholds(username)})


@app.route(f"{API_PREFIX}/alerts/thresholds", methods=["PUT"])
@requires_auth
def put_alert_thresholds_api():
    body = request.get_json(force=True, silent=True) or {}
    cpu = body.get("cpu_percent")
    mem = body.get("mem_percent")
    username = current_username()
    try:
        updated = zeno_db.set_user_alert_thresholds(
            username,
            cpu_percent=cpu if cpu is not None else None,
            mem_percent=mem if mem is not None else None,
        )
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid threshold values (1–100)."}), 400
    global _user_thresholds_cache, _user_thresholds_cache_at
    _user_thresholds_cache[username] = updated
    _user_thresholds_cache_at = time.time()
    return jsonify({"thresholds": updated})


@app.route(f"{API_PREFIX}/logs/central", methods=["GET"])
@requires_auth
def central_logs_api():
    names_raw = (request.args.get("containers") or "").strip()
    if not names_raw:
        return jsonify({"error": "containers parameter is required"}), 400

    names = [n.strip() for n in names_raw.split(",") if n.strip()]
    if not names:
        return jsonify({"error": "Select at least one container"}), 400
    if len(names) > 3:
        return jsonify({"error": "Maximum 3 containers per request"}), 400

    tail = request.args.get("tail", 300)
    try:
        tail = max(1, min(int(tail), 500))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid tail parameter"}), 400

    search = (request.args.get("search") or "").strip().lower()
    merged = []

    for name in names:
        try:
            c = client.containers.get(name)
        except NotFound:
            return jsonify({"error": f"No container named {name}"}), 404

        ser = serialize(c)
        group = ser.get("group", "")
        if group != "Core Apps" and not can_manage_container_group(group):
            return jsonify({"error": f"Access denied for {name}"}), 403

        try:
            raw = c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
        except APIError as e:
            return jsonify({"error": str(e)}), 500

        for line in raw.splitlines():
            if not line.strip():
                continue
            if search and search not in line.lower():
                continue
            merged.append({"container": name, "line": line})

    merged.sort(key=lambda x: x["line"])
    return jsonify({
        "containers": names,
        "tail": tail,
        "search": search or None,
        "lines": merged,
        "count": len(merged),
    })


@app.route(f"{API_PREFIX}/containers/<name>/exec", methods=["POST"])
@requires_auth
def container_exec(name):
    body = request.get_json(force=True, silent=True) or {}
    command = (body.get("command") or "").strip()
    if not command:
        return jsonify({"error": "command is required"}), 400
    if len(command) > 4000:
        return jsonify({"error": "command is too long"}), 400

    try:
        c = client.containers.get(name)
    except NotFound:
        return jsonify({"error": f"No container named {name}"}), 404

    if c.status != "running":
        return jsonify({"error": "Container must be running to execute commands."}), 400

    if not can_manage_container_obj(c):
        return jsonify({
            "error": "Docker CLI is not available for Core Apps containers."
        }), 403

    session_key = _terminal_session_key(name)
    cwd = body.get("cwd") or _terminal_cwd.get(session_key, "/")
    if not cwd.startswith("/"):
        cwd = "/" + cwd
    cwd = posixpath.normpath(cwd) or "/"

    cmd_lower = command.strip()
    if cmd_lower == "pwd":
        return jsonify({
            "name": name,
            "command": command,
            "exit_code": 0,
            "output": cwd + "\n",
            "cwd": cwd,
        })

    if cmd_lower == "cd" or cmd_lower.startswith("cd "):
        if session_key in _terminal_shells:
            return jsonify({
                "error": "Cannot change directory while inside an interactive shell. Type exit first."
            }), 400
        new_cwd = _resolve_cd(cwd, command)
        if not _validate_dir_in_container(c, new_cwd):
            target = command.split(maxsplit=1)[1] if len(command.split(maxsplit=1)) > 1 else ""
            msg = f"/bin/sh: cd: {target}: No such file or directory\n"
            return jsonify({
                "name": name,
                "command": command,
                "exit_code": 1,
                "output": msg,
                "cwd": cwd,
                "shell": None,
                "prompt": "$",
            })
        _terminal_cwd[session_key] = new_cwd
        return jsonify({
            "name": name,
            "command": command,
            "exit_code": 0,
            "output": "",
            "cwd": new_cwd,
            "shell": None,
            "prompt": "$",
        })

    if session_key in _terminal_shells:
        shell_result = _exec_in_terminal_shell(c, session_key, command, cwd)
        if shell_result is not None:
            log_container_activity("exec", c, details=command[:200])
            return jsonify({
                "name": name,
                "command": command,
                "exit_code": shell_result["exit_code"],
                "output": shell_result["output"] or "(no output)",
                "cwd": cwd,
                "shell": shell_result.get("shell"),
                "prompt": shell_result.get("prompt") or "$",
            })

    is_interactive, parts = _is_interactive_shell_command(command)
    if is_interactive:
        try:
            shell_result = _start_terminal_shell(c, session_key, parts, cwd)
        except Exception as exc:
            return jsonify({"error": f"Failed to start shell: {exc}"}), 500
        log_container_activity("exec", c, details=command[:200])
        return jsonify({
            "name": name,
            "command": command,
            "exit_code": shell_result["exit_code"],
            "output": shell_result["output"] or "(no output)",
            "cwd": cwd,
            "shell": shell_result.get("shell"),
            "prompt": shell_result.get("prompt") or "$",
        })

    try:
        result = c.exec_run(
            ["/bin/sh", "-c", command],
            workdir=cwd,
            demux=True,
        )
        stdout = (result.output[0] or b"").decode("utf-8", errors="replace")
        stderr = (result.output[1] or b"").decode("utf-8", errors="replace")
        output = stdout + stderr
        _terminal_cwd[session_key] = cwd
        log_container_activity("exec", c, details=command[:200])
        return jsonify({
            "name": name,
            "command": command,
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "output": output or "(no output)",
            "cwd": cwd,
            "shell": None,
            "prompt": "$",
        })
    except APIError as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Host stats
# ---------------------------------------------------------------------------

@app.route(f"{API_PREFIX}/host/stats", methods=["GET"])
@requires_auth
def host_stats():
    cpu = psutil.cpu_percent(interval=None)
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


@app.route(f"{API_PREFIX}/host/details", methods=["GET"])
@requires_auth
def host_details():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    uptime_secs = time.time() - psutil.boot_time()

    docker_version = "unknown"
    try:
        docker_version = client.version().get("Version", "unknown")
    except APIError:
        pass

    try:
        load1, load5, load15 = os.getloadavg()
        load_avg = [load1, load5, load15]
    except (OSError, AttributeError):
        load_avg = [None, None, None]

    return jsonify({
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "docker_version": docker_version,
        "cpu_count": psutil.cpu_count(logical=False),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "mem_total_gb": round(mem.total / 1024**3, 2),
        "mem_used_gb": round(mem.used / 1024**3, 2),
        "mem_percent": mem.percent,
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "disk_used_gb": round(disk.used / 1024**3, 2),
        "disk_percent": disk.percent,
        "boot_time": boot.isoformat(),
        "uptime_seconds": int(uptime_secs),
        "load_avg": load_avg,
        "container_count": len(client.containers.list(all=True)),
    })


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------



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


@app.route(f"{API_PREFIX}/databases", methods=["GET"])
@requires_auth
def list_databases():
    containers = client.containers.list(all=True, filters={"label": f"{LABEL_KIND}={USER_DB_LABEL_VALUE}"})
    return jsonify([serialize(c) for c in containers])

@app.route(f"{API_PREFIX}/databases", methods=["POST"])
@requires_auth
@require_feature("create_database")
def create_database():
    body = request.get_json(force=True, silent=True) or {}

    engine = (body.get("engine") or "").strip().lower()
    name = (body.get("name") or "").strip().lower()

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    db_name = (body.get("db_name") or "").strip()

    host_port = body.get("host_port")
    tables = body.get("tables") or []
    persistent = bool(body.get("persistent", True))

    # ---------------- ENGINE VALIDATION ----------------
    if engine not in ENGINE_DEFAULTS:
        return jsonify({
            "error": "Unknown engine. Choose postgres, mysql, mongo, or redis."
        }), 400

    # ---------------- NAME VALIDATION ----------------
    if not NAME_RE.match(name):
        return jsonify({
            "error": "Name must be lowercase letters/numbers/_/- , 2-40 chars."
        }), 400

    # ---------------- REQUIRED FIELD RULES ----------------
    if not host_port:
        return jsonify({"error": "host_port is required."}), 400

    try:
        host_port = int(host_port)
    except (TypeError, ValueError):
        return jsonify({"error": "host_port must be a valid number."}), 400

    if host_port < 1:
        return jsonify({"error": "Port cannot be zero or negative."}), 400
    if host_port < 1024:
        return jsonify({"error": "Port must be at least 1024."}), 400
    if host_port > 65535:
        return jsonify({"error": "Port cannot exceed 65535."}), 400

    if not password or len(password) < 4:
        return jsonify({
            "error": "Password must be at least 4 characters."
        }), 400

    if engine != "redis":
        if not username:
            return jsonify({"error": "Username is required for this engine."}), 400
        if not db_name:
            return jsonify({"error": "Database name is required for this engine."}), 400

    # ---------------- CONTAINER NAMES ----------------
    container_name = f"zeno_userdb_{name}"
    volume_name = f"{container_name}_data"

    # ---------------- EXISTENCE CHECK ----------------
    try:
        client.containers.get(container_name)
        return jsonify({
            "error": f"A database named '{name}' already exists."
        }), 409
    except NotFound:
        pass

    if port_in_use(host_port):
        return jsonify({
            "error": f"Port {host_port} is already in use on this host."
        }), 409

    # ---------------- ENGINE CONFIG ----------------
    defaults = ENGINE_DEFAULTS[engine]
    image = defaults["image"]
    container_port = defaults["port"]
    volume_path = defaults["volume_path"]

    env = {}
    command = None

    if engine == "postgres":
        env = {
            "POSTGRES_USER": username,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": db_name
        }

    elif engine == "mysql":
        env = {
            "MYSQL_ROOT_PASSWORD": password,
            "MYSQL_DATABASE": db_name,
            "MYSQL_USER": username,
            "MYSQL_PASSWORD": password
        }

    elif engine == "mongo":
        env = {
            "MONGO_INITDB_ROOT_USERNAME": username,
            "MONGO_INITDB_ROOT_PASSWORD": password
        }

    elif engine == "redis":
        command = ["redis-server", "--requirepass", password]

    # ---------------- IMAGE CHECK ----------------
    try:
        client.images.get(image)
    except ImageNotFound:
        try:
            client.images.pull(image)
        except APIError as e:
            return jsonify({
                "error": f"Could not pull image {image}: {str(e)}"
            }), 500

    # ---------------- RUN CONTAINER ----------------
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
                "zeno.app": "true",
                "zeno.version": APP_VERSION,
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
            run_args["restart_policy"] = {"Name": "unless-stopped"}

        container = client.containers.run(**run_args)

    except APIError as e:
        return jsonify({
            "error": f"Failed to create container: {str(e)}"
        }), 500

    # ---------------- POST SETUP ----------------
    ready = _wait_until_ready(container, engine, username, password, db_name)

    table_results = []
    if ready and tables:
        table_results = _create_tables_or_collections(
            container, engine, username, password, db_name, tables
        )

    log_container_activity("create", container, details=f"database:{engine}")

    return jsonify({
        "container": serialize(container),
        "ready": ready,
        "tables": table_results,
        "warning": None if ready else (
            "Container is starting but not yet healthy. "
            "Check logs if needed."
        ),
    }), 201

@app.route(f"{API_PREFIX}/databases/<name>", methods=["DELETE"])
@requires_auth
def delete_database(name):
    container_name = name if name.startswith("zeno_userdb_") else f"zeno_userdb_{name}"
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

        log_container_activity("delete", c)
        c.remove()
        if remove_volume:
            try:
                client.volumes.get(f"{container_name}_data").remove()
            except NotFound:
                pass
    except APIError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"deleted": container_name, "volume_removed": remove_volume})


def _ubuntu_setup_script(languages):
    """Build shell script to install optional dev languages and sample files."""
    packages = ["curl", "wget", "git", "vim", "nano", "build-essential"]
    samples = []

    if "python" in languages:
        packages.extend(["python3", "python3-pip", "python3-venv"])
        samples.append(
            'cat > /workspace/samples/hello.py << \'EOF\'\n'
            'print("Hello from Python")\nEOF'
        )
    if "java" in languages:
        packages.append("default-jdk")
        samples.append(
            'cat > /workspace/samples/Hello.java << \'EOF\'\n'
            'public class Hello {\n'
            '  public static void main(String[] args) {\n'
            '    System.out.println("Hello from Java");\n'
            '  }\n'
            '}\nEOF'
        )
    if "c" in languages:
        packages.append("gcc")
        samples.append(
            'cat > /workspace/samples/hello.c << \'EOF\'\n'
            '#include <stdio.h>\n'
            'int main() {\n'
            '  printf("Hello from C\\n");\n'
            '  return 0;\n'
            '}\nEOF'
        )
    if "cpp" in languages:
        packages.append("g++")
        samples.append(
            'cat > /workspace/samples/hello.cpp << \'EOF\'\n'
            '#include <iostream>\n'
            'int main() {\n'
            '  std::cout << "Hello from C++" << std::endl;\n'
            '  return 0;\n'
            '}\nEOF'
        )
    if "go" in languages:
        packages.append("golang-go")
        samples.append(
            'mkdir -p /workspace/samples/go && '
            'cat > /workspace/samples/go/main.go << \'EOF\'\n'
            'package main\n'
            'import "fmt"\n'
            'func main() { fmt.Println("Hello from Go") }\nEOF'
        )
    if "node" in languages:
        packages.extend(["nodejs", "npm"])
        samples.append(
            'cat > /workspace/samples/hello.js << \'EOF\'\n'
            'console.log("Hello from Node.js");\nEOF'
        )
    if "rust" in languages:
        samples.append(
            'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | '
            'sh -s -- -y && . $HOME/.cargo/env'
        )
        samples.append(
            'cat > /workspace/samples/hello.rs << \'EOF\'\n'
            'fn main() { println!("Hello from Rust"); }\nEOF'
        )

    pkg_line = " ".join(sorted(set(packages)))
    sample_cmds = "\n".join(samples) if samples else "true"
    return f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq {pkg_line}
mkdir -p /workspace/samples
{sample_cmds}
touch /workspace/.ready
"""


@app.route(f"{API_PREFIX}/servers/ubuntu", methods=["POST"])
@requires_auth
@require_feature("create_ubuntu")
def create_ubuntu_server():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip().lower()
    persistent = bool(body.get("persistent", True))
    raw_langs = body.get("languages") or []
    languages = [l for l in raw_langs if l in VALID_LANGUAGES]

    if not re.match(r"^[a-z0-9][a-z0-9_-]{1,30}$", name):
        return jsonify({
            "error": "Name must be 2-31 chars: lowercase letters, numbers, _ or -"
        }), 400

    container_name = f"zeno_ubuntu_{name}"
    try:
        client.containers.get(container_name)
        return jsonify({"error": f"Server {name} already exists."}), 409
    except NotFound:
        pass

    labels = {
        LABEL_KIND: USER_SERVER_LABEL_VALUE,
        "zeno.name": name,
        "zeno.created_by": current_username(),
    }
    if languages:
        labels["zeno.languages"] = ",".join(languages)

    volumes = {}
    if persistent:
        vol_name = f"{container_name}_workspace"
        try:
            client.volumes.get(vol_name)
        except NotFound:
            client.volumes.create(name=vol_name)
        volumes[vol_name] = {"bind": "/workspace", "mode": "rw"}

    try:
        container = client.containers.run(
            UBUNTU_IMAGE,
            name=container_name,
            detach=True,
            tty=True,
            stdin_open=True,
            command=["/bin/bash", "-c", "sleep infinity"],
            volumes=volumes,
            labels=labels,
            restart_policy={"Name": "unless-stopped"},
        )
    except APIError as e:
        return jsonify({"error": str(e)}), 500

    setup = _ubuntu_setup_script(languages)
    try:
        container.exec_run(
            ["/bin/bash", "-c", setup],
            detach=False,
        )
    except APIError:
        pass

    container.reload()
    log_container_activity(
        "create", container, details=f"ubuntu:{','.join(languages) or 'base'}"
    )
    return jsonify({
        "container": serialize(container),
        "languages": languages,
        "workspace": "/workspace",
    }), 201


@app.route(f"{API_PREFIX}/servers/web", methods=["POST"])
@requires_auth
@require_feature("create_web_server")
def create_web_server():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip().lower()
    server_type = (body.get("type") or "nginx").strip().lower()
    host_port = body.get("host_port")
    persistent = bool(body.get("persistent", False))

    if not re.match(r"^[a-z0-9][a-z0-9_-]{1,30}$", name):
        return jsonify({
            "error": "Name must be 2-31 chars: lowercase letters, numbers, _ or -"
        }), 400

    if server_type not in WEB_SERVER_DEFAULTS:
        types = ", ".join(sorted(WEB_SERVER_DEFAULTS))
        return jsonify({"error": f"Type must be one of: {types}"}), 400

    spec = WEB_SERVER_DEFAULTS[server_type]
    container_name = f"zeno_web_{name}"
    try:
        client.containers.get(container_name)
        return jsonify({"error": f"Web server {name} already exists."}), 409
    except NotFound:
        pass

    if host_port is not None:
        try:
            host_port = int(host_port)
            if host_port < 1 or host_port > 65535:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({"error": "Host port must be 1-65535."}), 400
        err = port_in_use_error(host_port)
        if err:
            return jsonify({"error": err}), 409
    else:
        host_port = find_free_port(8080, 8999)
        if host_port is None:
            return jsonify({"error": "No free port in range 8080-8999."}), 503

    labels = {
        LABEL_KIND: USER_WEB_LABEL_VALUE,
        "zeno.name": name,
        "zeno.web_type": server_type,
        "zeno.created_by": current_username(),
    }

    volumes = {}
    if persistent and server_type in ("nginx", "apache", "caddy"):
        vol_name = f"{container_name}_html"
        try:
            client.volumes.get(vol_name)
        except NotFound:
            client.volumes.create(name=vol_name)
        mount_path = {
            "nginx": "/usr/share/nginx/html",
            "apache": "/usr/local/apache2/htdocs",
            "caddy": "/usr/share/caddy",
        }[server_type]
        volumes[vol_name] = {"bind": mount_path, "mode": "rw"}

    port_key = f"{spec['port']}/tcp"
    try:
        container = client.containers.run(
            spec["image"],
            name=container_name,
            detach=True,
            ports={port_key: host_port},
            volumes=volumes,
            labels=labels,
            restart_policy={"Name": "unless-stopped"},
        )
    except APIError as e:
        return jsonify({"error": str(e)}), 500

    container.reload()
    log_container_activity("create", container, details=f"web:{server_type}")
    return jsonify({
        "container": serialize(container),
        "url": f"http://localhost:{host_port}",
        "type": server_type,
    }), 201


@app.route("/", methods=["GET"])
@requires_auth
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    zeno_db.wait_for_db(DASH_USER or "admin", DASH_PASS or "admin")
    _start_monitoring()
    app.run(host="0.0.0.0", port=9090)