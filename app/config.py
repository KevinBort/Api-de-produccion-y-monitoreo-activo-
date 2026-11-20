"""Configuración central. Todo lo que cambia entre máquinas vive en el .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

# override=False: las variables reales del entorno (Docker, CI) ganan sobre el .env
load_dotenv(override=False)

RAIZ = Path(__file__).resolve().parent.parent          # raíz del repo (app/ vive dentro)
DIR_DATOS = RAIZ / "data"
DIR_VECTORES = RAIZ / "vectorstore"

# Colecciones de ChromaDB (las dos viven en la misma carpeta vectorstore/)
COLECCION_DOCS = "rag_docs"                 # reglamento y guías
COLECCION_RESENAS = "resenas_huerta_viva"   # reseñas de socios (sentimiento)

# --------------------------------------------------------------------- LLM de chat
# "gemini" (default, capa gratuita de Google AI Studio) | "ollama" (local, como en el M6)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
# Copiá el id exacto desde Google AI Studio: los modelos gratuitos cambian con el tiempo.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "6"))   # reintentos con backoff ante 429
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))          # segundos por llamada

# Modelo local (solo si LLM_PROVIDER=ollama)
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:4b-instruct")
NUM_CTX = int(os.getenv("NUM_CTX", "4096"))

# --------------------------------------------------------------- Embeddings (Ollama)
# El vectorstore se indexó con bge-m3: NO cambiar sin reingestar.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ----------------------------------------------------------------- Control de flujo
MAX_STEPS = int(os.getenv("MAX_STEPS", "8"))   # pasos totales del supervisor
MAX_REFINAMIENTOS = 1                          # veces que un especialista puede repetir

# Recuperación
K_DOCS = int(os.getenv("K_DOCS", "4"))          # fragmentos de documentos por búsqueda
K_RESENAS = int(os.getenv("K_RESENAS", "12"))   # reseñas por búsqueda (hay 24 en total)

# ------------------------------------------------------------------------- API / Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# "redis" (producción, requiere redis-stack) | "memory" (desarrollo/tests, no persiste)
CHECKPOINT_BACKEND = os.getenv("CHECKPOINT_BACKEND", "redis").lower()
CHECKPOINT_TTL_MINUTES = int(os.getenv("CHECKPOINT_TTL_MINUTES", "1440"))  # 24 h
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", str(24 * 3600)))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "5"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "40"))

# ------------------------------------------------------------------------ Observabilidad
PHOENIX_ENABLED = os.getenv("PHOENIX_ENABLED", "true").lower() in ("1", "true", "yes")
PHOENIX_COLLECTOR_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
PHOENIX_PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME", "huerta-viva-agentes")
