"""Fixtures de prueba: grafo con agentes FALSOS (sin LLM, sin Ollama, sin API key).

Los tests de la API necesitan un Redis cualquiera (no hace falta redis-stack: los
checkpoints van en memoria). Si no hay Redis en REDIS_URL, esos tests se saltan.
"""

import os
import time

os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
os.environ.setdefault("PHOENIX_ENABLED", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")   # db 15: no pisa datos reales

import pytest

from app import graph as graph_mod
from app import hitl


def instalar_agentes_falsos(monkeypatch, *, falla: bool = False, demora: float = 0.0):
    """Reemplaza supervisor/investigador/analista/sintetizador por nodos mínimos."""
    def supervisor(llm):
        return lambda s: {"next_agent": "synthesizer", "step_count": 1,
                          "contributions": [{"agent": "supervisor", "step": 1, "summary": "-> synthesizer"}]}

    def sintetizador(llm):
        def nodo(s):
            time.sleep(demora)               # nodo SÍNCRONO y lento, como los del M6
            if falla:
                raise RuntimeError("fallo simulado del LLM")
            return {"final_answer": f"Respuesta de prueba a: {s['messages'][0].content}",
                    "task_completed": True,
                    "contributions": [{"agent": "synthesizer", "step": 1, "summary": "ok"}]}
        return nodo

    vacio = lambda llm: (lambda s: {})
    monkeypatch.setattr(graph_mod, "get_llm", lambda t=0: object())
    monkeypatch.setattr(graph_mod, "make_supervisor", supervisor)
    monkeypatch.setattr(graph_mod, "make_synthesizer_node", sintetizador)
    monkeypatch.setattr(graph_mod, "make_research_node", vacio)
    monkeypatch.setattr(graph_mod, "make_analyst_node", vacio)


@pytest.fixture
def bandeja(monkeypatch):
    """Captura los envíos al consejo en una lista (no toca Redis)."""
    enviados = []
    def falso(payload):
        enviados.append(payload)
        return {"outbox_id": "x", "accion": hitl.ACCION, "destinatario": hitl.DESTINATARIO, "enviado_en": "t"}
    monkeypatch.setattr(hitl, "enviar_al_consejo", falso)
    return enviados


@pytest.fixture
def redis_o_skip():
    import redis
    try:
        redis.Redis.from_url(os.environ["REDIS_URL"]).ping()
    except Exception:
        pytest.skip("No hay Redis en REDIS_URL")
