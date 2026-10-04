# Plan: Juego de D&D con agente DM (Telegram + Web)

Registro del plan acordado el 2026-10-03.

Objetivo: un juego de rol de D&D jugable **por Telegram y por web a la vez**,
dirigido por **agentes de IA como Dungeon Master**, donde los jugadores pueden
usar cualquiera de las dos superficies indistintamente y **el agente DM
conserva la memoria de la sesión**.

---

## 0. Decisiones acordadas

| Eje | Decisión |
| --- | --- |
| Topología | **Monolito modular**: un solo proceso asyncio con Telethon + web + runtime + agente + outbox |
| Web v1 | **Chat con el DM + feed en vivo** (WebSocket) + panel lateral de party/HP |
| Identidad web | **Código de enlace de Telegram** (6 dígitos) sobre la tabla `users` existente |
| Modelo del DM | **Modelo barato con streaming** para la narrativa + modelo pequeño para resúmenes de memoria |
| Servidor web | **`aiohttp`** (ya está en `requirements.txt`, mismo event loop que Telethon, WS sin dependencias nuevas) |
| Tools del agente | **In-process** (las funciones de `dndbot/mcp/*` ya son llamables como funciones) |
| Fuente de verdad | **Una sola**: `dndbot/data/dndbot.db` en WAL, compartida por todas las superficies |
| Formato canónico del feed | **Markdown** en `game_events`; conversión a HTML solo al salir por Telegram |
| `mcp_server.py` | **Se mantiene** como puerta para clientes MCP externos (Claude Desktop, OpenCode) |

---

## 1. Cimientos que ya existen

| Activo | Por qué sirve aquí |
| --- | --- |
| `dndbot/services.py` | Cerebro de reglas compartido por handlers y MCP; la web debe pasar por aquí, nunca por `storage.py` a pelo |
| `dndbot/storage.py` | 35 métodos de encuentros ya escritos (`create_encounter`, `start_encounter_run`, `damage_unit`, `kill_unit`, `heal_unit`, `set_unit_visibility`…) |
| SQLite **WAL** | Ya comparten la BD el bot y `mcp_server.py` en paralelo; el modelo escala a N procesos sin más |
| `.cache/` del SRD | Un solo calentamiento para todas las superficies |
| `dndbot/mcp/*.py` | Funciones planas con docstring como contrato: `build_server()` y el agente consumen las mismas |
| `dndbot/mcp/render.py` | `to_markdown` / `strip` / `esc_md`: puente HTML(Telegram) ↔ Markdown(modelo/web) |
| `dndbot/help.py`, `sheet.py` | Textos y ficha compartidos |
| `tests/test_flow.py` | Valida que **todo** mensaje de Telegram es HTML válido y ≤ 4096 — se extiende al canal web |

### Regla de diseño heredada

`dndbot/mcp/*` **no puede importar Telethon** (tira de `dndbot/handlers/*`).
Consecuencia: el agente **no puede emitir a Telegram por sí mismo** → la
salida hacia los jugadores vive en el runtime, no en los tools.

---

## 2. Arquitectura

```
   Telegram (Telethon)                 Web (HTTP + WebSocket)
          │                                    │
     [Ingress TG]                        [Ingress Web]
          │           normalización           │
          └─────────►  game_events  ◄─────────┘
                      (append-only, la verdad)
                              │
                   ┌──────────▼───────────┐
                   │  Runtime de partida  │  ← UN solo writer por sesión
                   │  cola + lease/turno  │    (evita 2 agentes pisen estado)
                   └──────────┬───────────┘
                              │ context builder
                ┌─────────────▼──────────────┐
                │  AGENTE DM                 │
                │  system prompt             │
                │  memoria (bible + resumen  │
                │           + recuperación)  │
                │  loop LLM → tools          │
                └──────┬─────────────┬───────┘
                       │             │ narración (Markdown)
             ┌─────────▼────────┐    │
             │ Tool surface     │    │
             │ (las 34 + nuevas)│    │
             └─────────┬────────┘    │
                       ▼             ▼
                  SQLite (estado)   game_events
                                        │
                              ┌─────────▼─────────┐
                              │      OUTBOX       │
                              └───┬───────────┬───┘
                                  │           │
                       Telegram ◄─┘           └─► WebSocket (web)
                    0.4 s/mensaje              push instantáneo
```

**Idea central:** el feed es **una sola tabla** (`game_events`). Telegram y la
web son dos adaptadores de render y entrega del mismo evento: un jugador que
entra por web ve lo que pasó en Telegram y viceversa, sin lógica duplicada.

### Componentes

| Componente | Estado | Responsabilidad |
| --- | --- | --- |
| Dominio (`storage`/`services`/`sheet`/`help`) | ✅ | reglas, una sola verdad |
| Ingress Telegram (`handlers/`) | ✅ | comandos → `game_events` |
| Ingress Web (`dndbot/web/`) | 🆕 | auth, chat, paneles, feed en vivo |
| Runtime de partida (`dndbot/runtime/`) | 🆕 | cola de eventos, lease de turno, dispara al agente |
| Agente DM (`runtime/agent.py`) | 🆕 | prompt, memoria, loop LLM → tools |
| Outbox (`runtime/outbox.py`) | 🆕 | fan-out con ritmo (TG) y push (web) |
| `mcp_server.py` | ✅ | clientes MCP externos |

