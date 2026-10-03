# Plan: Servidor MCP para D&D Helper Bot

Registro del plan acordado el 2026-10-03. Revisión previa hecha con graphify
(`graphify-out/graph.json`).

---

## 0. Contexto y decisiones

Objetivo: un servidor MCP que exponga las mismas funcionalidades que el bot de
Telegram, con una arquitectura **simple, fácil de entender y sin
sobreingienería**.

| Eje | Decisión |
| --- | --- |
| Arquitectura | Proceso nuevo, mismo paquete `dndbot/`, mismo patrón que los handlers (`register(mcp)`) |
| Refactor | Extraer `dndbot/services.py` compartido + 3 refactors de apoyo |
| Alcance v1 | Fases A+B+C completas (~30 tools) |
| Transporte | Solo `stdio` local. Sin HTTP, sin auth (el servidor es local/trusted) |
| `/announce` | **Fuera de alcance**: necesita un cliente Telethon para DMear a los jugadores |
| Salida | Markdown (no HTML), errores como `ToolError` |
| Estado | Tools stateless: sin wizards, sin dice pad, sin botones |

### Qué se reutiliza sin tocar nada

| Reutilizable tal cual | Acoplado a Telegram |
| --- | --- |
| `dice.py` (`parse`/`roll`/`RollResult`/`DiceError`) — puro | `common.resolve_campaign(event,…)` (lee `sender_id` + `chat.title`) |
| `srd.py` (`SRDClient`, singleton `srd`, `.cache/`) — puro | `common.member_campaign` / `dm_campaign` (tragan excepciones y hacen `event.reply`) |
| `storage.py` (entero, async, cero import de telethon) | `common.send_view`, `is_callback`, `command_argument` |
| `formatting.py` (HTML de Telegram pero sin telethon) | `keyboards.py` (devuelve `Button.inline`) |
| `logs.py`, `config.py` (salvo la validación) | `handlers/*.py` (toda la lógica inline) |
| `common`: `ensure_member`, `ensure_dm`, `is_admin`, `safe`, `plain_name`, `relative`, `duration` | `config.check()` exige `API_ID`/`API_HASH`/`BOT_TOKEN` |

### Hechos que condicionan el diseño

- SQLite ya corre en **WAL** → bot y servidor MCP comparten
  `dndbot/data/dndbot.db` en procesos paralelos sin más.
- `.cache/` se comparte igual → un solo calentamiento del SRD.
- `mcp` no está instalado en `.venv` → hay que añadirlo.
- SDK oficial hoy: **mcp v2** (`pip install mcp` → 2.x). `FastMCP` se renombró
  a `MCPServer`; solo `ToolError` muestra tu mensaje al modelo.

---

## 1. Plan de implementación del servidor MCP

### Estructura final

```
mcp_server.py                 # entry → MCPServer(...).run(transport="stdio")
dndbot/
  sheet.py                    # NUEVO: apply_hp_delta, SET_FIELD_COLUMNS, ABBREV, reglas de nivel
  services.py                 # NUEVO: join/approve/sesiones con guards — lo usan handlers Y mcp
  mcp/
    __init__.py               # build_server(): registra los 5 dominios
    runtime.py                # lifespan: db.connect + srd.start/warm | finally close
    identity.py               # actor(user_id) → MCP_USER_ID
    render.py                 # HTML Telegram → Markdown (punto único de conversión)
    dice.py                   # tools de dados
    srd.py                    # tools SRD
    character.py              # tools de personajes
    campaign.py               # tools de campañas y sesiones
    meta.py                   # whoami, help, tutorial, errors
tests/test_mcp.py             # script plano, env antes de importar, ALL GREEN + exit 0
```

`dndbot/mcp/*` replica el patrón `handlers/*.register(client)`: cada módulo
expone `register(mcp)`. Quien entiende el bot entiende el MCP de un vistazo.
Sin capas nuevas, sin HTTP, sin servicios intermedios.

### API del SDK (mcp v2)

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("dndbot", version="0.1.0", instructions=INSTRUCTIONS,
                lifespan=lifespan)

# Cada módulo dndbot/mcp/*.py expone register(mcp); las tools son funciones
# de nivel de módulo (así tests las llama directamente, sin transporte).
def register(mcp) -> None:
    mcp.add_tool(roll, description=cleandoc(roll.__doc__ or ""))

