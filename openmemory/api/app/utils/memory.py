"""
Memory client utilities for OpenMemory.

This module provides functionality to initialize and manage the Mem0 memory client
with automatic configuration management and Docker environment support.

Docker Ollama Configuration:
When running inside a Docker container and using Ollama as the LLM or embedder provider,
the system automatically detects the Docker environment and adjusts localhost URLs
to properly reach the host machine where Ollama is running.

Supported Docker host resolution (in order of preference):
1. OLLAMA_HOST environment variable (if set)
2. host.docker.internal (Docker Desktop for Mac/Windows)
3. Docker bridge gateway IP (typically 172.17.0.1 on Linux)
4. Fallback to 172.17.0.1

Example configuration that will be automatically adjusted:
{
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "llama3.1:latest",
            "ollama_base_url": "http://localhost:11434"  # Auto-adjusted in Docker
        }
    }
}
"""

import hashlib
import json
import logging
import os
import socket

from app.database import SessionLocal
from app.models import Config as ConfigModel

from app.utils import FALLBACK_MODEL, PRIMARY_MODEL
from mem0 import Memory
from openai import RateLimitError

_memory_client = None
_config_hash = None
_fallback_client = None
_fallback_config_hash = None


def _get_config_hash(config_dict):
    """Generate a hash of the config to detect changes."""
    config_str = json.dumps(config_dict, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()


def _get_docker_host_url():
    """
    Determine the appropriate host URL to reach host machine from inside Docker container.
    Returns the best available option for reaching the host from inside a container.
    """
    # Check for custom environment variable first
    custom_host = os.environ.get('OLLAMA_HOST')
    if custom_host:
        print(f"Using custom Ollama host from OLLAMA_HOST: {custom_host}")
        return custom_host.replace('http://', '').replace('https://', '').split(':')[0]
    
    # Check if we're running inside Docker
    if not os.path.exists('/.dockerenv'):
        # Not in Docker, return localhost as-is
        return "localhost"
    
    print("Detected Docker environment, adjusting host URL for Ollama...")
    
    # Try different host resolution strategies
    host_candidates = []
    
    # 1. host.docker.internal (works on Docker Desktop for Mac/Windows)
    try:
        socket.gethostbyname('host.docker.internal')
        host_candidates.append('host.docker.internal')
        print("Found host.docker.internal")
    except socket.gaierror:
        pass
    
    # 2. Docker bridge gateway (typically 172.17.0.1 on Linux)
    try:
        with open('/proc/net/route', 'r') as f:
            for line in f:
                fields = line.strip().split()
                if fields[1] == '00000000':  # Default route
                    gateway_hex = fields[2]
                    gateway_ip = socket.inet_ntoa(bytes.fromhex(gateway_hex)[::-1])
                    host_candidates.append(gateway_ip)
                    print(f"Found Docker gateway: {gateway_ip}")
                    break
    except (FileNotFoundError, IndexError, ValueError):
        pass
    
    # 3. Fallback to common Docker bridge IP
    if not host_candidates:
        host_candidates.append('172.17.0.1')
        print("Using fallback Docker bridge IP: 172.17.0.1")
    
    # Return the first available candidate
    return host_candidates[0]


def _fix_ollama_urls(config_section):
    """
    Fix Ollama URLs for Docker environment.
    Replaces localhost URLs with appropriate Docker host URLs.
    Sets default ollama_base_url if not provided.
    """
    if not config_section or "config" not in config_section:
        return config_section
    
    ollama_config = config_section["config"]
    
    # Set default ollama_base_url if not provided
    if "ollama_base_url" not in ollama_config:
        ollama_config["ollama_base_url"] = "http://host.docker.internal:11434"
    else:
        # Check for ollama_base_url and fix if it's localhost
        url = ollama_config["ollama_base_url"]
        if "localhost" in url or "127.0.0.1" in url:
            docker_host = _get_docker_host_url()
            if docker_host != "localhost":
                new_url = url.replace("localhost", docker_host).replace("127.0.0.1", docker_host)
                ollama_config["ollama_base_url"] = new_url
                print(f"Adjusted Ollama URL from {url} to {new_url}")
    
    return config_section


def reset_memory_client():
    """Reset the global memory client to force reinitialization with new config."""
    global _memory_client, _config_hash, _fallback_client, _fallback_config_hash
    _memory_client = None
    _config_hash = None
    _fallback_client = None
    _fallback_config_hash = None


def get_default_memory_config(model=None):
    """Get default memory client configuration with sensible defaults."""
    # Detect vector store based on environment variables
    vector_store_config = {
        "collection_name": "openmemory",
        "host": "mem0_store",
    }
    
    # Check for different vector store configurations based on environment variables
    if os.environ.get('CHROMA_HOST') and os.environ.get('CHROMA_PORT'):
        vector_store_provider = "chroma"
        vector_store_config.update({
            "host": os.environ.get('CHROMA_HOST'),
            "port": int(os.environ.get('CHROMA_PORT'))
        })
    elif os.environ.get('QDRANT_HOST') and os.environ.get('QDRANT_PORT'):
        vector_store_provider = "qdrant"
        vector_store_config.update({
            "host": os.environ.get('QDRANT_HOST'),
            "port": int(os.environ.get('QDRANT_PORT'))
        })
    elif os.environ.get('WEAVIATE_CLUSTER_URL') or (os.environ.get('WEAVIATE_HOST') and os.environ.get('WEAVIATE_PORT')):
        vector_store_provider = "weaviate"
        # Prefer an explicit cluster URL if provided; otherwise build from host/port
        cluster_url = os.environ.get('WEAVIATE_CLUSTER_URL')
        if not cluster_url:
            weaviate_host = os.environ.get('WEAVIATE_HOST')
            weaviate_port = int(os.environ.get('WEAVIATE_PORT'))
            cluster_url = f"http://{weaviate_host}:{weaviate_port}"
        vector_store_config = {
            "collection_name": "openmemory",
            "cluster_url": cluster_url
        }
    elif os.environ.get('REDIS_URL'):
        vector_store_provider = "redis"
        vector_store_config = {
            "collection_name": "openmemory",
            "redis_url": os.environ.get('REDIS_URL')
        }
    elif os.environ.get('PG_HOST') and os.environ.get('PG_PORT'):
        vector_store_provider = "pgvector"
        vector_store_config.update({
            "host": os.environ.get('PG_HOST'),
            "port": int(os.environ.get('PG_PORT')),
            "dbname": os.environ.get('PG_DB', 'mem0'),
            "user": os.environ.get('PG_USER', 'mem0'),
            "password": os.environ.get('PG_PASSWORD', 'mem0')
        })
    elif os.environ.get('MILVUS_HOST') and os.environ.get('MILVUS_PORT'):
        vector_store_provider = "milvus"
        # Construct the full URL as expected by MilvusDBConfig
        milvus_host = os.environ.get('MILVUS_HOST')
        milvus_port = int(os.environ.get('MILVUS_PORT'))
        milvus_url = f"http://{milvus_host}:{milvus_port}"
        
        vector_store_config = {
            "collection_name": "openmemory",
            "url": milvus_url,
            "token": os.environ.get('MILVUS_TOKEN', ''),  # Always include, empty string for local setup
            "db_name": os.environ.get('MILVUS_DB_NAME', ''),
            "embedding_model_dims": 1536,
            "metric_type": "COSINE"  # Using COSINE for better semantic similarity
        }
    elif os.environ.get('ELASTICSEARCH_HOST') and os.environ.get('ELASTICSEARCH_PORT'):
        vector_store_provider = "elasticsearch"
        # Construct the full URL with scheme since Elasticsearch client expects it
        elasticsearch_host = os.environ.get('ELASTICSEARCH_HOST')
        elasticsearch_port = int(os.environ.get('ELASTICSEARCH_PORT'))
        # Use http:// scheme since we're not using SSL
        full_host = f"http://{elasticsearch_host}"
        
        vector_store_config.update({
            "host": full_host,
            "port": elasticsearch_port,
            "user": os.environ.get('ELASTICSEARCH_USER', 'elastic'),
            "password": os.environ.get('ELASTICSEARCH_PASSWORD', 'changeme'),
            "verify_certs": False,
            "use_ssl": False,
            "embedding_model_dims": 1536
        })
    elif os.environ.get('OPENSEARCH_HOST') and os.environ.get('OPENSEARCH_PORT'):
        vector_store_provider = "opensearch"
        vector_store_config.update({
            "host": os.environ.get('OPENSEARCH_HOST'),
            "port": int(os.environ.get('OPENSEARCH_PORT'))
        })
    elif os.environ.get('FAISS_PATH'):
        vector_store_provider = "faiss"
        vector_store_config = {
            "collection_name": "openmemory",
            "path": os.environ.get('FAISS_PATH'),
            "embedding_model_dims": 1536,
            "distance_strategy": "cosine"
        }
    else:
        # Default fallback to Qdrant
        vector_store_provider = "qdrant"
        vector_store_config.update({
            "port": 6333,
        })
    
    print(f"Auto-detected vector store: {vector_store_provider} with config: {vector_store_config}")
    
    return {
        "vector_store": {
            "provider": vector_store_provider,
            "config": vector_store_config
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": model or PRIMARY_MODEL,
                "temperature": 0.1,
                "max_tokens": 2000,
                "api_key": "env:GROQ_API_KEY",
                "openai_base_url": "https://api.groq.com/openai/v1"
            }
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": "nomic-embed-text",
                "embedding_dims": 768,
                "ollama_base_url": "http://localhost:11434"
            }
        },
        "version": "v1.1",
        "custom_fact_extraction_prompt": (
            "Extract facts from user messages only. Return JSON: {\"facts\": []} — empty if nothing relevant.\n"
            "Rules:\n"
            "- Extract facts FAITHFULLY — do not rephrase or compress. Preserve the user's wording.\n"
            "- Every fact MUST include full context (which project, system, person) so it is useful on its own.\n"
            "  BAD: \"Has 8GB RAM\" GOOD: \"Raspberry Pi at parents' place has 8GB RAM\"\n"
            "- Splitting into multiple facts is fine — but EVERY fact must carry the full context.\n"
            "- Skip greetings, filler, generic statements.\n\n"
            "Examples:\n"
            "Input: The staging server on AWS eu-west-1 runs PostgreSQL 16 and uses Redis 7 for caching.\n"
            "Output: {\"facts\": [\"Staging server (AWS eu-west-1) runs PostgreSQL 16\", \"Staging server (AWS eu-west-1) uses Redis 7 for caching\"]}\n\n"
            "Input: SiteSeeker was migrated from SQLite to PostgreSQL with PgBouncer. Had enum issues but resolved them.\n"
            "Output: {\"facts\": [\"SiteSeeker: migrated from SQLite to PostgreSQL with PgBouncer (enum issues resolved)\"]}\n"
        ),
    }


