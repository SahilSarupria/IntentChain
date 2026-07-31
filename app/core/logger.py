"""
IntentChain Activity Logger
In-memory log store with ring buffer + cumulative stats + metrics for the
Grafana-style dashboard (both the in-app one and the real Prometheus/Grafana
stack under monitoring/).
"""
import time
import uuid
from collections import deque
from typing import Any
import threading

# Max entries kept in memory
MAX_LOG_ENTRIES = 500

# How many 1-minute buckets of history to keep for charts (2h)
TIMESERIES_MAX_BUCKETS = 120

# A "user" is considered active if we've seen a request from them within
# this many seconds. Identified by client IP (always available) — falls
# back gracefully since there's no auth/session layer in this app.
ACTIVE_USER_WINDOW_SECONDS = 300

_logs: deque = deque(maxlen=MAX_LOG_ENTRIES)
_stats = {
    "total_requests": 0,
    "successful_parses": 0,
    "failed_parses": 0,
    "txs_built": 0,
    "txs_sent": 0,
    "txs_rejected": 0,
    "total_latency_ms": 0.0,
    "latency_samples": 0,
    "server_start": time.time(),
}
_stats_lock = threading.Lock()

# event_type -> count, and per-action / per-network breakdowns
_by_event_type: dict[str, int] = {}
_by_action: dict[str, int] = {}
_by_network: dict[str, int] = {}
_breakdown_lock = threading.Lock()

# identifier (client IP) -> last-seen unix timestamp
_active_users: dict[str, float] = {}
_active_users_lock = threading.Lock()

# per-minute rolling time series for charts: list of bucket dicts, oldest first
_timeseries: deque = deque(maxlen=TIMESERIES_MAX_BUCKETS)
_timeseries_lock = threading.Lock()


def _empty_bucket(minute_ts: int) -> dict:
    return {
        "minute": minute_ts,
        "requests": 0,
        "errors": 0,
        "total_latency_ms": 0.0,
        "latency_samples": 0,
        "txs_sent": 0,
        "txs_rejected": 0,
    }


def _bucket_for(ts: float) -> dict:
    """Get (creating if needed) the 1-minute bucket that `ts` falls into."""
    minute_ts = int(ts // 60) * 60
    with _timeseries_lock:
        if not _timeseries or _timeseries[-1]["minute"] != minute_ts:
            _timeseries.append(_empty_bucket(minute_ts))
        return _timeseries[-1]


def track_active_user(identifier: str | None) -> None:
    """Record a heartbeat for `identifier` (e.g. client IP or wallet
    address). Call this on every inbound request, not just business events,
    so the active-user count reflects real traffic."""
    if not identifier:
        return
    with _active_users_lock:
        _active_users[identifier] = time.time()


def get_active_user_count(window_seconds: int = ACTIVE_USER_WINDOW_SECONDS) -> int:
    cutoff = time.time() - window_seconds
    with _active_users_lock:
        stale = [k for k, v in _active_users.items() if v < cutoff]
        for k in stale:
            del _active_users[k]
        return len(_active_users)


def log_event(event_type: str, data: dict[str, Any]) -> None:
    """Append a structured event to the log ring buffer."""
    now = time.time()
    entry = {
        "id":         str(uuid.uuid4())[:8],
        "timestamp":  now,
        "ts_iso":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "event_type": event_type,
        **data,
    }
    _logs.append(entry)
    _update_stats(event_type, data)
    _update_breakdowns(event_type, data)
    _update_timeseries(event_type, data, now)


def _update_stats(event_type: str, data: dict) -> None:
    with _stats_lock:
        status = data.get("status", "")
        latency = data.get("latency_ms") or data.get("user_latency_ms")

        if latency is not None:
            try:
                _stats["total_latency_ms"] += float(latency)
                _stats["latency_samples"]  += 1
            except (ValueError, TypeError):
                pass

        if event_type == "parse_intent":
            _stats["total_requests"] += 1
            if status == "success":
                _stats["successful_parses"] += 1
            elif status == "error":
                _stats["failed_parses"] += 1

        elif event_type == "build_tx" and status == "success":
            _stats["txs_built"] += 1

        elif event_type == "tx_result":
            if status == "sent":
                _stats["txs_sent"] += 1
            elif status in ("rejected", "error"):
                _stats["txs_rejected"] += 1


def _update_breakdowns(event_type: str, data: dict) -> None:
    with _breakdown_lock:
        _by_event_type[event_type] = _by_event_type.get(event_type, 0) + 1

        action = data.get("action") or data.get("parsed_action")
        if action:
            action = str(action)
            _by_action[action] = _by_action.get(action, 0) + 1

        network = data.get("network") or data.get("parsed_network")
        if network:
            network = str(network)
            _by_network[network] = _by_network.get(network, 0) + 1


def _update_timeseries(event_type: str, data: dict, now: float) -> None:
    bucket = _bucket_for(now)
    status = data.get("status", "")
    latency = data.get("latency_ms") or data.get("user_latency_ms")

    with _timeseries_lock:
        if event_type in ("parse_intent", "build_tx", "execute_read"):
            bucket["requests"] += 1
            if status == "error":
                bucket["errors"] += 1
        if latency is not None:
            try:
                bucket["total_latency_ms"] += float(latency)
                bucket["latency_samples"]  += 1
            except (ValueError, TypeError):
                pass
        if event_type == "tx_result":
            if status == "sent":
                bucket["txs_sent"] += 1
            elif status in ("rejected", "error"):
                bucket["txs_rejected"] += 1


def get_logs(limit: int = 100) -> list[dict]:
    """Return the most recent N log entries (newest first)."""
    entries = list(_logs)
    entries.reverse()
    return entries[:limit]


def clear_logs() -> None:
    _logs.clear()


def get_stats() -> dict:
    with _stats_lock:
        samples = _stats["latency_samples"]
        avg_latency = (
            round(_stats["total_latency_ms"] / samples, 2) if samples > 0 else 0
        )
        uptime_s = round(time.time() - _stats["server_start"])
        return {
            **_stats,
            "avg_latency_ms": avg_latency,
            "uptime_seconds": uptime_s,
        }


def get_timeseries() -> list[dict]:
    """Return per-minute buckets (oldest first), with avg latency computed,
    ready to plot directly."""
    with _timeseries_lock:
        buckets = list(_timeseries)
    out = []
    for b in buckets:
        samples = b["latency_samples"]
        out.append({
            "minute":       b["minute"],
            "ts_iso":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(b["minute"])),
            "requests":     b["requests"],
            "errors":       b["errors"],
            "avg_latency_ms": round(b["total_latency_ms"] / samples, 2) if samples else 0,
            "txs_sent":     b["txs_sent"],
            "txs_rejected": b["txs_rejected"],
        })
    return out


def get_breakdowns() -> dict:
    with _breakdown_lock:
        return {
            "by_event_type": dict(_by_event_type),
            "by_action":     dict(_by_action),
            "by_network":    dict(_by_network),
        }


def get_metrics_snapshot() -> dict:
    """Everything the dashboard(s) need in one call."""
    return {
        "current":     get_stats(),
        "active_users": get_active_user_count(),
        "timeseries":  get_timeseries(),
        **get_breakdowns(),
    }