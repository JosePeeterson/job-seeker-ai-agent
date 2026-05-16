"""
LLM abstraction layer.

Uses OpenAI when OPENAI_API_KEY is available (Streamlit Cloud / any cloud env).
Falls back to Ollama for local development.

Model name conventions:
  OpenAI models  — names starting with "gpt-", "o1-", "o3-"
  Ollama models  — everything else (e.g. "llama3.1:8b", "mistral:7b")

API key lookup order:
  1. OPENAI_API_KEY environment variable
  2. st.secrets["OPENAI_API_KEY"] (Streamlit Cloud secrets)
"""

import os


# --------------------------------------------------------------------------
# Public default models
# --------------------------------------------------------------------------
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


def _is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "o1-", "o3-"))


def _get_openai_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get("OPENAI_API_KEY")
    except Exception:
        return None


def chat(prompt: str, model: str, temperature: float = 0.0) -> str:
    """
    Send a prompt to an LLM and return the response text.

    Routing logic:
    - Model name looks like an OpenAI model  → use OpenAI (key required)
    - OPENAI_API_KEY is set + model is Ollama → use OpenAI with DEFAULT_OPENAI_MODEL
    - No key available                        → use local Ollama
    """
    api_key = _get_openai_key()

    if _is_openai_model(model):
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not found. Set it as an environment variable "
                "or add it to Streamlit secrets."
            )
        return _chat_openai(prompt, model, temperature, api_key)

    if api_key:
        # Cloud env: no Ollama available — map to equivalent OpenAI model
        return _chat_openai(prompt, DEFAULT_OPENAI_MODEL, temperature, api_key)

    # Local dev: use Ollama
    return _chat_ollama(prompt, model, temperature)


def _chat_openai(prompt: str, model: str, temperature: float, api_key: str) -> str:
    from openai import OpenAI  # lazy import — not installed on local dev
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


def _chat_ollama(prompt: str, model: str, temperature: float) -> str:
    import ollama  # lazy import — not installed on Streamlit Cloud
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": temperature},
    )
    return response["message"]["content"].strip()
