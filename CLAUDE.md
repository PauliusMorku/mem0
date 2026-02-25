# OpenMemory on Raspberry Pi 5 — Local Modifications

This is a fork of [mem0ai/mem0](https://github.com/mem0ai/mem0) with changes to run OpenMemory on Raspberry Pi 5 using Groq (LLM) + Ollama (embeddings) instead of OpenAI.

Fork: `git@github.com:PauliusMorku/mem0.git`
Branch: `pi5/groq-ollama-setup`

## Architecture

- **LLM:** `llama-3.3-70b-versatile` via Groq API (primary), `openai/gpt-oss-120b` (fallback on rate limit)
- **Embeddings:** `nomic-embed-text` via local Ollama (port 11434)
- **Vector store:** Qdrant (Docker container `mem0_store`, port 6333)
- **API:** OpenMemory MCP server (Docker container, port 8765)
- **Groq free tier limits:** 30 RPM / 12K TPM / 100K TPD per model

## Key Files Modified (from upstream)

| File | What changed |
|---|---|
| `openmemory/api/config.json` | Groq LLM + Ollama embedder + Qdrant config |
| `openmemory/api/default_config.json` | Same as config.json (fallback defaults) |
| `openmemory/api/app/utils/__init__.py` | PRIMARY_MODEL / FALLBACK_MODEL constants |
| `openmemory/api/app/utils/memory.py` | Default config uses Groq, custom extraction prompt, fallback logic, `_RateLimitEscape` mechanism |
| `openmemory/api/app/utils/categorization.py` | Uses Groq client directly for categorization, 70B->gpt-oss fallback |
| `openmemory/api/app/mcp_server.py` | Uses `add_memory_with_fallback()` wrapper for add_memories |
| `openmemory/api/app/routers/memories.py` | Uses `add_memory_with_fallback()` wrapper for create_memory |
| `openmemory/docker-compose.yml` | Removed API_KEY env, bind-mount Qdrant data |
| `.gitignore` | Added `openmemory/qdrant-data/` |

## Custom Fact Extraction Prompt

The default mem0 extraction prompt (`mem0/configs/prompts.py`) has few-shot examples
that teach the model to split related info into separate facts:
```
Input: Hi, my name is John. I am a software engineer.
Output: {"facts": ["Name is John", "Is a Software engineer"]}
```

This causes memory fragmentation — one coherent input becomes multiple context-free
fragments. This is an upstream design issue, not a model problem (even llama-3.3-70b
fragments with the default prompt).

**Fix:** `custom_fact_extraction_prompt` in `get_default_memory_config()` overrides
the default prompt to consolidate related info into single facts. This is the
official mem0 mechanism (`custom_fact_extraction_prompt` config key).

**Do not remove the custom prompt.** It was verified that the 70B model still
fragments without it.

## Model Selection (Groq Free Tier)

`llama-3.3-70b-versatile` was chosen after testing multiple models:

- Highest IFEval score (92.1%) — critical for structured JSON extraction
- Only model with proven reliable JSON + tool calling on Groq
- Production-tier status on Groq (no deprecation risk)
- 30 RPM / 12K TPM / 100K TPD free tier — sufficient for personal use

Models tested and rejected as primary:
- `llama-3.1-8b-instant`: Severe fragmentation, poor instruction following
- `meta-llama/llama-4-scout-17b-16e-instruct`: Broken tool calling on Groq, preview-only status

## Rate Limit Fallback (70B -> gpt-oss-120b)

When `llama-3.3-70b-versatile` hits Groq rate limits, all `memory_client.add()`
calls and categorization automatically fall back to `openai/gpt-oss-120b`. This
prevents silent memory loss.

- **Scope:** Only `add()` operations need fallback (search/delete/get_all are vector store ops)
- **Lazy init:** Fallback client only created on first rate limit hit (~50MB RAM saved normally)
- **Same config:** Both models share Qdrant, Ollama, and custom extraction prompt
- **gpt-oss-120b quality:** 90% MMLU, good extraction (no fragmentation), but weaker
  deduplication than 70B (may ADD where 70B would UPDATE). Good enough as fallback.
- **Shared RPM:** gpt-oss-120b shares RPM quota with llama-3.1-8b-instant on Groq.
  Fallback helps when daily quota (TPD) is the bottleneck, not burst traffic (RPM/TPM).
- **TPM is the real bottleneck:** Each `add()` makes 2 LLM calls (~2-3K tokens),
  plus 1 categorization call. With 12K TPM limit, 3+ concurrent adds can exhaust it.
  **Never call add_memories in parallel** — always sequential.

### How RateLimitError propagates

mem0's `_add_to_vector_store()` has `except Exception` blocks that silently
swallow `RateLimitError`, returning empty results. Two patches in
`_patch_rate_limit_handling()` make fallback work:

1. `max_retries=0` on the OpenAI SDK client — prevents ~34s of silent retries
2. `_RateLimitEscape(BaseException)` wrapper on `generate_response()` — since
   `except Exception` does not catch `BaseException` subclasses, the error
   escapes mem0's internals and reaches `add_memory_with_fallback()`

`_RateLimitEscape` is contained within `memory.py` — it is converted to
`RuntimeError` at the `add_memory_with_fallback()` boundary, so callers
only need their normal `except Exception` handlers.

The categorization client uses `max_retries=0` directly (no monkey-patch
needed since we control that code).

## Environment

- API key: `GROQ_API_KEY` env var (loaded from `openmemory/api/.env`)
- Ollama must be running on the host (accessible from Docker as `host.docker.internal:11434` or `localhost:11434`)
- Qdrant data persisted to `openmemory/qdrant-data/` (bind mount, gitignored)

## Testing Fallback

When testing rate limit fallback by pushing burn memories, **always use
`user_id=test`** (not `pm`) to avoid contaminating real memories. The MCP
`add_memories` tool uses the session's user_id from the URL path, so for
burn tests use the REST API directly:

```bash
curl -s http://localhost:8765/api/v1/memories/ \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "test", "text": "BURN_TEST: ...", "app": "test"}'
```

After testing, clean up with:
```bash
curl -s -X DELETE http://localhost:8765/api/v1/memories/ \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "test", "memory_ids": [...]}'
```

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