def _parse_environment_variables(config_dict):
    """
    Parse environment variables in config values.
    Converts 'env:VARIABLE_NAME' to actual environment variable values.
    """
    if isinstance(config_dict, dict):
        parsed_config = {}
        for key, value in config_dict.items():
            if isinstance(value, str) and value.startswith("env:"):
                env_var = value.split(":", 1)[1]
                env_value = os.environ.get(env_var)
                if env_value:
                    parsed_config[key] = env_value
                    print(f"Loaded {env_var} from environment for {key}")
                else:
                    print(f"Warning: Environment variable {env_var} not found, keeping original value")
                    parsed_config[key] = value
            elif isinstance(value, dict):
                parsed_config[key] = _parse_environment_variables(value)
            else:
                parsed_config[key] = value
        return parsed_config
    return config_dict


def _build_resolved_config(model=None, custom_instructions=None):
    """
    Build a fully resolved memory config: defaults + DB overrides + env var parsing.

    Used by both the primary and fallback client paths to ensure consistent
    configuration (vector store, embedder, custom instructions, etc.).
    """
    config = get_default_memory_config(model=model)

    db_custom_instructions = None

    # Load configuration overrides from database
    try:
        db = SessionLocal()
        db_config = db.query(ConfigModel).filter(ConfigModel.key == "main").first()

        if db_config:
            json_config = db_config.value

            # Extract custom instructions from openmemory settings
            if "openmemory" in json_config and "custom_instructions" in json_config["openmemory"]:
                db_custom_instructions = json_config["openmemory"]["custom_instructions"]

            # Override defaults with configurations from the database
            if "mem0" in json_config:
                mem0_config = json_config["mem0"]

                # Update LLM configuration if available
                if "llm" in mem0_config and mem0_config["llm"] is not None:
                    config["llm"] = mem0_config["llm"]

                    # Fix Ollama URLs for Docker if needed
                    if config["llm"].get("provider") == "ollama":
                        config["llm"] = _fix_ollama_urls(config["llm"])

                # Update Embedder configuration if available
                if "embedder" in mem0_config and mem0_config["embedder"] is not None:
                    config["embedder"] = mem0_config["embedder"]

                    # Fix Ollama URLs for Docker if needed
                    if config["embedder"].get("provider") == "ollama":
                        config["embedder"] = _fix_ollama_urls(config["embedder"])

                if "vector_store" in mem0_config and mem0_config["vector_store"] is not None:
                    config["vector_store"] = mem0_config["vector_store"]
        else:
            print("No configuration found in database, using defaults")

        db.close()

    except Exception as e:
        print(f"Warning: Error loading configuration from database: {e}")
        print("Using default configuration")

    # Use caller-provided custom_instructions first, then DB value
    instructions_to_use = custom_instructions or db_custom_instructions
    if instructions_to_use:
        config["custom_fact_extraction_prompt"] = instructions_to_use

    # Parse environment variables (e.g. "env:GROQ_API_KEY" -> actual value)
    config = _parse_environment_variables(config)

    return config


