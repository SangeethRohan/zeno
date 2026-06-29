import os
import re
import time
from datetime import datetime, timezone, timedelta

from werkzeug.security import check_password_hash, generate_password_hash
from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError

MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb://zeno:zenopass@zeno_mongo:27017/zeno?authSource=admin",
)
DB_NAME = os.environ.get("MONGO_DB", "zeno")
APP_TIERS = ("Core", "Pro", "Elite")
PRIMARY_ADMIN_TIER = "Elite"
DEFAULT_TIER = os.environ.get("APP_TIER", "Core")
if DEFAULT_TIER not in APP_TIERS:
    DEFAULT_TIER = "Core"

HOST_METRICS_CONTAINER = "__host__"
METRICS_TTL_SECONDS = 7 * 24 * 3600
ALERT_DEDUP_MINUTES = 15
ALERT_NOTIFICATION_RULES = ("cpu_high", "mem_high", "crash_loop", "port_failure")
DEFAULT_ALERT_NOTIFICATIONS = {rule: True for rule in ALERT_NOTIFICATION_RULES}
ALERT_NOTIFICATION_LABELS = {
    "cpu_high": "CPU alerts",
    "mem_high": "Memory alerts",
    "crash_loop": "Crash loop alerts",
    "port_failure": "Port failure alerts",
}
DEFAULT_ALERT_THRESHOLDS = {"cpu_percent": 90, "mem_percent": 90}

FEATURE_KEYS = ("create_database", "create_ubuntu", "create_web_server")
FEATURE_LABELS = {
    "create_database": "Create Database",
    "create_ubuntu": "Create Ubuntu Server",
    "create_web_server": "Create Web Server",
}

_client = None
_ready = False
_primary_admin = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client[DB_NAME]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db(admin_user="admin", admin_pass="admin"):
    global _ready, _primary_admin
    _primary_admin = admin_user
    db = get_db()
    db.client.admin.command("ping")

    if db.users.count_documents({}) == 0:
        db.users.insert_one({
            "username": admin_user,
            "password_hash": generate_password_hash(admin_pass),
            "role": "admin",
            "tier": PRIMARY_ADMIN_TIER,
            "created_at": _now_iso(),
            "created_by": "system",
            "is_primary": True,
        })

    db.users.update_many(
        {"tier": {"$exists": False}},
        {"$set": {"tier": DEFAULT_TIER}},
    )
    db.users.update_many(
        {"alert_notifications": {"$exists": False}},
        {"$set": {"alert_notifications": dict(DEFAULT_ALERT_NOTIFICATIONS)}},
    )
    db.users.update_one(
        {"username": admin_user},
        {"$set": {"is_primary": True, "tier": PRIMARY_ADMIN_TIER}},
    )
    db.users.update_many(
        {"username": {"$ne": admin_user}},
        {"$unset": {"is_primary": ""}},
    )

    if db.settings.count_documents({"key": "app_tier"}) == 0:
        db.settings.insert_one({"key": "app_tier", "value": DEFAULT_TIER})

    if db.settings.count_documents({"key": "tier_features"}) == 0:
        db.settings.insert_one({
            "key": "tier_features",
            "value": default_tier_features(),
        })

    if db.settings.count_documents({"key": "alert_thresholds"}) == 0:
        db.settings.insert_one({
            "key": "alert_thresholds",
            "value": dict(DEFAULT_ALERT_THRESHOLDS),
        })

    db.activity_log.create_index([("username", 1), ("ts", DESCENDING)])
    db.activity_log.create_index([("ts", DESCENDING)])
    db.group_layouts.create_index([("username", 1)], unique=True)
    db.metrics_history.create_index([("container", 1), ("ts", DESCENDING)])
    db.metrics_history.create_index(
        "ts", expireAfterSeconds=METRICS_TTL_SECONDS
    )
    db.alerts.create_index([("container", 1), ("ts", DESCENDING)])
    db.alerts.create_index([("rule", 1), ("container", 1), ("resolved", 1)])

    _ready = True


