"""API REST asíncrona del sistema multi-agente (FastAPI).

  POST /tasks               -> encola la tarea y devuelve job_id al instante (202)
  GET  /tasks/{id}          -> estado del job (PENDING/RUNNING/AWAITING_APPROVAL/DONE/FAILED/REJECTED)
  POST /tasks/{id}/approve  -> decisión humana para tareas críticas (ver hitl.py)
  GET  /health              -> Redis, backend de checkpoints y modelo en uso

Arranque:  uvicorn app.main:app --port 8000
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app import config, hitl
from app.graph import build_graph, open_checkpointer
from app.jobs import JobStore, valid_job_id
from app.observability import init_observability
from app.worker import JobRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_observability()                                  # Phoenix: trazas de todo lo que sigue
    store = JobStore()
    async with open_checkpointer() as checkpointer:       # Redis (o memoria en desarrollo)
        graph = build_graph(checkpointer=checkpointer)
        runner = JobRunner(graph, store)
        huerfanos = await store.fail_orphans()
        if huerfanos:
            log.warning("%d job(s) de una ejecución anterior marcados como FAILED", huerfanos)
        app.state.store, app.state.runner = store, runner
        log.info("API lista (checkpoints=%s, llm=%s)", config.CHECKPOINT_BACKEND, config.LLM_PROVIDER)
        try:
            yield
        finally:
            await runner.shutdown()
            await store.close()


app = FastAPI(
    title="Huerta Viva · API de agentes",
    description="Sistema multi-agente (supervisor + investigador + analista + sintetizador) "
                "con ejecución asíncrona, estado en Redis, trazas en Phoenix y aprobación humana.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(hitl.router)


class TaskIn(BaseModel):
    question: str = Field(min_length=5, max_length=2000, description="Pregunta o tarea para el sistema")
    requires_approval: Optional[bool] = Field(
        None, description="Fuerza (true) o evita (false) la pausa de aprobación humana. "
                          "Si se omite, la decide hitl.es_critica según la pregunta.")


@app.post("/tasks", status_code=202, tags=["Tareas"])
async def create_task(body: TaskIn, request: Request):
    """Encola la tarea y responde ya, sin esperar al grafo (que tarda segundos)."""
    critica = hitl.es_critica(body.question, body.requires_approval)
    job = await request.app.state.runner.submit(body.question, critica)
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "requires_approval": critica,
        "status_url": f"/tasks/{job['job_id']}",
    }


@app.get("/tasks/{job_id}", tags=["Tareas"])
async def get_task(job_id: str, request: Request):
    job = await request.app.state.store.get(job_id) if valid_job_id(job_id) else None
    if job is None:
        raise HTTPException(404, "Tarea no encontrada (o ya expiró)")
    return job


@app.get("/health", tags=["Sistema"])
async def health(request: Request):
    redis_ok = await request.app.state.store.ping()
    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": redis_ok,
        "checkpoint_backend": config.CHECKPOINT_BACKEND,
        "llm": f"{config.LLM_PROVIDER}:{config.GEMINI_MODEL if config.LLM_PROVIDER == 'gemini' else config.LLM_MODEL}",
    }