class _RateLimitEscape(BaseException):
    """
    Wraps RateLimitError to escape mem0's `except Exception` blocks.

    mem0's _add_to_vector_store() catches Exception at multiple points,
    silently swallowing RateLimitError and returning empty results. Since
    BaseException is not caught by `except Exception`, wrapping the error
    lets it propagate out to our add_memory_with_fallback() wrapper.
    """
    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


def _patch_rate_limit_handling(client):
    """
    Patch a Memory client's LLM to bypass both SDK retries and mem0's
    `except Exception` swallowing of RateLimitError.

    Two patches applied:
    1. max_retries=0 on the OpenAI client — prevents SDK from silently
       retrying 429s for ~34s
    2. Wrap generate_response() — converts RateLimitError into a
       BaseException subclass (_RateLimitEscape) so it escapes mem0's
       `except Exception` blocks
    """
    try:
        client.llm.client = client.llm.client.with_options(max_retries=0)
    except AttributeError:
        logging.warning("Could not patch mem0 LLM client max_retries — attribute path changed")

    original_generate = client.llm.generate_response

    def _generate_with_escape(*args, **kwargs):
        try:
            return original_generate(*args, **kwargs)
        except RateLimitError as e:
            raise _RateLimitEscape(e) from e

    client.llm.generate_response = _generate_with_escape