mcp.run(transport="stdio")
```

Detalles reales de la v2.3.0 comprobados en el `venv`:

- `@mcp.tool()` exige los paréntesis; sin ellos lanza `TypeError`.
- `description=cleandoc(doc)`: sin esto el SDK usa `fn.__doc__` **tal cual** y
  la indentación de la docstring se cuela en la descripción (4 espacios
  seguidos = bloque de código en Markdown).
- `structured_output` se deduce de la anotación de retorno; `-> str` sirve.
- `Context` se inyecta por anotación de tipo y nunca aparece en el esquema.
- Todo lo que no sea `ToolError` llega al modelo como
  `Error executing tool <name>` (el traceback se queda en el log).

Reglas de error del SDK:

- `raise ToolError(msg)` → `is_error=True` y **el modelo lee tu mensaje** (y
  puede reintentar). Es lo correcto para errores de dominio.
- Cualquier otra excepción → el modelo solo ve `Error executing tool <name>`;
  el traceback va al log del servidor. Correcto: no filtra internals.
- `MCPError` → error de protocolo, el modelo no ve nada. No usar aquí.
- **Nunca devolver** un string de error como valor: `is_error=False` haría que
  parezca éxito.

### Lifespan (espejo de `main()` sin Telethon)

```python
@asynccontextmanager
async def lifespan(server) -> AsyncIterator[AppContext]:
    await db.connect()
    await srd.start()
    await srd.warm(("monsters", "spells", "magicitems", "rules"))
    try:
        yield AppContext()
    finally:
        await srd.close()
        await db.close()
