from flask import jsonify, request
from docker.errors import NotFound, APIError

from .constants import UBUNTU_IMAGE, UBUNTU_NAME_RE, USER_SERVER_LABEL_VALUE
from .helpers import filter_languages, ubuntu_setup_script


def register_routes(app, client, deps):
    api_prefix = deps["api_prefix"]
    label_kind = deps["label_kind"]
    requires_auth = deps["requires_auth"]
    require_feature = deps["require_feature"]
    serialize = deps["serialize"]
    log_container_activity = deps["log_container_activity"]
    current_username = deps["current_username"]

    @app.route(f"{api_prefix}/servers/ubuntu", methods=["POST"])
    @requires_auth
    @require_feature("create_ubuntu")
    def create_ubuntu_server():
        body = request.get_json(force=True, silent=True) or {}
        name = (body.get("name") or "").strip().lower()
        persistent = bool(body.get("persistent", True))
        raw_langs = body.get("languages") or []
        languages = filter_languages(raw_langs)

        if not UBUNTU_NAME_RE.match(name):
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
            label_kind: USER_SERVER_LABEL_VALUE,
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

        setup = ubuntu_setup_script(languages)
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
