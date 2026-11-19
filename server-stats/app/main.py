import ipaddress
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .collector import start_collector_background
from .db import db_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

STATS_DB = os.getenv("STATS_DB", "/data/stats.sqlite3")
PEERS_DB = os.getenv("PEERS_DB", "/peers/db_v2.sqlite3")
HBBS = os.getenv("HBBS_CONTAINER", "hbbs")
HBBR = os.getenv("HBBR_CONTAINER", "hbbr")
TELEMETRY_TOKEN = os.getenv("TELEMETRY_TOKEN", "statistics-rustdesk-stats")

app = FastAPI(title="RustDesk Stats OSS — Statistics Ingeniería S.A.S.", version="0.2.0")


def _is_local_client(host: str) -> bool:
    # Docker publish muestra el bridge (privado), no 127.0.0.1, aunque el acceso sea local/túnel.
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_loopback or ip.is_private)
    except ValueError:
        return False


@app.middleware("http")
async def restrict_non_telemetry(request: Request, call_next):
    """Lectura solo desde red local/privada; telemetría y /health públicos (token en POST)."""
    client = request.client.host if request.client else ""
    path = request.url.path
    if path.startswith("/api/v1/telemetry") or path == "/health":
        return await call_next(request)
    if not _is_local_client(client):
        return JSONResponse({"detail": "solo localhost (usar túnel SSH)"}, status_code=403)
    return await call_next(request)


class TelemetryEvent(BaseModel):
    event_type: str = Field(..., description="session_start | session_end | heartbeat")
    ts: Optional[str] = None
    controlling_id: Optional[str] = None
    controlled_id: Optional[str] = None
    conn_type: Optional[str] = Field(
        None, description="remote_desktop | file_transfer | terminal | camera | port_forward | other"
    )
    path: Optional[str] = Field(None, description="direct | relay | lan | unknown")
    duration_sec: Optional[int] = None
    note: Optional[str] = None
    detail: Optional[str] = None


@app.on_event("startup")
def on_startup() -> None:
    init_db(STATS_DB)
    start_collector_background(STATS_DB, HBBS, HBBR)
    log.info("stats API up db=%s peers=%s", STATS_DB, PEERS_DB)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "rustdesk-stats", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/summary")
def summary() -> dict:
    with db_session(STATS_DB) as conn:
        peers_count = None
        try:
            pconn = sqlite3.connect(f"file:{PEERS_DB}?mode=ro", uri=True)
            peers_count = pconn.execute("SELECT COUNT(*) FROM peer").fetchone()[0]
            pconn.close()
        except Exception as e:
            peers_count = f"error: {e}"

        return {
            "peers_registered": peers_count,
            "events": conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"],
            "sessions": conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"],
            "sessions_open": conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE ended_at IS NULL"
            ).fetchone()["n"],
            "events_by_type": _rows(
                conn,
                "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type ORDER BY n DESC",
            ),
            "client_sessions": conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE source='client'"
            ).fetchone()["n"],
            "note": (
                "OSS solo servidor: peers/eventos/relay desde logs hbbs/hbbr. "
                "Sin telemetría en el cliente."
            ),
        }


