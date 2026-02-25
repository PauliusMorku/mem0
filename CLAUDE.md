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
| `openmemory/api/config.json` | Groq LLM + Ollama embedder + Qdrant config |
| `openmemory/api/default_config.json` | Same as config.json (fallback defaults) |
| `openmemory/api/app/utils/__init__.py` | PRIMARY_MODEL constant, MEMORY_ADD_TIMEOUT |
| `openmemory/api/app/utils/memory.py` | Default config uses Groq, custom extraction prompt |
| `openmemory/api/app/utils/categorization.py` | Uses Groq client directly for categorization |
| `openmemory/api/app/mcp_server.py` | 30s timeout on memory add operations |
| `openmemory/api/app/routers/memories.py` | 30s timeout on memory add operations |
| `openmemory/docker-compose.yml` | Removed API_KEY env, bind-mount Qdrant data |
| `.gitignore` | Added `openmemory/qdrant-data/` |

## Custom Fact Extraction Prompt

The default mem0 extraction prompt has few-shot examples that teach the model to
split related info into separate context-free facts. This causes memory fragmentation.

**Fix:** `custom_fact_extraction_prompt` in `get_default_memory_config()` overrides
the default prompt to preserve user wording and require full context in every fact.

## Environment

- API key: `GROQ_API_KEY` env var (loaded from `openmemory/api/.env`)
- Ollama must be running on the host (accessible from Docker via gateway)
- Qdrant data persisted to `openmemory/qdrant-data/` (bind mount, gitignored)

## Running

```bash
cd openmemory
docker compose up -d
```

Rebuild after code changes:
```bash
docker compose up -d --build openmemory-mcp
```

**After rebuilding**, restart any Claude Code sessions connected via MCP —
the SSE connection goes stale and produces `MCP error -32602` until reconnected.
