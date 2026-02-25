# OpenMemory on Raspberry Pi 5 — Local Modifications

This is a fork of [mem0ai/mem0](https://github.com/mem0ai/mem0) with changes to run OpenMemory on Raspberry Pi 5 using Groq (LLM) + Ollama (embeddings) instead of OpenAI.

Fork: `git@github.com:PauliusMorku/mem0.git`
Branch: `pi5/groq-ollama-setup`

## Architecture

- **LLM:** `llama-3.3-70b-versatile` via Groq API (primary), `openai/gpt-oss-120b` (fallback on rate limit)
- **Embeddings:** `nomic-embed-text` via local Ollama (port 11434)
- **Vector store:** Qdrant (Docker container `mem0_store`, port 6333)
- **API:** OpenMemory MCP server (Docker container, port 8765)

## Key Files Modified (from upstream)

| File | What changed |
|---|---|
| `openmemory/api/config.json` | Groq LLM + Ollama embedder + Qdrant config |
| `openmemory/api/default_config.json` | Same as config.json (fallback defaults) |
| `openmemory/api/app/utils/memory.py` | Default config uses Groq, custom extraction prompt, 70B->gpt-oss fallback |
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
- 100K TPD / 1K RPD free tier — sufficient for personal use

Models tested and rejected as primary:
- `llama-3.1-8b-instant`: Severe fragmentation, poor instruction following
- `meta-llama/llama-4-scout-17b-16e-instruct`: Broken tool calling on Groq, preview-only status

## Rate Limit Fallback (70B -> gpt-oss-120b)

When `llama-3.3-70b-versatile` hits Groq rate limits (100K TPD / 1K RPD), all
`memory_client.add()` calls and categorization automatically fall back to
`openai/gpt-oss-120b` (separate rate limit, 200K TPD / 1K RPD). This prevents
silent memory loss.

- **Scope:** Only `add()` operations need fallback (search/delete/get_all are vector store ops)
- **Lazy init:** Fallback client only created on first rate limit hit (~50MB RAM saved normally)
- **Same config:** Both models share Qdrant, Ollama, and custom extraction prompt
- **gpt-oss-120b:** 90% MMLU, supports JSON mode + JSON schema, production status on Groq
- **SDK retries disabled:** mem0's internal OpenAI client and the categorization client both use `max_retries=0`. Without this, the SDK silently retries 429s for ~34s before mem0 swallows the error — the memory is lost either way. With `max_retries=0`, `RateLimitError` propagates immediately to our fallback wrappers.
- **If both rate-limited:** Logged distinctly ("Both models rate limited"), then error propagates to existing exception handlers

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
