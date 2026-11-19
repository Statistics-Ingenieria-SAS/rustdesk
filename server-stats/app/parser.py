import re
from dataclasses import dataclass
from typing import Optional

# [2026-11-19 01:18:19.863963 +00:00] INFO [src/peer.rs:102] update_pk 145554750 [::ffff:104.28.212.160]:13903 ...
TS_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ [+-]\d{2}:\d{2})\]\s+\w+\s+\[[^\]]+\]\s+(?P<msg>.*)$"
)

UPDATE_PK_RE = re.compile(
    r"update_pk\s+(?P<id>\d+)\s+\[::ffff:(?P<ip>[0-9a-fA-F\.:]+)\]:(?P<port>\d+)"
)
IP_CHANGE_RE = re.compile(
    r"IP change of\s+(?P<id>\d+)\s+from\s+\[::ffff:(?P<from_ip>[0-9a-fA-F\.:]+)\]:\d+\s+to\s+\[::ffff:(?P<to_ip>[0-9a-fA-F\.:]+)\]:\d+"
)
RELAY_NEW_RE = re.compile(
    r"New relay request\s+(?P<uuid>[0-9a-fA-F-]+)\s+from\s+\[::ffff:(?P<ip>[0-9a-fA-F\.:]+)\]:(?P<port>\d+)"
)
RELAY_CLOSED_RE = re.compile(
    r"Relay of\s+\[::ffff:(?P<ip>[0-9a-fA-F\.:]+)\]:(?P<port>\d+)\s+closed"
)


@dataclass
class ParsedEvent:
    ts: str
    source: str
    event_type: str
    peer_id: Optional[str] = None
    peer_id_b: Optional[str] = None
    ip: Optional[str] = None
    relay_uuid: Optional[str] = None
    conn_type: Optional[str] = None
    detail: Optional[str] = None
    raw: Optional[str] = None


def parse_line(source: str, line: str) -> Optional[ParsedEvent]:
    line = line.strip()
    if not line:
        return None
    m = TS_RE.match(line)
    if not m:
        return None
    ts = m.group("ts")
    msg = m.group("msg")

    if source == "hbbs":
        m2 = UPDATE_PK_RE.search(msg)
        if m2:
            return ParsedEvent(
                ts=ts,
                source=source,
                event_type="peer_seen",
                peer_id=m2.group("id"),
                ip=m2.group("ip"),
                detail=f"port={m2.group('port')}",
                raw=line,
            )
        m2 = IP_CHANGE_RE.search(msg)
        if m2:
            return ParsedEvent(
                ts=ts,
                source=source,
                event_type="peer_ip_change",
                peer_id=m2.group("id"),
                ip=m2.group("to_ip"),
                detail=f"from={m2.group('from_ip')}",
                raw=line,
            )

    if source == "hbbr":
        m2 = RELAY_NEW_RE.search(msg)
        if m2:
            return ParsedEvent(
                ts=ts,
                source=source,
                event_type="relay_open",
                ip=m2.group("ip"),
                relay_uuid=m2.group("uuid"),
                detail=f"port={m2.group('port')}",
                conn_type="relay",
                raw=line,
            )
        m2 = RELAY_CLOSED_RE.search(msg)
        if m2:
            return ParsedEvent(
                ts=ts,
                source=source,
                event_type="relay_close",
                ip=m2.group("ip"),
                detail=f"port={m2.group('port')}",
                conn_type="relay",
                raw=line,
            )

    return None
