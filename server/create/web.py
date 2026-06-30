from flask import jsonify, request
from docker.errors import NotFound, APIError

from .constants import (
    UBUNTU_NAME_RE,
    USER_WEB_LABEL_VALUE,
    WEB_HOST_PORT_END,
    WEB_HOST_PORT_START,
    WEB_SERVER_DEFAULTS,
)


def register_routes(app, client, deps):
    api_prefix = deps["api_prefix"]
    label_kind = deps["label_kind"]
    requires_auth = deps["requires_auth"]
    require_feature = deps["require_feature"]
    serialize = deps["serialize"]
    log_container_activity = deps["log_container_activity"]
    current_username = deps["current_username"]
    port_in_use_error = deps["port_in_use_error"]
    find_free_port = deps["find_free_port"]

    @app.route(f"{api_prefix}/servers/web", methods=["POST"])
    @requires_auth
    @require_feature("create_web_server")
    def create_web_server():
        body = request.get_json(force=True, silent=True) or {}
        name = (body.get("name") or "").strip().lower()
        server_type = (body.get("type") or "nginx").strip().lower()
        host_port = body.get("host_port")
        persistent = bool(body.get("persistent", False))

        if not UBUNTU_NAME_RE.match(name):
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
            host_port = find_free_port(WEB_HOST_PORT_START, WEB_HOST_PORT_END)
            if host_port is None:
                return jsonify({
                    "error": (
                        f"No free port in range "
                        f"{WEB_HOST_PORT_START}-{WEB_HOST_PORT_END}."
                    )
                }), 503

        labels = {
            label_kind: USER_WEB_LABEL_VALUE,
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