def wait_for_db(admin_user="admin", admin_pass="admin", attempts=40, delay=1):
    last_error = None
    for _ in range(attempts):
        try:
            init_db(admin_user, admin_pass)
            return True
        except (PyMongoError, OSError) as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"MongoDB not ready: {last_error}")


def is_ready():
    return _ready


def authenticate(username, password):
    user = get_db().users.find_one({"username": username})
    if not user or not check_password_hash(user["password_hash"], password):
        return None
    return user


def get_user(username):
    user = get_db().users.find_one({"username": username}, {"password_hash": 0})
    if not user:
        return None
    user.pop("_id", None)
    if "tier" not in user:
        user["tier"] = DEFAULT_TIER
    user["is_primary"] = bool(user.get("is_primary"))
    return user


def is_primary_user(username):
    user = get_db().users.find_one({"username": username}, {"is_primary": 1})
    return bool(user and user.get("is_primary"))


def change_password(username, current_password, new_password):
    user = authenticate(username, current_password)
    if not user:
        raise ValueError("Current password is incorrect")
    if not new_password or len(new_password) < 4:
        raise ValueError("New password must be at least 4 characters")
    get_db().users.update_one(
        {"username": username},
        {"$set": {"password_hash": generate_password_hash(new_password)}},
    )


def admin_set_password(username, new_password):
    if not get_user(username):
        raise ValueError("User not found")
    if not new_password or len(new_password) < 4:
        raise ValueError("Password must be at least 4 characters")
    get_db().users.update_one(
        {"username": username},
        {"$set": {"password_hash": generate_password_hash(new_password)}},
    )


def get_user_tier(username):
    user = get_db().users.find_one({"username": username}, {"tier": 1})
    if not user:
        return DEFAULT_TIER
    tier = user.get("tier", DEFAULT_TIER)
    return tier if tier in APP_TIERS else DEFAULT_TIER


def list_users():
    users = list(get_db().users.find({}, {"password_hash": 0}).sort("username", 1))
    for u in users:
        u.pop("_id", None)
        if "tier" not in u:
            u["tier"] = DEFAULT_TIER
        u["is_primary"] = bool(u.get("is_primary"))
    return users


def _activity_counts_for_user(username):
    db = get_db()
    created = db.activity_log.count_documents({
        "username": username,
        "action": "create",
    })
    deleted = db.activity_log.count_documents({
        "username": username,
        "action": "delete",
    })
    operations = db.activity_log.count_documents({
        "username": username,
        "action": {"$in": ["start", "stop", "restart", "exec"]},
    })
    return {
        "containers_created": created,
        "containers_deleted": deleted,
        "operations": operations,
    }


def list_users_with_stats():
    users = list_users()
    for u in users:
        u.update(_activity_counts_for_user(u["username"]))
    return users


def create_user(username, password, role="user", created_by=None, tier=None):
    if get_db().users.find_one({"username": username}):
        raise ValueError("User already exists")
    if role not in ("admin", "user"):
        raise ValueError("Invalid role")
    if tier is None:
        tier = get_app_tier()
    if tier not in APP_TIERS:
        raise ValueError("Invalid tier")
    get_db().users.insert_one({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
        "tier": tier,
        "alert_notifications": dict(DEFAULT_ALERT_NOTIFICATIONS),
        "created_at": _now_iso(),
        "created_by": created_by,
    })


def update_user(username, *, tier=None, role=None):
    user = get_db().users.find_one({"username": username})
    if not user:
        raise ValueError("User not found")
    if user.get("is_primary") and (tier is not None or role is not None):
        raise ValueError("Primary admin role and tier cannot be changed")
    updates = {}
    if tier is not None:
        if tier not in APP_TIERS:
            raise ValueError("Invalid tier")
        updates["tier"] = tier
    if role is not None:
        if role not in ("admin", "user"):
            raise ValueError("Invalid role")
        updates["role"] = role
    if not updates:
        return
    get_db().users.update_one({"username": username}, {"$set": updates})