def get_memory_client(custom_instructions: str = None):
    """
    Get or initialize the Mem0 client.

    Args:
        custom_instructions: Optional instructions for the memory project.

    Returns:
        Initialized Mem0 client instance or None if initialization fails.

    Raises:
        Exception: If required API keys are not set or critical configuration is missing.
    """
    global _memory_client, _config_hash

    try:
        config = _build_resolved_config(custom_instructions=custom_instructions)

        # Check if config has changed by comparing hashes
        current_config_hash = _get_config_hash(config)

        # Only reinitialize if config changed or client doesn't exist
        if _memory_client is None or _config_hash != current_config_hash:
            print(f"Initializing memory client with config hash: {current_config_hash}")
            try:
                _memory_client = Memory.from_config(config_dict=config)
                _patch_rate_limit_handling(_memory_client)
                _config_hash = current_config_hash
                print("Memory client initialized successfully")
            except Exception as init_error:
                print(f"Warning: Failed to initialize memory client: {init_error}")
                print("Server will continue running with limited memory functionality")
                _memory_client = None
                _config_hash = None
                return None

        return _memory_client

    except Exception as e:
        print(f"Warning: Exception occurred while initializing memory client: {e}")
        print("Server will continue running with limited memory functionality")
        return None


def get_fallback_client():
    """
    Get or initialize a fallback Mem0 client using the smaller model.
    Lazy-initialized — only created on first rate limit hit.
    """
    global _fallback_client, _fallback_config_hash

    try:
        config = _build_resolved_config(model=FALLBACK_MODEL)

        current_hash = _get_config_hash(config)

        if _fallback_client is None or _fallback_config_hash != current_hash:
            logging.info(f"Initializing fallback memory client with model: {FALLBACK_MODEL}")
            _fallback_client = Memory.from_config(config_dict=config)
            _patch_rate_limit_handling(_fallback_client)
            _fallback_config_hash = current_hash
            logging.info("Fallback memory client initialized successfully")

        return _fallback_client

    except Exception as e:
        logging.error(f"Failed to initialize fallback memory client: {e}")
        return None


def add_memory_with_fallback(memory_client, text, **kwargs):
    """
    Add memory using the primary client, falling back to the smaller model
    if the primary model is rate-limited.

    Raises RuntimeError (not _RateLimitEscape) so callers' existing
    ``except Exception`` blocks handle it naturally.
    """
    try:
        return memory_client.add(text, **kwargs)
    except _RateLimitEscape:
        logging.warning(
            f"Primary model ({PRIMARY_MODEL}) rate limited. "
            f"Falling back to {FALLBACK_MODEL}..."
        )
        fallback = get_fallback_client()
        if fallback is None:
            raise RuntimeError(
                f"Primary model ({PRIMARY_MODEL}) rate limited "
                f"and fallback client unavailable"
            )
        try:
            return fallback.add(text, **kwargs)
        except _RateLimitEscape:
            logging.error(
                f"Both models rate limited for memory add "
                f"({PRIMARY_MODEL} and {FALLBACK_MODEL}). Memory not saved."
            )
            raise RuntimeError(
                f"Both models rate limited "
                f"({PRIMARY_MODEL} and {FALLBACK_MODEL}). Memory not saved."
            )


def get_default_user_id():
    return "default_user"
