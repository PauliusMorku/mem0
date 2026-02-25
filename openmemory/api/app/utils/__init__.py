PRIMARY_MODEL = "llama-3.3-70b-versatile"

# Maximum seconds to wait for LLM-based memory operations (extraction + dedup).
# Prevents MCP/API clients from hanging when the LLM provider is rate-limited.
MEMORY_ADD_TIMEOUT = 30.0