def bulk_set_tier(usernames, tier):
    if tier not in APP_TIERS:
        raise ValueError("Invalid tier")
    updated = []
    errors = []
    for name in usernames:
        if is_primary_user(name):
            errors.append({
                "username": name,
                "error": "Primary admin tier cannot be changed",
            })
            continue
        try:
            update_user(name, tier=tier)
            updated.append(name)
        except ValueError as e:
            errors.append({"username": name, "error": str(e)})
    return updated, errors


def delete_user(username):
    user = get_db().users.find_one({"username": username})
    if not user:
        raise ValueError("User not found")
    if user.get("is_primary"):
        raise ValueError("The primary admin account cannot be deleted")
    if user.get("role") == "admin":
        admin_count = get_db().users.count_documents({"role": "admin"})
        if admin_count <= 1:
            raise ValueError("Cannot delete the last admin")
    get_db().users.delete_one({"username": username})


def bulk_delete_users(usernames):
    deleted = []
    errors = []
    for name in usernames:
        try:
            delete_user(name)
            deleted.append(name)
        except ValueError as e:
            errors.append({"username": name, "error": str(e)})
    return deleted, errors


def get_app_tier():
    doc = get_db().settings.find_one({"key": "app_tier"})
    tier = doc.get("value", DEFAULT_TIER) if doc else DEFAULT_TIER
    return tier if tier in APP_TIERS else DEFAULT_TIER


def set_app_tier(tier):
    if tier not in APP_TIERS:
        raise ValueError(f"Invalid tier: {tier}")
    get_db().settings.update_one(
        {"key": "app_tier"},
        {"$set": {"value": tier}},
        upsert=True,
    )


def default_tier_features():
    return {
        tier: {key: True for key in FEATURE_KEYS}
        for tier in APP_TIERS
    }


def get_tier_features_map():
    doc = get_db().settings.find_one({"key": "tier_features"})
    merged = default_tier_features()
    if doc and isinstance(doc.get("value"), dict):
        for tier in APP_TIERS:
            tier_vals = doc["value"].get(tier)
            if isinstance(tier_vals, dict):
                for key in FEATURE_KEYS:
                    if key in tier_vals:
                        merged[tier][key] = bool(tier_vals[key])
    return merged


def set_tier_features_map(feature_map):
    cleaned = default_tier_features()
    for tier in APP_TIERS:
        tier_vals = feature_map.get(tier) if feature_map else None
        if isinstance(tier_vals, dict):
            for key in FEATURE_KEYS:
                if key in tier_vals:
                    cleaned[tier][key] = bool(tier_vals[key])
    get_db().settings.update_one(
        {"key": "tier_features"},
        {"$set": {"value": cleaned}},
        upsert=True,
    )
    return cleaned


def features_for_user(username, role="user"):
    if role == "admin":
        return {key: True for key in FEATURE_KEYS}
    tier = get_user_tier(username)
    return get_tier_features_map().get(tier, {key: True for key in FEATURE_KEYS})


def log_activity(username, action, container=None, container_image=None, details=None):
    get_db().activity_log.insert_one({
        "username": username,
        "action": action,
        "container": container or "",
        "container_image": container_image or "",
        "details": details or "",
        "ts": _now_iso(),
    })


def list_activity(username=None, limit=100, skip=0):
    limit = max(1, min(int(limit), 500))
    skip = max(0, int(skip))
    query = {}
    if username:
        query["username"] = username
    cursor = (
        get_db().activity_log.find(query, {"_id": 0})
        .sort("ts", DESCENDING)
        .skip(skip)
        .limit(limit)
    )
    return list(cursor)


