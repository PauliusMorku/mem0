import json
import logging
import os
from typing import List

from app.utils.prompts import MEMORY_CATEGORIZATION_PROMPT
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

_groq_api_key = os.environ.get("GROQ_API_KEY")
if not _groq_api_key:
    raise RuntimeError("GROQ_API_KEY environment variable is required but not set")

_groq_client = OpenAI(
    api_key=_groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)


class MemoryCategories(BaseModel):
    categories: List[str]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
def get_categories_for_memory(memory: str) -> List[str]:
    response = None
    try:
        messages = [
            {"role": "system", "content": MEMORY_CATEGORIZATION_PROMPT},
            {"role": "user", "content": f"{memory}\n\nRespond with JSON only: {{\"categories\": [...]}}"},
        ]

        response = _groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        parsed = MemoryCategories(**result)
        return [cat.strip().lower() for cat in parsed.categories]

    except Exception as e:
        logging.error(f"[ERROR] Failed to get categories: {e}")
        try:
            if response:
                logging.debug(f"[DEBUG] Raw response: {response.choices[0].message.content}")
        except Exception as debug_e:
            logging.debug(f"[DEBUG] Could not extract raw response: {debug_e}")
        raise
