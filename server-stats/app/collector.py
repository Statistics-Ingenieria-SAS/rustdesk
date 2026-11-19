import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import docker

from .db import db_session, get_meta, set_meta
from .parser import parse_line

log = logging.getLogger("collector")


def _insert_event(conn: sqlite3.Connection, ev) -> bool:
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO events
              (ts, source, event_type, peer_id, peer_id_b, ip, relay_uuid, conn_type, detail, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.ts,
                ev.source,
                ev.event_type,
                ev.peer_id,
                ev.peer_id_b,
                ev.ip,
                ev.relay_uuid,
                ev.conn_type,
                ev.detail,
                ev.raw,
            ),
        )
        return cur.rowcount > 0
    except sqlite3.Error as e:
        log.warning("insert event failed: %s", e)
        return False


def _close_open_relay_sessions(conn: sqlite3.Connection, ip: str, ended_at: str) -> None:
    rows = conn.execute(
        """
        SELECT id, started_at FROM sessions
        WHERE ended_at IS NULL AND source='hbbr' AND ip_a=?
        ORDER BY id DESC LIMIT 5
        """,
        (ip,),
    ).fetchall()
    for row in rows:
        try:
            start = datetime.strptime(row["started_at"][:26], "%Y-%m-%d %H:%M:%S.%f")
            end = datetime.strptime(ended_at[:26], "%Y-%m-%d %H:%M:%S.%f")
            dur = max(0, int((end - start).total_seconds()))
        except ValueError:
            dur = None
        conn.execute(
            "UPDATE sessions SET ended_at=?, duration_sec=? WHERE id=?",
            (ended_at, dur, row["id"]),
        )


def _open_relay_session(conn: sqlite3.Connection, ev) -> None:
    if ev.relay_uuid:
        exists = conn.execute(
            "SELECT id FROM sessions WHERE relay_uuid=? LIMIT 1",
            (ev.relay_uuid,),
        ).fetchone()
        if exists:
            return
    elif ev.ip:
        exists = conn.execute(
            """
            SELECT id FROM sessions
            WHERE ended_at IS NULL AND source='hbbr' AND ip_a=? AND started_at=?
            LIMIT 1
            """,
            (ev.ip, ev.ts),
        ).fetchone()
        if exists:
            return
    conn.execute(
        """
        INSERT INTO sessions
          (started_at, controlling_id, controlled_id, conn_type, path, relay_uuid, ip_a, source)
        VALUES (?, NULL, NULL, ?, 'relay', ?, ?, 'hbbr')
        """,
        (ev.ts, ev.conn_type or "relay", ev.relay_uuid, ev.ip),
    )


def process_event(conn: sqlite3.Connection, ev) -> None:
    if not _insert_event(conn, ev):
        return
    if ev.event_type == "relay_open":
        _open_relay_session(conn, ev)
    elif ev.event_type == "relay_close" and ev.ip:
        _close_open_relay_sessions(conn, ev.ip, ev.ts)


def fetch_logs_since(client: docker.DockerClient, container_name: str, since_ts: Optional[str]) -> list[str]:
    try:
        c = client.containers.get(container_name)
    except docker.errors.NotFound:
        log.warning("container missing: %s", container_name)
        return []

    kwargs = {"stdout": True, "stderr": True, "timestamps": False}
    # docker since wants unix epoch seconds
    if since_ts:
        try:
            # "2026-11-19 01:18:14.620736 +00:00"
            dt = datetime.strptime(since_ts[:26], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
            kwargs["since"] = int(dt.timestamp()) - 2
        except ValueError:
            kwargs["since"] = int(time.time()) - 7 * 86400
    else:
        # Primera corrida: solo últimos 7 días (evita volcar todo el historial)
        kwargs["since"] = int(time.time()) - 7 * 86400

    raw = c.logs(**kwargs)
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    return [ln for ln in text.splitlines() if ln.strip()]


def collector_loop(stats_db: str, hbbs: str, hbbr: str, interval_sec: int = 20) -> None:
    client = docker.from_env()
    log.info("collector started hbbs=%s hbbr=%s", hbbs, hbbr)
    while True:
        try:
            with db_session(stats_db) as conn:
                for source, name in (("hbbs", hbbs), ("hbbr", hbbr)):
                    key = f"last_ts_{source}"
                    since = get_meta(conn, key, "")
                    lines = fetch_logs_since(client, name, since or None)
                    newest = since
                    for line in lines:
                        ev = parse_line(source, line)
                        if not ev:
                            continue
                        process_event(conn, ev)
                        if not newest or ev.ts > newest:
                            newest = ev.ts
                    if newest and newest != since:
                        set_meta(conn, key, newest)
        except Exception:
            log.exception("collector iteration failed")
        time.sleep(interval_sec)


def start_collector_background(stats_db: str, hbbs: str, hbbr: str) -> threading.Thread:
    t = threading.Thread(
        target=collector_loop,
        kwargs={"stats_db": stats_db, "hbbs": hbbs, "hbbr": hbbr},
        name="rustdesk-stats-collector",
        daemon=True,
    )
    t.start()
    return t