---

## 3. Estructura de módulos

```
bot.py                          # entrypoint único (existe, se amplía)
mcp_server.py                   # se mantiene
dndbot/
  ├─ config.py                  # + WEB_PORT, LLM_*, AGENT_*
  ├─ storage.py  services.py    # dominio — sin cambio de filosofía
  ├─ sheet.py    help.py  render.py
  ├─ handlers/                  # ingress Telegram (existe)
  ├─ mcp/                       # tools (existe) + encounter.py (M6)
  ├─ web/                       🆕
  │    app.py                   # aiohttp.Application (WS + REST)
  │    auth.py                  # código de enlace TG → users
  │    chat.py                  # ingest del chat web → game_events
  │    feed.py                  # WS: suscripción por sesión
  │    panels.py                # party/HP/turno para el lateral
  └─ runtime/                   🆕
       events.py                # escribir/leer game_events, seq, emit()
       outbox.py                # entrega TG (0.4 s) + WS (push)
       context.py               # context builder
       agent.py                 # loop del DM
       memory.py                # bible + resumen de escena + FTS5
       llm.py                   # LlmPort + proveedor barato (streaming)
```

### Arranque (`dndbot/__main__.py:main()`), un paso más al final

```
setup_logging → config.check → build_client → register_handlers
→ login → publish_commands → db.connect → srd.start/warm
→ 🆕 runtime.start()    (outbox + agente)
→ 🆕 web.start(port)    (solo si WEB_PORT está definido)
```

Mismo patrón que hoy: si `WEB_PORT` no existe en `.env`, el bot arranca exactamente
igual que ahora.

---

## 4. Modelo de datos nuevo

```sql
CREATE TABLE game_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  surface TEXT NOT NULL,             -- 'tg' | 'web' | 'agent' | 'system'
  actor_id INTEGER,                  -- users.id
  kind TEXT NOT NULL,                -- player_message | dm_narration
                                     -- | tool_call | roll | ooc
  body_md TEXT NOT NULL,             -- CANÓNICO
  body_html TEXT,                    -- render para Telegram
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  delivered_tg INTEGER NOT NULL DEFAULT 0,
  delivered_web INTEGER NOT NULL DEFAULT 0,
  UNIQUE(session_id, seq)
);

CREATE TABLE dm_sessions (          -- contexto del agente por partida
  session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  scene_summary TEXT NOT NULL DEFAULT '',
  turn_count INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 0,
  locked_by TEXT,                    -- lease: si el agente muere, se libera
  locked_at REAL                     -- solo con timeout
);

CREATE TABLE memory_bible (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                -- lore | npc | ruling | player | recap
  subject TEXT NOT NULL DEFAULT '',  -- "NPC: Vessa", "Jugador: Sylra"
  body TEXT NOT NULL,
  importance INTEGER NOT NULL DEFAULT 3,
  updated_at REAL NOT NULL
);

CREATE VIRTUAL TABLE memory_fts USING fts5(body, content='memory_bible', ...);
```

Notas:

- Van en el `SCHEMA` de `storage.py` **antes** de que existan BDs creadas:
  `CREATE TABLE IF NOT EXISTS` no añade tablas nuevas a una BD ya creada y
  `MIGRATIONS` solo sabe hacer `ALTER TABLE ADD COLUMN`.
- SQLite **FTS5** da recuperación sin infraestructura de vectores. Embeddings
  son un opcional posterior, no un requisito.

---

## 5. Reglas de emisión (evita reescribir 60 comandos)

| Tipo de salida | Por dónde sale |
| --- | --- |
| Narración del DM, anuncios, resultados de combate, muertes | `game_events` → **outbox** → ambas superficies |
| Respuesta puntual a un comando (`/help`, `/char`, errores, `ToolError`) | **directo en la superficie de origen** (como hoy) |
| Mensajes de jugador (web o TG) | `game_events` → el agente los lee |

Los handlers existentes no se reescriben: solo los caminos **narrativos** pasan
por `emit()`. Defensa contra el **doble envío** (un handler que responda directo
*y* emite): test explícito en `test_flow.py`.

---

## 6. Flujo de un turno (con streaming)

```
evento pendiente → lease → context builder → LLM (stream)
                                   │
        web  ◄── WS: chunks de texto en vivo
        TG   ◄── chat action "escribiendo…" + mensaje final
                 (edit-in-place en Telegram = flood; no compensa)
```

1. Jugador escribe (TG o web) → `game_events(player_message)`.
2. Runtime ve eventos pendientes de esa sesión y **toma el lease**
   (`UPDATE … WHERE locked_at < now()`), con timeout para liberarse si el
   proceso revienta.
