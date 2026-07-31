# IntentChain Monitoring

Two ways to watch the app's metrics (latency, active users, tx success/
failure, request rate, breakdowns by action/network). Both read from the
same backend counters in `app/core/logger.py` — nothing to keep in sync.

## 1. In-app dashboard (no setup)

Open `frontend/metrics.html` in a browser (or click **📈 Metrics** in the
main app header) while `uvicorn app.main:app` is running. It polls
`GET /metrics` every few seconds — good for a quick local check, no extra
services required.

## 2. Real Grafana + Prometheus

For a proper dashboard you can share, alert on, or keep running long-term:

```bash
# 1. Start the FastAPI app as usual (from the project root)
uvicorn app.main:app --reload

# 2. In another terminal, start the monitoring stack
cd monitoring
docker compose up -d
```

- **Prometheus** → http://localhost:9090 — scrapes
  `GET /metrics/prometheus` on the FastAPI app every 5s (config:
  `prometheus.yml`).
- **Grafana** → http://localhost:3002 — log in with `admin` / `admin`.
  The Prometheus datasource and the **IntentChain — System Metrics**
  dashboard are auto-provisioned; no manual setup needed. Open
  *Dashboards → IntentChain — System Metrics*.

  Grafana always listens on port `3000` *inside* its container — that's
  fixed by the image. `docker-compose.yml` maps host port `3002` to it so
  it doesn't collide with anything else you may have running on 3000 (a
  frontend dev server, another Grafana, etc). If you change the mapping,
  only edit the **left** side (`"XXXX:3000"`) and open Grafana at `XXXX` —
  changing the right side just points at nothing inside the container and
  you'll get `ERR_EMPTY_RESPONSE`.

### Troubleshooting

- **`bind: Only one usage of each socket address` on `docker compose up`**
  — something on your machine already owns that host port. Find it
  (`netstat -ano | findstr :3000` on Windows, `lsof -i :3000` on
  Mac/Linux) and either stop it or change the host-side port in
  `docker-compose.yml`.
- **`frontend/metrics.html` stuck on "CONNECTING"** — it needs
  `http://127.0.0.1:8000` reachable. Confirm `uvicorn app.main:app` is
  actually running (not crashed) and test with
  `curl http://127.0.0.1:8000/health`. Which port `metrics.html` itself is
  served from doesn't matter — it's a separate concern from whether the
  backend it's polling is up.

### If the app isn't running on the host machine

`prometheus.yml` scrapes `host.docker.internal:8000`, which reaches a
`uvicorn` process running directly on your machine. If you containerize
the FastAPI app instead, either:
- put it on the same Docker network as this compose file and change the
  target in `prometheus.yml` to the container's service name, or
- add it as a third service in `docker-compose.yml`.

### Metrics exposed

| Metric | Type | Meaning |
|---|---|---|
| `intentchain_requests_total` | counter | intent-parse requests received |
| `intentchain_successful_parses_total` / `intentchain_failed_parses_total` | counter | parse outcomes |
| `intentchain_txs_built_total` | counter | unsigned txs built |
| `intentchain_txs_sent_total` / `intentchain_txs_rejected_total` | counter | user-reported tx outcomes |
| `intentchain_avg_latency_ms` | gauge | rolling average latency across timed events |
| `intentchain_active_users` | gauge | distinct client IPs seen in the last 5 min |
| `intentchain_uptime_seconds` | gauge | seconds since process start |
| `intentchain_requests_by_action_total{action="..."}` | counter | per-action breakdown |
| `intentchain_requests_by_network_total{network="..."}` | counter | per-network breakdown |

### Stopping

```bash
cd monitoring
docker compose down          # keep data
docker compose down -v       # also wipe Prometheus/Grafana volumes
```