# Personalizaciones — Statistics Ingeniería S.A.S.

Guía para **mantener este fork** al actualizar desde upstream RustDesk.  
Última revisión: **2026-11-18**.

| Concepto | Valor |
| --- | --- |
| Fork | `JorgeIvanAyala/rustdesk` |
| Upstream | `rustdesk/rustdesk` |
| Cliente pin | **1.5.3** |
| Servidor ID/relay (Contabo) | **rustdesk-server OSS 1.1.16** (`hbbs` / `hbbr`) |
| Licencia Pro | **No** (todo OSS) |
| Organización | Statistics Ingeniería S.A.S. |

Documentos relacionados:

- Inventario VPS: [`SERVIDOR-CONTABO.md`](SERVIDOR-CONTABO.md)
- Stats OSS: [`../server-stats/README.md`](../server-stats/README.md)
- Configurador Windows: [`../scripts/Configure-RustDesk-Server.ps1`](../scripts/Configure-RustDesk-Server.ps1)

---

## 1. Qué es custom (checklist al actualizar)

Al hacer merge de una release nueva de upstream, **revisar y reaplicar** lo siguiente si el merge lo toca o lo diluye:

| # | Personalización | Dónde | Acción al actualizar |
| --- | --- | --- | --- |
| 1 | Inyección de servidor + clave pública en CI Windows | `.github/workflows/Only_Windows.yml` | Conservar el step *Inject Custom Server Settings*; ajustar `VERSION`, `VCPKG_COMMIT_ID` y el `sed` si cambia `RENDEZVOUS_SERVERS` / tipo de `RS_PUB_KEY` |
| 2 | Secretos GitHub del fork | Settings → Secrets | `RENDEZVOUS_SERVER`, `RS_PUB_KEY` (no commitear valores) |
| 3 | Servidor Contabo OSS | VPS `/root/docker/rustdesk-server/` | Independiente del repo cliente; ver `SERVIDOR-CONTABO.md` |
| 4 | Configurador Windows (PS1 + EXE) | `scripts/Configure-RustDesk-Server.*` | Mantener host/key por defecto; regenerar `.exe` si cambia el `.ps1` |
| 5 | Estadísticas de uso (solo servidor) | `server-stats/` + Contabo `/root/docker/rustdesk-stats/` | Redeploy en VPS si cambia el código; **no** meter telemetría en el cliente |
| 6 | Título operativo | Scripts / docs | “Servidor Statistics Ingeniería S.A.S.” |

### Decisiones explícitas (no revertir sin acuerdo)

1. **Sin RustDesk Pro** — no depender de API/audit Pro ni de imagen Pro.
2. **Sin telemetría en el cliente** — no instalar agentes en PCs; no enviar `quién↔quién` desde el EXE. Las stats son solo en Contabo a partir de logs `hbbs`/`hbbr` + DB de peers.
3. **EXE del configurador sin firma de código** — SmartScreen puede avisar; uso interno / admin.
4. **Campo API vacío en el cliente** — en OSS es normal (aviso, no fallo).

---

## 2. Servidor Contabo (resumen)

| Campo | Valor |
| --- | --- |
| Host | `vmi2352198.contaboserver.net` / `209.145.51.68` |
| Stack | Docker Compose, `network_mode: host`, imagen `rustdesk/rustdesk-server:1.1.16` |
| Clave pública (clientes) | `tjRk7Jbzv+LyhCvKZVXkUy1qzwaa8XA5USlE1lSE1vo=` |
| Puertos RustDesk | 21115–21119 (y 21114 abierto pero Pro-only) |
| Stats | `rustdesk-stats` en `:8088` — panel vía túnel SSH |
| SSH | Solo llave Mantto (`id_ed25519_mantto`); fail2ban activo |

Detalle completo: `docs/SERVIDOR-CONTABO.md`.

---

## 3. Build Windows custom (CI)

Workflow: `.github/workflows/Only_Windows.yml`.

1. Pins de versión del fork (`VERSION`, `VCPKG_COMMIT_ID`) alineados a la release mergeada.
2. Step **Inject Custom Server Settings** sustituye en `libs/hbb_common/src/config.rs`:
   - `rs-ny.rustdesk.com` / `rs.rustdesk.com` → secreto `RENDEZVOUS_SERVER`
   - `RS_PUB_KEY` → secreto `RS_PUB_KEY`  
   En **1.5.x** la constante es `pub const RS_PUB_KEY: &str = "..."` (ya no `&'static str`).
3. Tras un bump de upstream, comprobar el valor por defecto de `RENDEZVOUS_SERVERS` en `config.rs` y ampliar el `sed` si aparece otro hostname.

---

## 4. Configurador de puesto Windows

| Archivo | Uso |
| --- | --- |
| `scripts/Configure-RustDesk-Server.ps1` | Fuente; requiere admin |
| `scripts/Configure-RustDesk-Server.exe` | Empaquetado (PS2EXE); mismo propósito |

Comportamiento relevante:

- Defaults: host Contabo + clave pública de arriba.
- Escribe `RustDesk2.toml` en perfil **LocalService** (ruta del servicio) y en perfiles de usuario.
- Prefiere MSI; fallback EXE; título de consola “Servidor Statistics Ingeniería S.A.S.”.

Al regenerar el `.exe` tras editar el `.ps1`, volver a distribuir el EXE (p. ej. Grafos) a quien despliega puestos.

---

## 5. Estadísticas OSS (`server-stats/`)

Servicio FastAPI + collector de logs Docker + lectura RO de `db_v2.sqlite3`.

| Sí | No |
| --- | --- |
| Peers registrados, peer visto, cambio IP, relay open/close | Telemetría embebida en el cliente |
| Dashboard en localhost / túnel SSH | Agente en cada PC |
| Código en repo + deploy en Contabo | Licencia Pro / consola 21114 |

Deploy VPS: `/root/docker/rustdesk-stats/` (`docker compose up -d --build`).

Panel:

```bash
ssh -L 8088:127.0.0.1:8088 -i <llave-mantto> root@vmi2352198.contaboserver.net
# http://127.0.0.1:8088/
```

---

## 6. Procedimiento sugerido al subir de versión upstream

1. Anotar tag/commit actual del fork y crear rama `backup/pre-update-X.Y.Z` si hace falta.
2. `git fetch upstream` y merge del tag deseado en `master` (o PR).
3. Resolver conflictos priorizando **conservar** personalizaciones de la tabla del §1.
4. Actualizar `Only_Windows.yml` (`VERSION`, vcpkg, `sed` de rendezvous/key).
5. Verificar secretos `RENDEZVOUS_SERVER` / `RS_PUB_KEY` en GitHub.
6. Correr CI Windows; validar que el cliente apunta al Contabo y no a `rs-ny.rustdesk.com`.
7. Si sube `rustdesk-server`, actualizar imagen en Contabo y `SERVIDOR-CONTABO.md` (cliente y servidor **no** comparten número de versión).
8. Si cambió el configurador, regenerar EXE y redistribuir.
9. Actualizar la fecha y pins de **este** archivo.

---

## 7. Artefactos que no van al repo

No versionar builds locales grandes ni basura de release:

- `windows-x64-release/`
- `windows-x64-release.zip`
- Datos runtime de stats en el VPS (`/root/docker/rustdesk-stats/data/`)

Sí versionar: docs, `scripts/*.ps1` (+ EXE pequeño del configurador), `server-stats/` (código), cambios de workflow.
