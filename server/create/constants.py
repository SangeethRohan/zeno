import re

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

ENGINE_DEFAULTS = {
    "postgres": {
        "image": "postgres:16",
        "port": 5432,
        "volume_path": "/var/lib/postgresql/data",
    },
    "mysql": {
        "image": "mysql:8",
        "port": 3306,
        "volume_path": "/var/lib/mysql",
    },
    "mongo": {
        "image": "mongo:latest",
        "port": 27017,
        "volume_path": "/data/db",
    },
    "redis": {
        "image": "redis:latest",
        "port": 6379,
        "volume_path": "/data",
    },
}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
UBUNTU_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,30}$")

DB_HOST_PORT_START = 5500
DB_HOST_PORT_END = 5999
WEB_HOST_PORT_START = 8080
WEB_HOST_PORT_END = 8999
