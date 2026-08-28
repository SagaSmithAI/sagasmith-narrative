# SagaSmith Narrative MCP

[中文说明](README-zh.md)

[Website](https://sagasmithai.github.io) · [Platform overview](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md) · [SagaSmith Web](https://github.com/SagaSmithAI/SagaSmith-Web) · [Content catalog](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

> Current source: `sagasmith-narrative/packages/mcp`. It is released from the Narrative vertical monorepo with its Domain and Skills contracts.

An authoritative, system-neutral MCP for long-form tabletop narrative play. It
depends on `sagasmith-core` for durable campaigns, transactions, revisions,
documents, continuity ledgers, snapshots, and branches.

The base runtime has two phases: `lobby` and `play`. A profile may opt into an
authoritative `conflict` phase. On MCP 2026-07-28, `tools/list` is complete,
sorted and privately cacheable for 300 seconds. The Host selects a phase- and
task-appropriate subset for the model; every call still revalidates role, phase,
campaign and revision. `exposure` returns an expiring navigation handle and
never grants authority. Legacy connection exposure and `tools/list_changed`
remain only in the migration adapter.

The server never guesses rules from prose. A campaign binds an immutable profile
version and checksum. Profiles may use Level 0 (explicit Agent/human rulings) or
the limited pure Level 1 mechanics in the sibling Narrative Domain package. More complex rules require a
separate system provider.

Lobby administrators can inspect campaign-scoped profile/Pack drafts through
`narrative_query`. Access changes support grant and revoke operations with
last-owner protection; controlled actors can be updated through `actor_change`.
Recovery queries and snapshot/branch mutations are administrator-only.
Snapshots remain independently restorable full state documents at the public
boundary. Core schema v8 stores each document as one bounded, checksummed
`zlib-1` record; restore and branch checkout do not replay an ancestor chain.

See [Architecture and authority](docs/architecture.md) and
[Profile and Pack lifecycle](docs/profile-and-pack.md) for the durable product
boundary. Three self-authored regression campaigns live in `fixtures/ash-harbor`,
`fixtures/moss-road-seasons`, and `fixtures/echo-manor-voices`. The third is a
15-session, five-NPC character-performance campaign with declared goals,
private motives, red lines, false beliefs, relationship arcs, voice markers,
isolated dialogue, and a recovered alternate ending.

## Development

```powershell
uv sync --all-packages --all-extras
uv run --no-sync pytest packages/mcp/tests
uv run --no-sync ruff check packages/domain packages/mcp
```

Run the original campaign fixtures concurrently through real stdio MCP sessions:

```powershell
uv run --no-sync python packages/mcp/scripts/regression_parallel_campaigns.py --output .runs/parallel
```

The runner opens real stdio MCP sessions, uses a separate session identity for
each principal, executes every declared route step, follows a focused alternate
branch, and emits machine-readable per-campaign timelines and a combined
summary. A non-zero exit means the run is not accepted.

The Agent Host integration uses the Narrative workspace and Agent repository environments: the
test process runs with `../SagaSmith-agent/.venv/Scripts/python.exe`, while the
spawned MCP server runs with this repository's `.venv/Scripts/python.exe`:

```powershell
../SagaSmith-agent/.venv/Scripts/python.exe -m pytest -q packages/mcp/tests/test_agent_host_integration.py
```

Run locally over stdio with `sagasmith-narrative-mcp`. The same authoritative
handlers are also available over loopback-only Streamable HTTP:

```powershell
$env:SAGASMITH_NARRATIVE_MCP_TRANSPORT = "streamable-http"
$env:SAGASMITH_NARRATIVE_MCP_HTTP_HOST = "127.0.0.1"
$env:SAGASMITH_NARRATIVE_MCP_HTTP_PORT = "8770"
sagasmith-narrative-mcp
```

Both transports expose identical tool schemas, errors, revisions, idempotency,
and authority behavior. Non-loopback Streamable HTTP requires
`SAGASMITH_AUTH_CONTEXT_SECRET` and a dedicated, audience-scoped Host delegation
on every request. Browser or unrelated-audience bearer tokens must never be
passed through to the MCP.

Its independent default home is
`~/.sagasmith/narrative-mcp`. Set `SAGASMITH_NARRATIVE_MCP_HOME` to relocate it,
`SAGASMITH_NARRATIVE_MCP_DATABASE_URL` to use an explicit database, and
`SAGASMITH_NARRATIVE_MCP_BOUND_PRINCIPAL_ID` when the transport authenticates
one principal.

The server applies Core Alembic migrations at startup and requires the current
Snapshot schema v8. Before deployment, stop the server and take a consistent
backup of the SQLite database (including a settled WAL), or use the external
database's native backup mechanism. Protocol rollback selects the documented
legacy adapter; data rollback still restores the database together with matching
Core and MCP versions as one unit. See
[Protocol compatibility](docs/protocol-compatibility.md).

Without `SAGASMITH_NARRATIVE_MCP_BOUND_PRINCIPAL_ID`, stdio and loopback HTTP
are trusted single-user local modes; model-supplied principal fields are not
multiplayer authentication. A multiplayer deployment must bind one
authenticated principal per MCP process through a trusted transport.
Shared-principal HTTP exposure is not supported and the server must not be
published directly to a network.