def activity_summary():
    """Per-user counts for admin dashboard."""
    db = get_db()
    users = list_users()
    summary = []
    for u in users:
        row = {
            "username": u["username"],
            "role": u.get("role", "user"),
            "tier": u.get("tier", DEFAULT_TIER),
            "created_at": u.get("created_at"),
            "created_by": u.get("created_by"),
            "is_primary": bool(u.get("is_primary")),
        }
        row.update(_activity_counts_for_user(u["username"]))
        summary.append(row)
    return summary


CORE_GROUP_ID = "core-apps"
CORE_GROUP_NAME = "Core Apps"


def _slug_group(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "group"


def build_default_layout(container_items):
    """Build layout from container default_group values."""
    groups = [{
        "id": CORE_GROUP_ID,
        "name": CORE_GROUP_NAME,
        "locked": True,
        "order": 0,
    }]
    assignments = {}
    seen = {CORE_GROUP_ID: groups[0]}
    order = 1

    for item in container_items:
        name = item["name"]
        default = item.get("default_group") or item.get("group") or "Other"
        if item.get("is_core_app") or default == CORE_GROUP_NAME:
            assignments[name] = CORE_GROUP_ID
            continue
        gid = _slug_group(default)
        base = gid
        n = 2
        while gid in seen and seen[gid]["name"] != default:
            gid = f"{base}-{n}"
            n += 1
        if gid not in seen:
            seen[gid] = {
                "id": gid,
                "name": default,
                "locked": False,
                "order": order,
            }
            groups.append(seen[gid])
            order += 1
        assignments[name] = gid

    groups.sort(key=lambda g: g["order"])
    container_order = _sync_container_order(assignments, {})
    return {"groups": groups, "assignments": assignments, "container_order": container_order}


def _sync_container_order(assignments, container_order):
    """Ensure container_order lists match assignments."""
    order = dict(container_order or {})
    by_group = {}
    for name, gid in assignments.items():
        by_group.setdefault(gid, [])
        if name not in by_group[gid]:
            by_group[gid].append(name)
    for gid, names in by_group.items():
        existing = [n for n in order.get(gid, []) if n in names]
        for n in names:
            if n not in existing:
                existing.append(n)
        order[gid] = existing
    return order


def get_group_layout(username):
    doc = get_db().group_layouts.find_one({"username": username})
    if not doc:
        return None
    return doc.get("layout")


def save_group_layout(username, layout):
    groups = layout.get("groups") or []
    core = next((g for g in groups if g.get("id") == CORE_GROUP_ID), None)
    if not core or not core.get("locked"):
        raise ValueError("Layout must include the locked Core Apps group.")

    for g in groups:
        if not g.get("id") or not g.get("name"):
            raise ValueError("Each group needs an id and name.")
        g["locked"] = g.get("id") == CORE_GROUP_ID

    get_db().group_layouts.update_one(
        {"username": username},
        {"$set": {"layout": layout, "updated_at": _now_iso()}},
        upsert=True,
    )


def get_or_create_layout(username, container_items):
    layout = get_group_layout(username)
    if layout:
        return merge_layout_with_containers(layout, container_items)
    layout = build_default_layout(container_items)
    save_group_layout(username, layout)
    return layout


def merge_layout_with_containers(layout, container_items):
    """Add new containers / groups; keep core apps locked."""
    groups = list(layout.get("groups") or [])
    assignments = dict(layout.get("assignments") or {})
    by_id = {g["id"]: g for g in groups}

    if CORE_GROUP_ID not in by_id:
        groups.insert(0, {
            "id": CORE_GROUP_ID,
            "name": CORE_GROUP_NAME,
            "locked": True,
            "order": 0,
        })
        by_id[CORE_GROUP_ID] = groups[0]

    max_order = max((g.get("order", 0) for g in groups), default=0)

    for item in container_items:
        name = item["name"]
        if item.get("is_core_app"):
            assignments[name] = CORE_GROUP_ID
            continue
        if name in assignments and assignments[name] in by_id:
            continue
        default = item.get("default_group") or "Other"
        gid = _slug_group(default)
        if gid not in by_id:
            max_order += 1
            by_id[gid] = {
                "id": gid,
                "name": default,
                "locked": False,
                "order": max_order,
            }
            groups.append(by_id[gid])
        assignments[name] = gid

    groups.sort(key=lambda g: g.get("order", 0))
    container_order = _sync_container_order(
        assignments, layout.get("container_order", {})
    )
    return {"groups": groups, "assignments": assignments, "container_order": container_order}


def apply_layout_to_containers(container_items, layout):
    by_id = {g["id"]: g for g in layout.get("groups", [])}
    for item in container_items:
        if item.get("is_core_app"):
            item["group"] = CORE_GROUP_NAME
            item["group_id"] = CORE_GROUP_ID
            continue
        gid = layout.get("assignments", {}).get(item["name"])
        grp = by_id.get(gid)
        if grp:
            item["group"] = grp["name"]
            item["group_id"] = gid
        else:
            item["group"] = item.get("default_group", "Other")
            item["group_id"] = gid or "other"
    return container_items


def _metric_doc_ts(doc):
    ts = doc.get("ts")
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts or "")


