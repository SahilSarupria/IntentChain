"""
IntentChain Activity Logger
In-memory log store with ring buffer + cumulative stats.
"""
import time
import uuid
from collections import deque
from typing import Any
import threading

# Max entries kept in memory
MAX_LOG_ENTRIES = 500

_lock  = deque.__new__(deque)
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


def log_event(event_type: str, data: dict[str, Any]) -> None:
    """Append a structured event to the log ring buffer."""
    entry = {
        "id":         str(uuid.uuid4())[:8],
        "timestamp":  time.time(),
        "ts_iso":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type": event_type,
        **data,
    }
    _logs.append(entry)
    _update_stats(event_type, data)


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
