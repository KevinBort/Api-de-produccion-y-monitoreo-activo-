"""Worker: corre el grafo en segundo plano y mantiene el estado del job en Redis.

Es un worker "simple" (tareas asyncio dentro del proceso de FastAPI), suficiente para
esta etapa. Puntos que importan:

  * No bloquea el event loop: se usa `graph.ainvoke`; los nodos del M6 son síncronos y
    LangGraph los ejecuta en un thread pool, así que los endpoints siguen respondiendo.
  * Cualquier excepción termina en FAILED con el mensaje de error (nunca queda un job
    colgado en RUNNING).
  * Un semáforo limita cuántos jobs corren a la vez (MAX_CONCURRENT_JOBS); el resto
    espera en PENDING. Protege el rate limit del LLM.
  * Se guarda una referencia a cada tarea: asyncio solo guarda referencias DÉBILES y un
    `create_task` sin referencia puede ser recolectado a mitad de ejecución.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from langgraph.types import Command

from app import config
from app.jobs import (AWAITING_APPROVAL, DONE, FAILED, REJECTED, RUNNING, JobStore, ahora)
from app.observability import job_span
from app.state import initial_state

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, graph, store: JobStore, max_concurrent: int = config.MAX_CONCURRENT_JOBS):
        self.graph = graph
        self.store = store
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ API pública
    async def submit(self, question: str, requires_approval: bool) -> dict:
        """Crea el job (PENDING), lo encola y devuelve de inmediato (no espera al grafo)."""
        job = await self.store.create(question, requires_approval)
        self._spawn(self._run(job["job_id"], initial_state(question, requires_approval),
                              fase="run", pregunta=question))
        return job

    async def resume(self, job_id: str, approved: bool, comment: str) -> bool:
        """Reanuda un job pausado. False si no estaba esperando aprobación (o ya la
        recibió otra petición: la transición es atómica)."""
        if not await self.store.transition(job_id, AWAITING_APPROVAL, RUNNING):
            return False
        await self.store.update(job_id, pending_approval=None)
        self._spawn(self._run(job_id, Command(resume={"approved": approved, "comment": comment}),
                              fase="resume", reanudando=True))
        return True

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # -------------------------------------------------------------------- interno
    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: str, entrada_grafo: Any, fase: str, pregunta: str = "",
                   reanudando: bool = False) -> None:
        cfg = {
            "configurable": {"thread_id": job_id},      # job_id == thread_id
            "recursion_limit": config.RECURSION_LIMIT,
            "metadata": {"job_id": job_id},
            "tags": ["huerta-viva-api"],
        }
        try:
            async with self._sem:                        # si hay cola, el job sigue PENDING
                if not reanudando:
                    await self.store.update(job_id, status=RUNNING, started_at=ahora())
                with job_span(job_id, fase, pregunta):
                    await self.graph.ainvoke(entrada_grafo, cfg)
                    snap = await self.graph.aget_state(cfg)
            await self._finalizar(job_id, snap)
        except asyncio.CancelledError:
            await self._fallar(job_id, "Cancelado: el servicio se estaba deteniendo.")
            raise
        except Exception as e:  # noqa: BLE001 - TODO error de fondo debe quedar en FAILED
            log.exception("Job %s falló", job_id)
            await self._fallar(job_id, f"{type(e).__name__}: {e}")

    async def _finalizar(self, job_id: str, snap) -> None:
        if snap.next:  # el grafo se detuvo antes de terminar -> ¿pausa del HITL?
            payload = next((i.value for t in snap.tasks for i in t.interrupts), None)
            if payload is None:
                return await self._fallar(job_id, f"Ejecución incompleta (siguiente nodo: {snap.next}).")
            await self.store.update(job_id, status=AWAITING_APPROVAL, pending_approval=payload)
            return

        v = snap.values
        if not v.get("task_completed"):
            return await self._fallar(job_id, "El grafo terminó sin generar la respuesta final.")
        job = await self.store.get(job_id) or {}
        aprobacion = v.get("approval") or {}
        rechazada = bool(v.get("requires_approval")) and aprobacion and not aprobacion.get("approved")
        await self.store.update(
            job_id,
            status=REJECTED if rechazada else DONE,
            result=v.get("final_answer", ""),
            contributions=v.get("contributions", []),
            approval=aprobacion or None,
            action_result=v.get("action_result") or None,
            finished_at=ahora(),
            elapsed_s=_segundos(job.get("created_at")),
            error=None,
        )

    async def _fallar(self, job_id: str, mensaje: str) -> None:
        try:
            job = await self.store.get(job_id) or {}
            await self.store.update(job_id, status=FAILED, error=mensaje[:1000],
                                    finished_at=ahora(), pending_approval=None,
                                    elapsed_s=_segundos(job.get("created_at")))
        except Exception:  # noqa: BLE001 - p. ej. Redis caído: solo podemos dejar rastro en el log
            log.exception("No se pudo marcar FAILED el job %s", job_id)


def _segundos(created_at: str | None) -> float | None:
    if not created_at:
        return None
    return round((datetime.fromisoformat(ahora()) - datetime.fromisoformat(created_at)).total_seconds(), 3)