def insert_metric_snapshot(
    container,
    cpu,
    mem_used_mb,
    mem_limit_mb,
    block_read_bytes,
    block_write_bytes,
):
    now = datetime.now(timezone.utc)
    get_db().metrics_history.insert_one({
        "container": container,
        "ts": now,
        "cpu": float(cpu or 0),
        "mem_used_mb": float(mem_used_mb or 0),
        "mem_limit_mb": float(mem_limit_mb or 0),
        "block_read_bytes": int(block_read_bytes or 0),
        "block_write_bytes": int(block_write_bytes or 0),
    })


def list_metric_history(container, hours=24):
    hours = max(1, min(int(hours), 168))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    cursor = (
        get_db().metrics_history.find(
            {"container": container, "ts": {"$gte": since}},
            {"_id": 0},
        )
        .sort("ts", 1)
    )
    results = []
    for doc in cursor:
        doc["ts"] = _metric_doc_ts(doc)
        if doc.get("mem_limit_mb"):
            doc["mem_percent"] = round(
                doc["mem_used_mb"] / doc["mem_limit_mb"] * 100, 2
            )
        else:
            doc["mem_percent"] = 0
        results.append(doc)
    return results


def find_unresolved_alert(rule, container):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ALERT_DEDUP_MINUTES)
    return get_db().alerts.find_one({
        "rule": rule,
        "container": container,
        "resolved": False,
        "ts": {"$gte": cutoff},
    })


def get_alert_thresholds():
    doc = get_db().settings.find_one({"key": "alert_thresholds"})
    merged = dict(DEFAULT_ALERT_THRESHOLDS)
    if doc and isinstance(doc.get("value"), dict):
        for key in DEFAULT_ALERT_THRESHOLDS:
            val = doc["value"].get(key)
            if isinstance(val, (int, float)):
                merged[key] = max(1, min(int(val), 100))
    return merged


def set_alert_thresholds(cpu_percent=None, mem_percent=None):
    current = get_alert_thresholds()
    if cpu_percent is not None:
        current["cpu_percent"] = max(1, min(int(cpu_percent), 100))
    if mem_percent is not None:
        current["mem_percent"] = max(1, min(int(mem_percent), 100))
    get_db().settings.update_one(
        {"key": "alert_thresholds"},
        {"$set": {"value": current}},
        upsert=True,
    )
    return current