3. **Context builder** (ver §7).
4. **Loop del agente**: narra o pide tools (`roll`, `change_hp`, `srd_lookup`,
   `start_session`…). Cada llamada queda en `game_events(tool_call)` →
   auditable, y el agente **no puede inventar números**: tiene que tirar.
5. Narración → `game_events(dm_narration)` con dos renders (HTML / Markdown).
6. **Outbox**: web por WS al instante; Telegram respetando 0.4 s y ≤ 4096.
7. Cada K turnos → reescribe `scene_summary`; al cerrar → recap.

**Contexto por turno** (`runtime/context.py`):

```
system prompt del DM
+ biblia de campaña   (5-8 recuerdos más importantes + 3 recuperados por FTS)
+ resumen de escena   (dm_sessions.scene_summary)
+ estado mecánico     (list_party, who_is_here, session_history — LEÍDO, no recordado)
+ últimos N mensajes  (acotado por tokens)
```

**Reinicio = cero pérdida:** todo vive en la DB; el agente rearma su contexto
desde cero. Ningún estado en RAM.

---

## 7. Memoria del agente DM

| Capa | Cuándo se escribe | Cómo |
| --- | --- | --- |
| Ventana corta | cada turno | automático (`game_events`) |
| Resumen de escena | cada K turnos (ej. 12) | modelo pequeño: conserva HP, posición, hilos abiertos, clifhangers |
| Biblia de campaña | cuando el DM decide que es un hecho duradero | tool `remember(kind, subject, body)` |
| Recap de sesión | `end_session` | modelo pequeño → `memory_bible(kind='recap')` |
| Recuperación | cada turno | FTS5 top-3 sobre `memory_bible` + `game_events` |

**Principio: la memoria es para lo narrativo; las tablas son para lo mecánico.**
HP, party, sesión y encuentros **no se recuerdan, se leen** con tools en cada
turno → el agente no puede "recordar" mal un número.

Capas, de más volátil a más durable: ventana corta → resumen de escena →
biblia → recaps → recuperación por búsqueda.

---

## 8. UX abierta: cómo hablan los jugadores en Telegram

En la web es obvio (el chat es el canal dedicado). En Telegram los mensajes son
ruido OOC hasta que se demuestre lo contrario. Opciones:

| | Opción | Notas |
| --- | --- | --- |
| a | Todo el chat de la sesión | simple, pero el OOC llega al agente |
| b | Responder al mensaje del DM o mencionar al bot | preciso, cero comandos que memorizar |
| c | `/say …` o `/dm …` | explícito, se olvida |
| d | `/rp on\|off` por chat | modo rol; OOC cuando está off |

**Recomendación: (b) + (d)** — mención/responde siempre cuenta como rol, y
`/rp on` permite escribir sin más en el chat de la sesión. La web no lo
necesita. *Pendiente de confirmar en M3.*

---

## 9. Hitos

| | Ficheros | Criterio de "hecho" |
| --- | --- | --- |
| **M0** | `storage.py` (4 tablas), `runtime/events.py`, `runtime/outbox.py` | un mensaje emitido llega a TG y queda en `game_events` |
| **M1** | `web/app.py`, `web/auth.py`, `web/panels.py` | abres la web con el código de TG: campaña + ficha + party |
| **M2** | `web/chat.py` | escribes en la web y **el mensaje aparece en Telegram** |
| **M3** | `runtime/agent.py`, `context.py`, `llm.py`, `memory.py` | partida dirigida por IA por Telegram con memoria |
| **M4** | `runtime/outbox.py` (WS) + `web/feed.py` | misma partida, dos superficies, en vivo |
| **M5** | `runtime/memory.py` (resumen + FTS + bible) | el DM recuerda en la sesión 4 algo de la sesión 1 |
| **M6** | `mcp/encounter.py` (~10 tools) | combate rastreado jugable desde ambos lados |

Cada hito deja el repo en verde: `tests/test_units.py`, `tests/test_flow.py`,
`tests/test_mcp.py` (scripts planos, no pytest).

---

## 10. Riesgos

- **Doble envío** si un handler responde directo *y* emite → la tabla de §5 +
  test explícito.
- **Flood de Telegram**: el outbox es el **único** punto que manda a TG;
  respetar 0.4 s y 4096 chars.
- **Coste/latencia**: presupuesto de tokens por turno; el agente solo se
  dispara con eventos pendientes.
- **Paridad superficie**: `test_flow.py` valida HTML de todo mensaje de TG;
  hace falta el equivalente para el canal web (Markdown balanceado).
- **BD compartida**: la web escribe vía `services.py`, jamás `storage.py` a pelo.
- **Paridad de superficie hoy incompleta**: `/fight`, `/hit`, `/kill`, `/heal`
  **no tienen tool** → M6 antes de que un agente dirija combate rastreado.

---

## 11. Fuera de alcance (v1)

- Embeddings / base vectorial (FTS5 basta).
- Varios agentes NPC con propio LLM (un DM por sesión).
- `announce`/broadcast desde los tools (imposible sin Telethon; lo hace el outbox).
- Migración de SQLite a otro motor.
- Móvil nativo / PWA offline.
