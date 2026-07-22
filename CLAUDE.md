# OpenMemory on Raspberry Pi 5 — Local Modifications

This is a fork of [mem0ai/mem0](https://github.com/mem0ai/mem0) with changes to run OpenMemory on Raspberry Pi 5 using Groq (LLM) + Ollama (embeddings) instead of OpenAI.

Fork: `git@github.com:PauliusMorku/mem0.git`
Branch: `rpi5-ollama`

## Architecture

- **LLM:** `llama-3.3-70b-versatile` via Groq API
- **Embeddings:** `nomic-embed-text` via local Ollama (port 11434)
- **Vector store:** Qdrant (Docker container `mem0_store`, port 6333)
- **API:** OpenMemory MCP server (Docker container, port 8765)
- **Groq free tier limits:** 30 RPM / 12K TPM / 100K TPD per model

## Key Files Modified (from upstream)

| File | What changed |
|---|---|
| `openmemory/api/app/utils/__init__.py` | PRIMARY_MODEL constant, MEMORY_ADD_TIMEOUT |
| `openmemory/api/app/utils/memory.py` | Default config uses Groq, custom extraction prompt |
| `openmemory/api/app/utils/categorization.py` | Uses Groq client directly for categorization |
| `openmemory/api/app/mcp_server.py` | 30s timeout on memory add operations |
| `openmemory/api/app/routers/memories.py` | 30s timeout on memory add operations |
| `openmemory/docker-compose.yml` | Removed API_KEY env, bind-mount Qdrant data, qdrant pinned to `v1.17.0-16k` |
| `openmemory/api/requirements.txt` | `mem0ai` pinned to `>=1.0.4,<2.0.0` (see below) |
| `.gitignore` | Added `openmemory/qdrant-data/` |

## MCP Endpoints (as of 2026-07-22 upstream merge)

The server exposes both MCP transports:

- **Streamable HTTP (preferred):** `http://<pi-ip>:8765/mcp/<client>/http/<user>`
  e.g. `http://<pi-ip>:8765/mcp/claude-code/http/pm`. Stateless, JSON responses.
  Not affected by the SSE reconnect bug.
- **SSE (deprecated, kept for old clients):** `http://<pi-ip>:8765/mcp/<client>/sse/<user>`.
  Known bug: after a dropped SSE connection reconnects, tool calls hit an
  uninitialized session → `-32602` / "Received request before initialization
  was complete". Migrate clients to the `/http/` URL instead.

## mem0ai Version Pin — do not unpin blindly

`openmemory/api/requirements.txt` pins `mem0ai>=1.0.4,<2.0.0`. mem0ai 2.x renamed
`vector_store.search(limit=)` to `top_k=`, and upstream's `mcp_server.py` still
passes `limit=10` (unfixed upstream as of 2026-07-22), so an unpinned build
installs 2.x and breaks `search_memory` at runtime. Remove the pin only after
upstream fixes that call site AND 2.x compatibility with existing Qdrant
payloads (written by 1.x) is verified.

## Recurring Backups

`backup-scripts/weekly-backup.sh` backs up the sqlite DB (online backup) and a
checksum-verified Qdrant snapshot to `/mnt/data/workspace/backups/openmemory-weekly/`,
keeping the last 6. Installed in the `pm` user crontab on raspi5 (Sundays 03:30,
log: `backup.log` in the same directory). Restore commands are in the script header.

## Upgrade History

- **2026-07-22:** Merged `upstream/main` (through `dd5f7e39`, merge commit
  `ab0124b3`) — brought in the streamable-HTTP MCP endpoint (upstream #4122)
  and dependency security bumps (mcp SDK 1.28.1, starlette, python-dotenv).
  All local Groq/Ollama customizations preserved; upstream's new `infer` tool
  param kept but defaulted to `False` (local no-fact-extraction behavior).
  UI image NOT rebuilt (upstream UI changes were lockfile-only security bumps).
  Pre-upgrade backups (sqlite DB, Qdrant snapshot, configs, old image tagged
  `mem0/openmemory-mcp:backup-2026-07-22`, pre-merge git HEAD):
  `/mnt/data/workspace/backups/openmemory-upgrade-2026-07-22/`

## Custom Fact Extraction Prompt

The default mem0 extraction prompt has few-shot examples that teach the model to
split related info into separate context-free facts. This causes memory fragmentation.

**Fix:** `custom_fact_extraction_prompt` in `get_default_memory_config()` overrides
the default prompt to preserve user wording and require full context in every fact.

## Environment

- API key: `GROQ_API_KEY` env var (loaded from `openmemory/api/.env`)
- Ollama must be running on the host (accessible from Docker via gateway)
- Qdrant data persisted to `openmemory/qdrant-data/` (bind mount, gitignored)
- Web UI env: `openmemory/.env` (gitignored) must set `USER=pm` and
  `NEXT_PUBLIC_API_URL=http://172.26.1.1:8765` (ZeroTier IP) so the
  browser can reach the API when accessing the dashboard remotely.
  The UI is rebuilt with these values baked in — after changing them,
  run `docker compose up -d --build openmemory-ui`.

## Running

```bash
cd openmemory
docker compose up -d
```

Rebuild after code changes:
```bash
docker compose up -d --build openmemory-mcp
```

**After rebuilding**, clients connected via the deprecated SSE endpoint go stale
and produce `MCP error -32602` until their session restarts. Clients on the
streamable-HTTP `/http/` endpoint are stateless and unaffected.