```

### Pasos

**S1 · Cimientos**

- `mcp>=2` en `requirements.txt` + install en `.venv`.
- `config.check(telegram: bool = True)`: el MCP llama `check(telegram=False)`
  para no exigir credenciales de Telegram.
- `MCP_USER_ID` nuevo en `config.py` + `.env.example`.

✔ Verificación: el paquete importa sin `API_ID`/`API_HASH`/`BOT_TOKEN`.

**S2 · Refactors de dominio (los 4, behavior-preserving)**

1. `common.resolve_campaign_for(user_id, argument, chat_title=None)` — extraer
   el núcleo puro. El `resolve_campaign(event, …)` actual queda como wrapper
   delgado (`event.sender_id` + `event.chat.title`). **Cero cambios en handlers.**
2. `dndbot/sheet.py` — mover `apply_hp_delta`, `SET_FIELD_COLUMNS`, `ABBREV` y
   las reglas de nivel fuera de `handlers/character.py` (evita que MCP importe
   un handler: inversión de dependencias).
3. `dndbot/services.py` — `request_join`, `approve_join`, `deny_join`,
   `start_session`, `end_session` con sus guards; `handlers/campaign.py` pasa a
   llamarlos (adelgaza los handlers y evita que las reglas diverjan).
4. `config.check(telegram=…)` (si no se hizo en S1).

✔ Verificación: `tests/test_units.py` y `tests/test_flow.py` siguen en
`ALL GREEN`. Si algo falla aquí, el refactor no era preservador.

**S3 · Scaffold vivo**

- `mcp_server.py` + `runtime.py` (lifespan) + `render.py` + `identity.py`.
- Solo 3 tools: `whoami`, `roll`, `srd_lookup`.

✔ Verificación: un cliente MCP conecta por stdio, `tools/list` responde y la
primera tool real funciona.

**S4 · Fase A** — 10 tools de solo lectura (ver §2).

**S5 · Fase B** — 10 tools de personajes sobre `sheet.py` + `db.*`.

**S6 · Fase C** — 14 tools de campañas/sesiones sobre `services.py`, con
`resolve_campaign_for` + `ensure_member`/`ensure_dm` y `ToolError` en cada
error de dominio.

**S7 · Cierre**

- `tests/test_mcp.py` completo (llama a las funciones de tool directamente,
  sin transporte).
- Tabla de tools en `README.md`.
- Notas de wiring en `AGENTS.md` (el equivalente a la lista `BOT_COMMANDS`).
- Bloque de configuración del cliente MCP:

```json
"mcp": {
  "dndbot": {
    "type": "local",
    "command": ["/…/DnD-helper-bot/.venv/bin/python", "/…/DnD-helper-bot/mcp_server.py"]
  }
}
```

**Riesgo**: S1–S3 concentran todo el riesgo (refactor + arranque). S4–S6 son
aditivos y de riesgo bajo.

---

## 2. Plan para agregar los tools

### Inventario: 48 comandos → ~30 tools en 6 dominios

**Fase A — solo lectura, sin identidad ni escrituras (10)**

| Tool | Comandos que cubre |
| --- | --- |
| `roll(expression)` | `/roll`, `/adv`, `/dis` (el parser ya acepta `adv:+4`) |
| `party_initiative(bonus=0)` | `/init` |
| `srd_lookup(category, name)` | los 13: `/monster /spell /item /rule /equipment /condition /class /race /subrace /skill /prof /damagetype /align` |
| `srd_search(query, limit=12)` | `/search` (fan-out de 8 categorías) |
| `srd_random(category, challenge_rating=None)` | `/randmonster` (`"2"`, `"1/4"`), `/randspell` |
| `srd_list(category, page=1)` | el browser de `/start` → SRD (8 por página) |
| `get_help(topic=None)` | `/help` |
| `get_tutorial(topic=None)` | `/tutorial` (estático) |
| `whoami(user_id=None, name=None)` | `/whoami` + descubrir ids |
| `recent_errors(user_id=None)` | `/errors` (solo si `is_admin`) |

**Fase B — personajes (10)**

`create_character(...)` · `get_character` · `list_party` · `change_hp` ·
`set_hp` · `level_up` · `set_xp` · `set_character_field` · `add_note` ·
`switch_character`

- `create_character` sustituye el wizard de 6 pasos por **una sola llamada**
  (name, class, subclass, race, background, level, 6 abilities, ac, max_hp,
  speed).
- El resto son adaptadores delgados sobre `db.*` + `rules.apply_hp_delta`.
- Cubre `/newchar /char /party /hp /sethp /level /levelup /xp /set /note
  /switch`.

**Fase C — campañas (9)**

`list_campaigns` · `create_campaign` · `join_campaign` · `list_join_requests` ·
`resolve_join(request_id, approve)` (los botones *Approve/Deny*) ·
`select_campaign` · `get_roster` · `campaign_info` · `leave_campaign`

**Fase C — sesiones (5)**

`start_session` · `end_session` · `session_history` · `checkin` ·
`who_is_here`

**Meta (incluida en A)**

`whoami` · `get_help` · `get_tutorial` · `recent_errors`

**Excluido a conciencia (Fase D)**

- `/announce` → necesita un cliente Telethon para DMear a los jugadores.
- Dice pad (`dice:*`) → estado por usuario, UI pura.
- Wizards (`DRAFT`, `PAD`) → el MCP es stateless.
- Menús y botones (`menu:*`, `tut:*`, `camp:*` UI, `char:hp/level/xp`) → sin
  valor de dominio.

### Identidad

- Los tools que necesitan actor aceptan `user_id: int | None = None`.
- Si va `None` se usa `config.MCP_USER_ID`; si no hay ninguno → `ToolError`
  explicando cómo descubrirlo (`whoami`).
- Campaña: parámetro `campaign: str | None` (nombre o código) resuelto con
  `resolve_campaign_for`. **Sin** fallback por título de chat (eso es de
  Telegram).

### Checklist para agregar un tool (repetible)

1. ¿La lógica ya vive en `dice.py` / `srd.py` / `storage.py`? → llámala directo.
2. ¿Vive inline en un handler?
   - trivial (<5 líneas) → re-implementa en `dndbot/mcp/*.py`;
   - la comparten bot y MCP → extráela primero a `services.py`/`sheet.py` y que
     ambos la usen. **Nunca copiar en `dndbot/mcp/`.**
3. Tool tipado + docstring donde la descripción **incluya la sintaxis del
   comando del README** (el modelo aprende de ahí).
4. Permisos con `resolve_campaign_for` + `ensure_member`/`ensure_dm`.
5. Excepciones de dominio → `raise ToolError(msg_plano)`.
6. Salida por `render.to_markdown` (o JSON simple si es una lista).
7. Registrar en el `register(mcp)` del dominio y en la lista de
   `dndbot/mcp/__init__.py`.
8. Caso en `tests/test_mcp.py` y fila en la tabla de tools del `README.md`.

### Reglas de mantenimiento

1. Un tool nuevo = 1 docstring con la sintaxis del comando + 1 caso en
   `test_mcp.py` + fila en el `README.md`.
2. Si la lógica crece y la necesitan ambos lados, va a `services.py` o
   `sheet.py`.

### Qué NO hacer

- Capa de servicios genérica, repositorios, DTOs.
- HTTP/auth en v1.
- Replantear los handlers.
- Estado en memoria en los tools (wizards, pads, tokens).

---

## 3. Recomendaciones

### Para el bot de Telegram

1. **Adelgazar `campaign.py` (671 líneas) y `character.py` (721 líneas)**
   extrayendo los flujos a `services.py` — es la mejora que más reduce riesgo
   y es justamente lo que el MCP reutiliza. Los handlers quedan como
   adaptadores (parsear → servicio → HTML). *(Se hace en S2.)*
2. **Bug real en `/init`**: `bonus = int(argument)` está *fuera* del `try` →
   `/init foo` cae en el guard y responde "algo salió mal" en vez de un
   mensaje útil. Moverlo dentro.
3. **Excepciones con HTML pre-renderizado**: `NoCampaign("…llama a
   <code>/join</code>…")` mezcla dominio y presentación. Mensajes planos en la
   excepción + formato en la capa de salida sirve a Telegram y al MCP con el
   mismo código.
4. **Estado en memoria que muere al reiniciar**: `_TOKENS` de `keyboards.py`
   (los botones SRD mueren tras un restart, ya documentado en `AGENTS.md`),
   `DRAFT` y `PAD`. Para los tokens: derivarlos de forma determinista (hash
   corto de `category:index`) en vez de un contador incremental.
5. **Sin migraciones de schema**: `CREATE TABLE IF NOT EXISTS` no añade
   columnas a una DB ya creada. Un `PRAGMA user_version` + chequeo al
   conectar cuesta 10 líneas y evita sorpresas silenciosas.
6. **`/errors` lo lee cualquiera** → restringir a `is_admin`
   (`common.is_admin` ya existe).
7. **`/randspell N` ignora su argumento** → usarlo como filtro de nivel o
   quitarlo (ahora mismo miente al usuario).
8. **Descripciones reales en `BOT_COMMANDS`**: hoy publica `/<nombre>` como
   descripción; una frase por comando aprovecha el menú `/` de Telegram.
9. **Efecto secundario oculto**: `resolve_campaign` cambia el
   `active_campaign` del usuario cuando pasas un argumento explícito.
   Documentarlo o hacerlo opcional.

### Para las tools del MCP

10. **Stateless**: una llamada → una respuesta. `create_character` de una sola
    vez en vez de 6 pasos.
11. **Markdown, no HTML** (`render.py` como único punto) y **`ToolError` para
    todo lo que el modelo pueda corregir** (nombre mal escrito, no ser DM).
12. **Identidad y campaña explícitas**: `MCP_USER_ID` + `user_id` opcional;
    jamás resolver por título de chat.
13. **Las descripciones son la documentación**: cada tool declara la sintaxis
    equivalente del comando.
14. **stdio local por ahora.** Si algún día hay HTTP: auth antes de exponer,
    porque `recent_errors` y cualquier tool de escritura son sensibles.
15. **Compartir `.cache/` y SQLite (WAL ya activo)** entre bot y MCP: un solo
    calentamiento del SRD y cero duplicación de datos; mantener transacciones
    cortas (SQLite tiene **un** escritor).
16. **Tests** con el estilo propio del repo: script plano, env antes de
    importar, `ALL GREEN` + exit 0 — no inventar pytest.
17. **Escapar el texto del jugador en Markdown**: `render.to_markdown`
    convierte etiquetas y entidades, pero no escapa los especiales Markdown
    del texto que viene de la base, así que un nombre con `_` o `*` puede
    renderizar raro en las respuestas reutilizadas del bot
    (`get_character`, `list_party`, `change_hp`…). Las tools que arman su
    propia respuesta sí pasan el nombre por `esc_md` y quedan bien.
    Corregirlo de verdad = tokenizar `to_markdown` y escapar los trozos de
    texto sin tocar las etiquetas que genera.

### Tareas independientes

Las mejoras 2–9 del bot **no bloquean** al MCP: se hacen después de S7 o en
paralelo, por separado.

---

## 4. Apéndice: revisión con graphify

Grafo en `graphify-out/graph.json` (build de 2026-10-03; esa corrida no
generó `GRAPH_REPORT.md` ni `graph.html`, solo `graph.json` + manifest, así
que las secciones siguientes se derivaron del grafo directamente).

- **Corpus**: ~475 nodos · >1000 aristas · 24 comunidades (0–23), todo
  `_origin: ast` (repo de solo código).
- **God nodes** (mayor grado): `storage.Database` (com. 1, ~60 métodos) ·
  `common.resolve_campaign` (14 entradas desde 6 módulos) ·
  `common.send_view` (8 entradas) · `handlers/*.register` ×6 ·
  `srd.SRDClient.get/.index/.detail` (com. 2).
- **Conexiones sorprendentes** (cruzan comunidades):
  1. `handlers/dice.dice_pad` → `handlers/srd_lookup.send_entry`: el botón
     *Monster* del pad cruza de dados a SRD.
  2. `keyboards.dice_roll_summary` → `handlers/dice.describe`: lógica de
     formateo viviendo en el módulo de teclados.
  3. `handlers/character` → `srd.index("classes"/"races")`: el asistente de
     creación alcanza la API externa para sugerencias.
  4. Nodos de `tests/test_flow.py` mezclados en las comunidades 11/14/19/22
     con handlers de producción.
- **Preguntas que sugiere el grafo**:
  - ¿Qué módulos son puros y cuáles dependen de Telethon? *(respondida en §0)*
  - ¿Por qué `resolve_campaign` es el nodo puente entre 6 handlers?
  - ¿Qué lógica de dominio vive dentro de handlers y no en módulos de dominio?
    *(la que hay que extraer: §1 S2)*

---

## 5. Estado de la implementación

Actualizado conforme se avanza.

- [x] **S1 · Cimientos** — `mcp>=2,<3` en `requirements.txt` (instala 2.3.0);
  `config.check(telegram: bool = True)`; `MCP_USER_ID` en `config.py` y
  `.env.example`.
- [x] **S2 · Refactors de dominio** — `resolve_campaign_for(user_id, argument,
  chat_title)` con `resolve_campaign(event, …)` como wrapper (lee
  `event.chat.title` solo cuando no hay argumento, para no costar un entity
  lookup de más); `dndbot/sheet.py` (planificado como `rules.py`: se
  renombró porque "rules" es también una categoría del SRD);
  `dndbot/services.py` con `Refused`, `JoinOutcome`, `create_campaign`,
  `request_join`, `join_request_context`, `approve_join`, `deny_join`,
  `leave_campaign`, `start_session`, `end_session`.
  ✔ `test_units` 77/77 y `test_flow` 150/150 en `ALL GREEN`.
- [x] **S3 · Scaffold vivo** — `mcp_server.py` + `dndbot/mcp/{__init__,
  runtime, identity, render, dice, srd, meta}.py` con `roll`, `srd_lookup` y
  `whoami`. ✔ Verificado con cliente en proceso **y** por stdio real:
  `initialize` → `serverInfo {name: dndbot, version: 0.1.0}` → `tools/list`
  (3 tools) → `tools/call` de `roll` y de `srd_lookup monster/goblin`
  (SRD en Markdown), stdout solo con JSON-RPC (4/4 líneas), exit 0 al cerrar
  stdin; `test_units` y `test_flow` siguen en `ALL GREEN`.
- [x] **S4 · Fase A — 10 tools de solo lectura** — `roll`,
  `party_initiative`, `srd_lookup`, `srd_search`, `srd_random`, `srd_list`,
  `get_help`, `get_tutorial`, `whoami`, `recent_errors`. `whoami` cumple la
  firma del plan (`user_id`, `name`): el modo `name` busca por
  username/nombre con el nuevo `db.find_user` para descubrir ids.
  Lógica extraída para que bot y MCP la compartan (behavior-preserving):
  `dice.initiative_order`, `srd.SEARCH_CATEGORIES` + `srd.BROWSE_PAGE` +
  `SRDClient.search_all`, y `help.HELP_PAGES`/`help.HELP_TOPICS` (los dos
  dicts inline de `/help` pasan a usarlos). `dndbot/mcp/inputs.py` resuelve
  la campaña de una llamada y convierte `NoCampaign` en `ToolError`.
  ✔ Las 10 tools listadas y llamadas en proceso (+ `whoami name=`), probe
  stdio real sin una sola línea no-JSON-RPC en stdout, `test_units` 77/77 y
  `test_flow` 150/150 en `ALL GREEN`.
- [x] **S5 · Fase B — 10 tools de personajes** — `create_character`,
  `get_character`, `list_party`, `change_hp`, `set_hp`, `level_up`,
  `set_xp`, `set_character_field`, `add_note`, `switch_character` (cubren
  `/newchar /char /party /hp /sethp /levelup /level /xp /set /note
  /switch`). Extraído para compartir con el bot (behavior-preserving):
  `sheet.hp_bar` (adiós a las dos copias de la barra: la usan `stat_block` y
  `hp_reply`), `sheet.party_block`, `sheet.hp_reply`,
  `services.character_for` (el cuerpo de `load_character`),
  `services.switch_to` (el cuerpo de `/switch`, que devuelve `(character,
  html)`) y `inputs.member` (actor + campaña + pertenencia en una llamada).
  `create_character` sustituye al wizard de 6 pasos por una sola llamada y
  respeta la guardia de `/newchar` con `force: true`.
  ✔ 20 tools en `tools/list`; sondeo en proceso de las 10 nuevas (creación,
  no-miembro, HP con temporal y muerte, subida y límites de nivel, campos
  con modificador, notas, cambio de activo); `test_units` 77/77 y
  `test_flow` 150/150 en `ALL GREEN`.
  ⚠ `test_flow` solo prueba `/switch` sin nombre (la rama que cambia de
  hoja la ejercita el sondeo vía `services.switch_to`) y no prueba
  `/newchar force`.
- [x] **S6 · Fase C — 14 tools de campañas y sesiones** — `list_campaigns`,
  `create_campaign`, `join_campaign`, `list_join_requests`, `resolve_join`,
  `select_campaign`, `get_roster`, `campaign_info`, `leave_campaign`,
  `start_session`, `end_session`, `session_history`, `checkin`,
  `who_is_here` (cubren `/campaigns /newcampaign /join /pending /select
  /roster /campaign /leave /startsession /endsession /session /checkin
  /who`, más los botones *Approve/Deny* vía `resolve_join`). Comparten con
  el bot: `services.select_campaign` y `services.checkin` (flujos nuevos)
  y la sección `# -- words both sides send` con el HTML que el handler
  construía inline (`campaign_summary`, `session_started_text`,
  `campaigns_pick_text`, `campaigns_list_text`,
  `pending_requests_text`, `roster_text`, `roster_brief_text`,
  `at_table_text`, `session_history_text`); `inputs.dm` añade la guardia
  de DM a `inputs.member`. De paso se deduplicó `_approve_join`
  (`services.approve_join` ya hace `add_member` + `set_active_campaign`).
  ✔ 34 tools en `tools/list`; sondeo en proceso de las 14 (cola con
  `#id`, aprobación idempotente, DM-only en sesiones, sentarse y salir);
  `test_units` 77/77 y `test_flow` 150/150 en `ALL GREEN`, con los 13
  comandos de campaña ejercitados.
  ⚠ **Dos brechas de superficie (Telethon, documentadas)**: nada se
  transmite ni se DM desde MCP, así que una sesión abierta aquí no avisa a
  los jugadores y una solicitud aprobada aquí no avisa al solicitante —
  ambos se enteran al preguntar en Telegram. `pending_requests_text`
  necesita `with_ids=True` porque el bot esconde los ids tras botones y
  `resolve_join` los necesita.
- [x] **S7 · Cierre** — `tests/test_mcp.py` (278 comprobaciones: inventario
  de 34 tools con sus descripciones, contrato de `ToolError` en texto plano,
  resultados libres de HTML de Telegram, y recorrido de identidad, dados,
  SRD, personajes, campañas y sesiones sobre una base temporal — sin
  transporte, llamando las funciones directamente); tabla de las 34 tools
  con su equivalente de Telegram en `README.md` (junto a la sección nueva
  *MCP server*, `MCP_USER_ID` en el ejemplo de `.env` y el árbol de
  `Layout` actualizado con `help.py`, `sheet.py`, `services.py` y
  `dndbot/mcp/`); wiring en `AGENTS.md` (arranque, los tres cambios que
  exige una tool nueva, la prohibición de importar `handlers/` desde
  `mcp/`, identidad/`inputs` y reglas de `render`); bloque de configuración
  del cliente MCP en `README.md`.
  ✔ Las tres suites en `ALL GREEN`: `test_units` 77/77, `test_flow`
  150/150, `test_mcp` 278/278.

### Notas de la implementación

- Las tools son funciones **a nivel de módulo** en `dndbot/mcp/*.py` y
  `register(mcp)` las da de alta con
  `add_tool(fn, description=cleandoc(fn.__doc__))`: quedan importables para
  `tests/test_mcp.py` sin transporte, y el `cleandoc` evita que la
  indentación de la docstring se cuele en la descripción (el SDK usa
  `fn.__doc__` tal cual).
- `render.py` expone tres puntos de conversión: `strip` (HTML → texto plano,
  para mensajes de `ToolError`), `to_markdown` (HTML → Markdown, para
  salidas) y `esc_md` (escapa nombres de jugador: `\` `` ` `` `*` `_` `[`
  `<`…). El orden es siempre tags primero y entidades al final.
- El arranque replica `main()` sin Telethon: `db.connect` → `srd.start` →
  `srd.warm` → `expire_stale_requests` → (servicio) → `srd.close` +
  `db.close`.
- `logging.StreamHandler()` va a **stderr** por defecto y no hay ningún
  `print()` en el paquete, así que el transporte stdio no se corrompe;
  `mcp_server.py` solo imprime configuración errónea a stderr.
- `/announce` sigue fuera de alcance (necesita cliente Telethon para DMs).
- **Filtro por CR (arreglado en S4)**: `random_by_cr` leía
  `challenge_rating` de las entradas del índice REST, donde ese campo nunca
  existe (el índice solo trae `level` para hechizos), así que
  `/randmonster 1/4` respondía siempre *"No monsters with CR 1-4 in the
  SRD"*. Ahora `SRDClient.challenge_ratings()` lo trae en **una** petición
  al endpoint GraphQL del mismo API (`{ monsters(limit: 1000) { index
  challenge_rating } }`: ~0.4 s, 15 KB, 334 monstruos) y lo cachea en
  memoria con `INDEX_TTL`; `_post_json` comparte la política de reintentos
  de `_get` mediante `_retry`. De paso, `/randmonster 2` (número suelto)
  filtra de verdad, como promete `/help`.
- **Sintaxis de `srd_random`**: `"2"` es el CR exacto y `"1/4"` es el *rango*
  1–4 — igual que el comando, la barra separa los extremos y no es una
  fracción (`"0.25"` sí sirve para el CR ¼). Queda dicho en la descripción
  de la tool; aclararlo en `/help` cae en la recomendación 7.
- **`recent_errors` exige `ADMIN_ID`**: el comando `/errors` del bot no
  comprueba nada (recomendación 6), pero Fase A lo marcaba como solo-admin y
  el log trae trazas y rutas locales, así que la tool sí filtra. Divergencia
  deliberada y temporal: al aplicar la recomendación 6 ambos quedan igualados.
- **Los mensajes compartidos conservan los comandos de Telegram**
  (`/newchar`, `/checkin`, `/endsession`…): el mapa tool ↔ comando está en
  las descripciones (punto 3 del checklist), así que un `ToolError` que dice
  *“…with `/endsession`"* se resuelve con la tool `end_session`. Lo que sí
  se escribe a mano en MCP son las frases **que dirigen a la acción** (los
  estados vacíos y las confirmaciones): ahí se nombran las tools, no los
  comandos.
- **Nada de Telethon desde MCP**: `start_session`, `end_session` y
  `resolve_join` no broadcastean ni envían DMs, porque eso necesita el
  cliente de Telegram. El jugador se entera al preguntar en el bot.
- **Solicitantes sin fila en `users`**: `display_name` devuelve *"Someone"*
  para un id que nunca ha escrito al bot (los nombres los registra
  `handlers/core.py` con `upsert_user` en cada mensaje). Solo puede pasar
  si se crea una solicitud con `join_campaign` desde MCP para un id que no
  usa Telegram; en el flujo normal el solicitante escribió `/join` y tiene
  nombre.
