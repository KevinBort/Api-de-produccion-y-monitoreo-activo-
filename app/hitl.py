"""Human-in-the-loop: pausa obligatoria para tareas críticas.

¿Qué es una tarea crítica? Una que pide una ACCIÓN CON EFECTO SECUNDARIO sobre el
mundo exterior (aquí: enviar el informe al consejo coordinador). Es una regla
determinista (regex sobre la pregunta, o el flag `requires_approval` del POST), así
es predecible y fácil de demostrar. Las tareas normales (solo lectura) NO se pausan.

Flujo en el grafo:

    synthesizer ─┬─ no crítica ───────────────────────────────► END
                 └─ crítica ─► human_approval ⏸ ─┬─ aprobada ─► ejecutar_accion ─► END
                                                 └─ rechazada ──────────────────► END

`interrupt()` guarda el estado en el checkpointer (Redis) y detiene el grafo. Se
reanuda con `Command(resume=...)` desde POST /tasks/{id}/approve — incluso tras
reiniciar el servicio, porque el checkpoint vive en Redis.

OJO: al reanudar, LangGraph vuelve a ejecutar el nodo interrumpido desde su primera
línea. Por eso `human_approval` SOLO pregunta (sin efectos) y el efecto secundario
vive en otro nodo (`ejecutar_accion`), que corre una única vez, después.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from langgraph.graph import END
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app import config
from app.jobs import AWAITING_APPROVAL, valid_job_id
from app.state import AgentState

ACCION = "enviar_informe_consejo"
DESTINATARIO = "consejo-coordinador@huertaviva.example"

# Verbos que piden actuar hacia afuera (enviar / mandar / notificar / publicar / difundir).
_CRITICA = re.compile(
    r"\b(env[ií]a|manda|notifica|publica|difunde)(r|n)?(l[eoa]s?|me)?\b",
    re.IGNORECASE,
)


def es_critica(pregunta: str, override: Optional[bool] = None) -> bool:
    """`override` (el flag del POST) manda; si es None decide la regla."""
    if override is not None:
        return override
    return bool(_CRITICA.search(pregunta))


# ----------------------------------------------------------------- efecto secundario
def enviar_al_consejo(payload: dict) -> dict:
    """Acción con efecto secundario (SIMULADA): deja el envío en una bandeja de salida
    en Redis (`outbox:consejo`). En producción aquí iría el SMTP / webhook."""
    import redis

    registro = {
        "outbox_id": uuid.uuid4().hex,
        "accion": ACCION,
        "destinatario": DESTINATARIO,
        "enviado_en": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    cliente = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
    try:
        cliente.rpush("outbox:consejo", json.dumps(registro, ensure_ascii=False))
    finally:
        cliente.close()
    return {k: registro[k] for k in ("outbox_id", "accion", "destinatario", "enviado_en")}


# ------------------------------------------------------------------------- nodos
def human_approval_node(state: AgentState) -> dict:
    """Pausa el grafo hasta recibir la decisión de una persona. SIN efectos secundarios."""
    decision = interrupt({
        "tipo": "aprobacion_requerida",
        "accion": ACCION,
        "descripcion": f"Se enviará el siguiente informe a {DESTINATARIO}.",
        "pregunta": state["messages"][0].content,
        "borrador": state.get("final_answer", ""),
    })
    if isinstance(decision, dict):
        aprobado, comentario = bool(decision.get("approved")), str(decision.get("comment", ""))
    else:
        aprobado, comentario = bool(decision), ""
    return {"approval": {"approved": aprobado, "comment": comentario}}


def ejecutar_accion_node(state: AgentState) -> dict:
    """Solo se alcanza si una persona aprobó. Corre una única vez."""
    resultado = enviar_al_consejo({
        "pregunta": state["messages"][0].content,
        "informe": state.get("final_answer", ""),
        "comentario_aprobador": (state.get("approval") or {}).get("comment", ""),
    })
    return {"action_result": resultado}


# ----------------------------------------------------------------- aristas condicionales
def tras_sintetizar(state: AgentState) -> str:
    return "human_approval" if state.get("requires_approval") else END


def tras_aprobacion(state: AgentState) -> str:
    return "ejecutar_accion" if (state.get("approval") or {}).get("approved") else END


# ------------------------------------------------------------------ endpoint /approve
router = APIRouter()


class ApprovalIn(BaseModel):
    approved: bool = Field(description="true = aprobar y ejecutar la acción; false = rechazar")
    comment: str = Field("", max_length=500)


@router.post("/tasks/{job_id}/approve", status_code=202, tags=["HITL"])
async def approve_task(job_id: str, body: ApprovalIn, request: Request):
    """Reanuda una tarea pausada en `AWAITING_APPROVAL` con la decisión humana."""
    runner, store = request.app.state.runner, request.app.state.store
    if not valid_job_id(job_id) or await store.get(job_id) is None:
        raise HTTPException(404, "Tarea no encontrada")
    if not await runner.resume(job_id, body.approved, body.comment):
        actual = (await store.get(job_id) or {}).get("status")
        raise HTTPException(409, f"La tarea no espera aprobación (estado actual: {actual}, "
                                 f"se requiere {AWAITING_APPROVAL}).")
    return {"job_id": job_id, "status": "RUNNING", "approved": body.approved}
