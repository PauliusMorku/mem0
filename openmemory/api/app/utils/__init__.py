PRIMARY_MODEL = "llama-3.3-70b-versatile"
# Shares Groq rate limits with llama-3.1-8b-instant (separate daily quota, shared RPM)
FALLBACK_MODEL = "openai/gpt-oss-120b"

# Maximum seconds to wait for LLM-based memory operations (extraction + dedup).
# Prevents MCP/API clients from hanging when the LLM provider is rate-limited.
MEMORY_ADD_TIMEOUT = 30.0
