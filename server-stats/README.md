# RustDesk Stats OSS — Statistics Ingeniería S.A.S.

Servicio open source de estadísticas de uso para el servidor Contabo (`hbbs`/`hbbr`), **sin licencia RustDesk Pro**.

**Solo servidor.** No se instala nada en PCs de usuario ni se incluye telemetría en el cliente custom.

## Qué mide

| Dato | Fuente |
| --- | --- |
| Peers registrados (ID, alta) | `db_v2.sqlite3` (solo lectura) |
| Peer visto / cambio de IP | logs `hbbs` |
| Relay abierto / cerrado | logs `hbbr` |
| Duración aproximada de relay | empareja open/close por IP |

No incluye quién↔quién ni tipo de herramienta (escritorio/archivos/terminal): eso requeriría el cliente.

## Despliegue (Contabo)

```bash
cd /root/docker/rustdesk-stats
docker compose up -d --build
curl -s http://127.0.0.1:8088/health
curl -s http://127.0.0.1:8088/api/summary
```

Panel vía túnel SSH:

```bash
ssh -L 8088:127.0.0.1:8088 -i <llave-mantto> root@vmi2352198.contaboserver.net
# http://127.0.0.1:8088/
```

## API

- `GET /health` (público)
- `GET /api/summary`, `/api/peers`, `/api/events`, `/api/sessions`, `/` (red privada / túnel)
- `POST /api/v1/telemetry` existe pero **no se usa** (reserva; sin cliente emisor)
