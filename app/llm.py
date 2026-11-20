"""Un único lugar para crear el modelo de chat. Cambiar de proveedor = tocar este archivo.

Los embeddings NO pasan por aquí (siguen en Ollama, ver vector_db.py).
"""

from app import config


def get_llm(temperature: float = 0):
    if config.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=config.LLM_MODEL,
            temperature=temperature,
            num_ctx=config.NUM_CTX,
            base_url=config.OLLAMA_BASE_URL,
        )

    if config.LLM_PROVIDER == "gemini":
        if not config.GOOGLE_API_KEY:
            raise RuntimeError(
                "Falta GOOGLE_API_KEY. Crea una clave gratuita en Google AI Studio "
                "y ponla en tu archivo .env (ver .env.example)."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=config.GEMINI_MODEL,
            temperature=temperature,
            google_api_key=config.GOOGLE_API_KEY,
            max_retries=config.LLM_MAX_RETRIES,   # backoff automático ante 429 (límite gratuito)
            timeout=config.LLM_TIMEOUT,
        )

    raise ValueError(f"LLM_PROVIDER desconocido: {config.LLM_PROVIDER!r} (usa 'gemini' u 'ollama')")
