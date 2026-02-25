import json
import logging
import os
from typing import List

from app.utils import PRIMARY_MODEL
from app.utils.prompts import MEMORY_CATEGORIZATION_PROMPT
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

_groq_api_key = os.environ.get("GROQ_API_KEY")
if not _groq_api_key:
    raise RuntimeError("GROQ_API_KEY environment variable is required but not set")

_groq_client = OpenAI(
    api_key=_groq_api_key,
    base_url="https://api.groq.com/openai/v1",
    max_retries=0,
)


class MemoryCategories(BaseModel):
    categories: List[str]


def get_categories_for_memory(memory: str) -> List[str]:
    try:
        messages = [
            {"role": "system", "content": MEMORY_CATEGORIZATION_PROMPT},
            {"role": "user", "content": f"{memory}\n\nRespond with JSON only: {{\"categories\": [...]}}"},
        ]

        response = _groq_client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        parsed = MemoryCategories(**result)
        return [cat.strip().lower() for cat in parsed.categories]

    except Exception as e:
        logging.error(f"[ERROR] Failed to get categories: {e}")
        raise