def get_alert_notifications(username):
    user = get_db().users.find_one(
        {"username": username}, {"alert_notifications": 1}
    )
    merged = dict(DEFAULT_ALERT_NOTIFICATIONS)
    if user and isinstance(user.get("alert_notifications"), dict):
        for key in ALERT_NOTIFICATION_RULES:
            if key in user["alert_notifications"]:
                merged[key] = bool(user["alert_notifications"][key])
    return merged


def set_alert_notifications(username, notifications):
    if not get_user(username):
        raise ValueError("User not found")
    cleaned = dict(DEFAULT_ALERT_NOTIFICATIONS)
    if isinstance(notifications, dict):
        for key in ALERT_NOTIFICATION_RULES:
            if key in notifications:
                cleaned[key] = bool(notifications[key])
    get_db().users.update_one(
        {"username": username},
        {"$set": {"alert_notifications": cleaned}},
    )
    return cleaned


def insert_alert(
    rule,
    container,
    message,
    severity="warning",
    cpu_percent=None,
    mem_percent=None,
):
    if find_unresolved_alert(rule, container):
        return None
    now = datetime.now(timezone.utc)
    doc = {
        "rule": rule,
        "container": container,
        "message": message,
        "severity": severity,
        "cpu_percent": cpu_percent,
        "mem_percent": mem_percent,
        "ts": now,
        "resolved": False,
        "resolved_at": None,
    }
    get_db().alerts.insert_one(doc)
    doc["ts"] = now.isoformat()
    doc.pop("_id", None)
    return doc


def resolve_alerts(rule, container):
    now = datetime.now(timezone.utc)
    get_db().alerts.update_many(
        {"rule": rule, "container": container, "resolved": False},
        {"$set": {"resolved": True, "resolved_at": now}},
    )


def list_alerts(hours=24, active_only=False, containers_only=False):
    hours = max(1, min(int(hours), 168))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = {"ts": {"$gte": since}}
    if active_only:
        query["resolved"] = False
    if containers_only:
        query["container"] = {
            "$exists": True,
            "$nin": ["", HOST_METRICS_CONTAINER],
        }
    cursor = (
        get_db().alerts.find(query, {"_id": 0})
        .sort("ts", DESCENDING)
        .limit(500)
    )
    results = []
    for doc in cursor:
        doc["ts"] = _metric_doc_ts(doc)
        if doc.get("resolved_at") and isinstance(doc["resolved_at"], datetime):
            doc["resolved_at"] = doc["resolved_at"].isoformat()
        results.append(doc)
    return results


def _timeline_action_title(action, container):
    labels = {
        "create": "Container created",
        "delete": "Container deleted",
        "start": "Container started",
        "stop": "Container stopped",
        "restart": "Container restarted",
        "exec": "CLI command",
        "state_change": "State changed",
    }
    base = labels.get(action, action)
    if container:
        return f"{base}: {container}"
    return base


def list_timeline(hours=24, limit=200):
    hours = max(1, min(int(hours), 168))
    limit = max(1, min(int(limit), 500))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_iso = since.isoformat()
    events = []

    for entry in get_db().activity_log.find(
        {"ts": {"$gte": since_iso}}, {"_id": 0}
    ):
        action = entry.get("action", "")
        container = entry.get("container") or ""
        evt_type = "state" if action == "state_change" else "operation"
        events.append({
            "ts": entry.get("ts", ""),
            "type": evt_type,
            "title": _timeline_action_title(action, container),
            "detail": entry.get("details") or entry.get("container_image") or "",
            "container": container or None,
            "severity": "info",
            "username": entry.get("username"),
        })

    for alert in list_alerts(hours=hours, active_only=False):
        events.append({
            "ts": alert["ts"],
            "type": "alert",
            "title": alert["message"],
            "detail": alert.get("rule", ""),
            "container": alert.get("container"),
            "severity": alert.get("severity", "warning"),
            "resolved": alert.get("resolved", False),
        })

    events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return events[:limit]


def log_system_activity(action, container=None, details=None):
    log_activity("system", action, container=container, details=details)