@app.get("/api/peers")
def peers(limit: int = Query(100, ge=1, le=1000)) -> dict:
    try:
        pconn = sqlite3.connect(f"file:{PEERS_DB}?mode=ro", uri=True)
        pconn.row_factory = sqlite3.Row
        rows = pconn.execute(
            "SELECT id, created_at, status, note, info FROM peer ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        pconn.close()
        return {"items": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"No se pudo leer peers DB: {e}") from e


@app.get("/api/events")
def events(
    limit: int = Query(200, ge=1, le=2000),
    event_type: Optional[str] = None,
) -> dict:
    sql = "SELECT * FROM events"
    params: list[Any] = []
    if event_type:
        sql += " WHERE event_type=?"
        params.append(event_type)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with db_session(STATS_DB) as conn:
        return {"items": _rows(conn, sql, tuple(params))}


@app.get("/api/sessions")
def sessions(limit: int = Query(200, ge=1, le=2000), open_only: bool = False) -> dict:
    sql = "SELECT * FROM sessions"
    if open_only:
        sql += " WHERE ended_at IS NULL"
    sql += " ORDER BY started_at DESC LIMIT ?"
    with db_session(STATS_DB) as conn:
        return {"items": _rows(conn, sql, (limit,))}


@app.post("/api/v1/telemetry")
def telemetry(
    body: TelemetryEvent,
    x_telemetry_token: Optional[str] = Header(default=None),
) -> dict:
    if x_telemetry_token != TELEMETRY_TOKEN:
        raise HTTPException(401, "token inválido")

    ts = body.ts or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f +00:00")
    with db_session(STATS_DB) as conn:
        conn.execute(
            """
            INSERT INTO events
              (ts, source, event_type, peer_id, peer_id_b, ip, relay_uuid, conn_type, detail, raw)
            VALUES (?, 'client', ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                ts,
                body.event_type,
                body.controlling_id,
                body.controlled_id,
                body.conn_type,
                body.detail or body.note,
                body.model_dump_json(),
            ),
        )
        if body.event_type == "session_start":
            conn.execute(
                """
                INSERT INTO sessions
                  (started_at, controlling_id, controlled_id, conn_type, path, source, note)
                VALUES (?, ?, ?, ?, ?, 'client', ?)
                """,
                (
                    ts,
                    body.controlling_id,
                    body.controlled_id,
                    body.conn_type,
                    body.path or "unknown",
                    body.note,
                ),
            )
        elif body.event_type == "session_end":
            row = conn.execute(
                """
                SELECT id, started_at FROM sessions
                WHERE source='client' AND ended_at IS NULL
                  AND IFNULL(controlling_id,'')=IFNULL(?, '')
                  AND IFNULL(controlled_id,'')=IFNULL(?, '')
                  AND IFNULL(conn_type,'')=IFNULL(?, IFNULL(conn_type,''))
                ORDER BY id DESC LIMIT 1
                """,
                (body.controlling_id, body.controlled_id, body.conn_type),
            ).fetchone()
            if row:
                dur = body.duration_sec
                if dur is None:
                    try:
                        start = datetime.strptime(row["started_at"][:26], "%Y-%m-%d %H:%M:%S.%f")
                        end = datetime.strptime(ts[:26], "%Y-%m-%d %H:%M:%S.%f")
                        dur = max(0, int((end - start).total_seconds()))
                    except ValueError:
                        dur = None
                conn.execute(
                    "UPDATE sessions SET ended_at=?, duration_sec=?, conn_type=COALESCE(?, conn_type), note=COALESCE(?, note) WHERE id=?",
                    (ts, dur, body.conn_type, body.note, row["id"]),
                )
    return {"ok": True}


DASHBOARD_HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>RustDesk Stats — Statistics Ingeniería S.A.S.</title>
  <style>
    :root { --bg:#0f1419; --card:#1a2332; --text:#e7ecf3; --muted:#9aa7b8; --accent:#3d8bfd; }
    body { margin:0; font-family:Segoe UI,system-ui,sans-serif; background:var(--bg); color:var(--text); }
    header { padding:1.25rem 1.5rem; border-bottom:1px solid #243044; }
    h1 { margin:0 0 .25rem; font-size:1.25rem; }
    p { margin:0; color:var(--muted); font-size:.9rem; }
    main { padding:1.25rem 1.5rem; display:grid; gap:1rem; }
    .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:.75rem; }
    .card { background:var(--card); border-radius:10px; padding:1rem; }
    .card b { display:block; font-size:1.4rem; margin-top:.35rem; }
    table { width:100%; border-collapse:collapse; background:var(--card); border-radius:10px; overflow:hidden; }
    th, td { text-align:left; padding:.55rem .7rem; border-bottom:1px solid #243044; font-size:.85rem; }
    th { color:var(--muted); font-weight:600; }
    a { color:var(--accent); }
    .note { background:#243044; padding:.75rem 1rem; border-radius:8px; color:var(--muted); font-size:.85rem; }
  </style>
</head>
<body>
  <header>
    <h1>Servidor Statistics Ingeniería S.A.S. — Estadísticas RustDesk (OSS)</h1>
    <p>Recolector local sin licencia Pro · datos de hbbs/hbbr + telemetría cliente</p>
  </header>
  <main>
    <div class="note" id="note"></div>
    <div class="cards" id="cards"></div>
    <section>
      <h2>Sesiones recientes</h2>
      <table><thead><tr>
        <th>Inicio</th><th>Fin</th><th>Duración</th><th>Controla</th><th>Controlado</th><th>Tipo</th><th>Origen</th>
      </tr></thead><tbody id="sessions"></tbody></table>
    </section>
    <section>
      <h2>Eventos recientes</h2>
      <table><thead><tr>
        <th>Ts</th><th>Fuente</th><th>Tipo</th><th>Peer</th><th>IP</th><th>Detalle</th>
      </tr></thead><tbody id="events"></tbody></table>
    </section>
  </main>
  <script>
    const fmtDur = s => s==null ? '—' : (s<60? s+'s' : Math.floor(s/60)+'m '+ (s%60)+'s');
    async function load() {
      const sum = await fetch('/api/summary').then(r=>r.json());
      document.getElementById('note').textContent = sum.note || '';
      document.getElementById('cards').innerHTML = `
        <div class="card">Peers registrados<b>${sum.peers_registered}</b></div>
        <div class="card">Eventos<b>${sum.events}</b></div>
        <div class="card">Sesiones<b>${sum.sessions}</b></div>
        <div class="card">Sesiones abiertas<b>${sum.sessions_open}</b></div>`;
      const sess = await fetch('/api/sessions?limit=50').then(r=>r.json());
      document.getElementById('sessions').innerHTML = (sess.items||[]).map(s=>`<tr>
        <td>${s.started_at||''}</td><td>${s.ended_at||'—'}</td><td>${fmtDur(s.duration_sec)}</td>
        <td>${s.controlling_id||'—'}</td><td>${s.controlled_id||'—'}</td>
        <td>${s.conn_type||'—'}</td><td>${s.source||''}</td></tr>`).join('') || '<tr><td colspan="7">Sin datos aún</td></tr>';
      const ev = await fetch('/api/events?limit=50').then(r=>r.json());
      document.getElementById('events').innerHTML = (ev.items||[]).map(e=>`<tr>
        <td>${e.ts||''}</td><td>${e.source||''}</td><td>${e.event_type||''}</td>
        <td>${e.peer_id||'—'}</td><td>${e.ip||'—'}</td><td>${e.detail||e.relay_uuid||''}</td></tr>`).join('') || '<tr><td colspan="6">Sin datos aún</td></tr>';
    }
    load(); setInterval(load, 15000);
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML
