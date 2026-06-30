from flask import jsonify, request
from docker.errors import NotFound, APIError, ImageNotFound

from .constants import (
    DB_HOST_PORT_END,
    DB_HOST_PORT_START,
    ENGINE_DEFAULTS,
    NAME_RE,
    USER_DB_LABEL_VALUE,
)
from .helpers import create_tables_or_collections, wait_until_ready


def register_routes(app, client, deps):
    api_prefix = deps["api_prefix"]
    app_version = deps["app_version"]
    label_kind = deps["label_kind"]
    label_engine = deps["label_engine"]
    requires_auth = deps["requires_auth"]
    require_feature = deps["require_feature"]
    serialize = deps["serialize"]
    log_container_activity = deps["log_container_activity"]
    port_in_use_error = deps["port_in_use_error"]
    find_free_port = deps["find_free_port"]

    @app.route(f"{api_prefix}/databases", methods=["GET"])
    @requires_auth
    def list_databases():
        containers = client.containers.list(
            all=True, filters={"label": f"{label_kind}={USER_DB_LABEL_VALUE}"}
        )
        return jsonify([serialize(c) for c in containers])

    @app.route(f"{api_prefix}/databases", methods=["POST"])
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

        if engine not in ENGINE_DEFAULTS:
            return jsonify({
                "error": "Unknown engine. Choose postgres, mysql, mongo, or redis."
            }), 400

        if not NAME_RE.match(name):
            return jsonify({
                "error": "Name must be lowercase letters/numbers/_/- , 2-40 chars."
            }), 400

        if host_port is None or host_port == "":
            host_port = find_free_port(DB_HOST_PORT_START, DB_HOST_PORT_END)
            if host_port is None:
                return jsonify({
                    "error": (
                        f"No free port in range "
                        f"{DB_HOST_PORT_START}-{DB_HOST_PORT_END}."
                    )
                }), 503
        else:
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

        container_name = f"zeno_userdb_{name}"
        volume_name = f"{container_name}_data"

        try:
            client.containers.get(container_name)
            return jsonify({
                "error": f"A database named '{name}' already exists."
            }), 409
        except NotFound:
            pass

        port_err = port_in_use_error(host_port)
        if port_err:
            return jsonify({"error": port_err}), 409

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
                "POSTGRES_DB": db_name,
            }
        elif engine == "mysql":
            env = {
                "MYSQL_ROOT_PASSWORD": password,
                "MYSQL_DATABASE": db_name,
                "MYSQL_USER": username,
                "MYSQL_PASSWORD": password,
            }
        elif engine == "mongo":
            env = {
                "MONGO_INITDB_ROOT_USERNAME": username,
                "MONGO_INITDB_ROOT_PASSWORD": password,
            }
        elif engine == "redis":
            command = ["redis-server", "--requirepass", password]

        try:
            client.images.get(image)
        except ImageNotFound:
            try:
                client.images.pull(image)
            except APIError as e:
                return jsonify({
                    "error": f"Could not pull image {image}: {str(e)}"
                }), 500

        try:
            run_args = {
                "image": image,
                "name": container_name,
                "command": command,
                "environment": env,
                "ports": {f"{container_port}/tcp": host_port},
                "labels": {
                    label_kind: USER_DB_LABEL_VALUE,
                    label_engine: engine,
                    "zeno.app": "true",
                    "zeno.version": app_version,
                    "stackcontrol.persistent": str(persistent).lower(),
                },
                "detach": True,
            }

            if persistent:
                client.volumes.create(name=volume_name)
                run_args["volumes"] = {
                    volume_name: {
                        "bind": volume_path,
                        "mode": "rw",
                    }
                }
                run_args["restart_policy"] = {"Name": "unless-stopped"}

            container = client.containers.run(**run_args)

        except APIError as e:
            return jsonify({
                "error": f"Failed to create container: {str(e)}"
            }), 500

        ready = wait_until_ready(container, engine, username, password, db_name)

        table_results = []
        if ready and tables:
            table_results = create_tables_or_collections(
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

    @app.route(f"{api_prefix}/databases/<name>", methods=["DELETE"])
    @requires_auth
    def delete_database(name):
        container_name = (
            name if name.startswith("zeno_userdb_") else f"zeno_userdb_{name}"
        )
        remove_volume = request.args.get("remove_volume", "false").lower() == "true"
        try:
            c = client.containers.get(container_name)
        except NotFound:
            return jsonify({"error": f"No database container named {name}"}), 404

        labels = c.attrs.get("Config", {}).get("Labels") or {}
        if labels.get(label_kind) != USER_DB_LABEL_VALUE:
            return jsonify({
                "error": "This container was not created by the dashboard; refusing to delete."
            }), 400

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
