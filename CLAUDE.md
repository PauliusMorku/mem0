# OpenMemory on Raspberry Pi 5 — Local Modifications

This is a fork of [mem0ai/mem0](https://github.com/mem0ai/mem0) with changes to run OpenMemory on Raspberry Pi 5 using Groq (LLM) + Ollama (embeddings) instead of OpenAI.

Fork: `git@github.com:PauliusMorku/mem0.git`
Branch: `pi5/groq-ollama-setup`

## Architecture

- **LLM:** `llama-3.3-70b-versatile` via Groq API (OpenAI-compatible)
- **Embeddings:** `nomic-embed-text` via local Ollama (port 11434)
- **Vector store:** Qdrant (Docker container `mem0_store`, port 6333)
- **API:** OpenMemory MCP server (Docker container, port 8765)

## Key Files Modified (from upstream)

| File | What changed |
|---|---|
| `openmemory/api/config.json` | Groq LLM + Ollama embedder + Qdrant config |
| `openmemory/api/default_config.json` | Same as config.json (fallback defaults) |
| `openmemory/api/app/utils/memory.py` | Default config uses Groq, custom extraction prompt |
| `openmemory/api/app/utils/categorization.py` | Uses Groq client directly for categorization |
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

Models tested and rejected:
- `llama-3.1-8b-instant`: Severe fragmentation, poor instruction following
- `meta-llama/llama-4-scout-17b-16e-instruct`: Broken tool calling on Groq, preview-only status

## Environment

- API key: `GROQ_API_KEY` env var (loaded from `openmemory/api/.env`)
- Ollama must be running on the host (accessible from Docker as `host.docker.internal:11434` or `localhost:11434`)
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
