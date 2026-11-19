# RustDesk — servidor Contabo (VPS)

Inventario del servidor self-hosted usado por el cliente custom de este repositorio.

Revisado: **2026-11-18** (acceso SSH con llave Mantto).

Mapa de todas las personalizaciones del fork (CI, configurador, stats, checklist de upgrade): [`PERSONALIZACIONES.md`](PERSONALIZACIONES.md).

## Resumen

| Campo | Valor |
| --- | --- |
| Hostname | `vmi2352198` / `vmi2352198.contaboserver.net` |
| IP pública | `209.145.51.68` |
| SO | Ubuntu **24.04.5 LTS** (Noble) |
| Kernel | `6.8.0-149-generic` |
| CPU / RAM | 4 vCPU / 5.8 GiB |
| Producto servidor | [rustdesk/rustdesk-server](https://github.com/rustdesk/rustdesk-server) (OSS, no Pro) |
| Versión en producción | **1.1.16** (`hbbs` / `hbbr`) |
| Última release upstream | **1.1.16** (2026-07-20) → al día |
| Cliente (este fork) | **1.5.3** (tag upstream mergeada en local) |

> Cliente y servidor son productos distintos: `rustdesk` (app) vs `rustdesk-server` (`hbbs`/`hbbr`). No deben compartir el mismo número de versión.

## Acceso SSH

| Campo | Valor |
| --- | --- |
| Entrada | `root@vmi2352198.contaboserver.net` (equiv. `root@209.145.51.68`) |
| Puerto | `22` |
| Llave | `01 Desarrollo/19 Mantto Linux/secrets/ssh/id_ed25519_mantto` |
| Comentario de la clave | `mantto-linux-agent` |
| Pública | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIEbss5oTLTReRfYO+knOrwm9eMK/xyodmNTNbl2+Aiq mantto-linux-agent` |

Protección: **fail2ban** (`sshd`) + **ufw**. Varios intentos fallidos banean la IP de origen (REJECT en ufw). Desbaneo típico desde consola Contabo:

```bash
fail2ban-client status sshd
fail2ban-client set sshd unbanip <IP>
```

## Instalación Docker

Ruta: `/root/docker/rustdesk-server/`

| Archivo / dir | Uso |
| --- | --- |
| `compose.yml` | Stack actual (`1.1.16`, `network_mode: host`) |
| `data/` | Volumen montado en `/root` del contenedor (DB + claves) |
| `data/id_ed25519` / `.pub` | Par de claves del ID/rendezvous server |
| `data/db_v2.sqlite3` | Base de peers |
| `backup-rustdesk-*.tar.gz` / `data-backup-*.tgz` | Respaldos locales |
| `/opt/mantto-backup/hosts/srv-rustdesk.sh` | Script de backup Mantto |

### `compose.yml` (efectivo)

```yaml
services:
  hbbs:
    container_name: hbbs
    image: rustdesk/rustdesk-server:1.1.16
    command: hbbs
    volumes:
      - ./data:/root
    network_mode: host
    depends_on:
      - hbbr
    restart: always

  hbbr:
    container_name: hbbr
    image: rustdesk/rustdesk-server:1.1.16
    command: hbbr
    volumes:
      - ./data:/root
    network_mode: host
    restart: always
```

### Contenedores

| Nombre | Imagen | Rol | Estado (2026-11-18) |
| --- | --- | --- | --- |
| `hbbs` | `rustdesk/rustdesk-server:1.1.16` | ID / rendezvous | Up |
| `hbbr` | `rustdesk/rustdesk-server:1.1.16` | Relay | Up |
| `e0828a8eb906_hbbr` | `rustdesk/rustdesk-server:latest` | Relay viejo (antes del pin a 1.1.16) | Up — **huérfano; candidato a retirar** |

Versión confirmada dentro del contenedor:

```text
hbbs 1.1.16
hbbr 1.1.16
```

## Clave pública del servidor (para el cliente)

```text
tjRk7Jbzv+LyhCvKZVXkUy1qzwaa8XA5USlE1lSE1vo=
```

En el build custom Windows (`Only_Windows.yml`) se inyecta vía secretos GitHub:

- `RENDEZVOUS_SERVER` → host del VPS (p. ej. `209.145.51.68` o el hostname Contabo)
- `RS_PUB_KEY` → la clave pública de arriba

## Puertos

| Puerto | Proto | Uso |
| --- | --- | --- |
| 21115 | TCP | NAT type test |
| 21116 | TCP/UDP | Hole punching / heartbeat / ID |
| 21117 | TCP | Relay (`hbbr`) |
| 21118 / 21119 | TCP | WebSocket (web client) |
| 21114 | TCP | Web console (**solo Pro**; abierto en ufw pero no aplica a OSS) |
| 8088 | TCP | Stats OSS (`rustdesk-stats`): telemetría pública + panel solo localhost |
| 22 | TCP | SSH |

Escucha verificada en host: `21115`, `21116` (tcp+udp), `21117`, `21118`, `21119`.

## Operación rápida

```bash
cd /root/docker/rustdesk-server
docker compose ps
docker compose logs -f --tail=100 hbbs hbbr
docker exec hbbs hbbs --version
docker exec hbbr hbbr --version
```

Actualizar imagen (cuando salga release > 1.1.16):

```bash
cd /root/docker/rustdesk-server
# editar compose.yml -> nueva tag
docker compose pull
docker compose up -d
```

## Estadísticas de uso (OSS — solo servidor)

Servicio propio (sin licencia Pro) en `/root/docker/rustdesk-stats/`. **No hay telemetría en el cliente** ni agente en PCs de usuario.

| Campo | Valor |
| --- | --- |
| Contenedor | `rustdesk-stats` |
| Puerto | `8088` (`/health` público; dashboard/API lectura = red privada / túnel SSH) |
| Fuentes | logs Docker `hbbs`/`hbbr` + `db_v2.sqlite3` (RO) |
| Código local | `server-stats/` |

### Métricas

| Endpoint / panel | Contenido | Acceso |
| --- | --- | --- |
| `GET /health` | Liveness | público |
| `GET /api/summary` | Peers, eventos, sesiones relay | localhost / túnel SSH |
| `GET /api/peers` | IDs registrados | localhost |
| `GET /api/events` | `peer_seen`, `peer_ip_change`, `relay_open`, `relay_close` | localhost |
| `GET /api/sessions` | Sesiones relay inferidas (sin quién↔quién fiable) | localhost |
| `GET /` | Dashboard HTML | localhost |

Límite OSS: el servidor no registra con fiabilidad quién controla a quién ni el tipo de herramienta. Eso exigiría código en el cliente; **decidido no llevarlo**.

### Acceso al panel (túnel SSH)

```bash
ssh -L 8088:127.0.0.1:8088 -i <llave-mantto> root@vmi2352198.contaboserver.net
# abrir http://127.0.0.1:8088/
```

```bash
cd /root/docker/rustdesk-stats
docker compose ps
docker compose logs -f --tail=100
curl -s http://127.0.0.1:8088/api/summary
```

## Notas / pendientes

1. Contenedor huérfano `e0828a8eb906_hbbr` (`:latest`) sigue corriendo junto al `hbbr` 1.1.16. Conviene detenerlo y eliminarlo tras confirmar que el relay activo es solo el de `compose.yml`.
2. El cliente del fork está en **1.5.3**; el servidor OSS en **1.1.16** (última disponible). No hay desfase de servidor respecto a upstream OSS.
3. SSH: usar **solo** la llave Mantto. No probar otras claves/usuarios (fail2ban banea rápido).
4. Preferir hostname `vmi2352198.contaboserver.net` en SSH si la IP no está en `known_hosts` (evita rechazo por host key).
5. Stats solo servidor (`rustdesk-stats` en `:8088`). Sin telemetría en el cliente.
