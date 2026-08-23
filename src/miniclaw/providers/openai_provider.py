from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


def create_model() -> ChatOpenAI:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required; configure it in .env or the environment")
    if not model:
        raise RuntimeError("OPENAI_MODEL is required; configure it in .env or the environment")
    kwargs: dict[str, object] = {"model": model, "api_key": api_key}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if temperature := os.getenv("OPENAI_TEMPERATURE"):
        kwargs["temperature"] = float(temperature)
    return ChatOpenAI(**kwargs)
