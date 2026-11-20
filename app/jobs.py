"""Estado de los jobs en Redis (async: no bloquea el event loop).

Dos capas de persistencia, con claves distintas:
  - `job:{id}`     -> hash con el estado que ve el CLIENTE (este módulo).
  - `checkpoint:*` -> checkpoints internos de LangGraph (los gestiona AsyncRedisSaver).
`job_id` == `thread_id` del grafo.

Estados:
  PENDING -> RUNNING -> DONE
                    └-> AWAITING_APPROVAL -> RUNNING -> DONE | REJECTED
  cualquier fallo -> FAILED   (¡siempre se llega a un estado final! si no, el cliente
                               se queda para siempre en un polling infinito)
"""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis

from app import config

PENDING, RUNNING, AWAITING_APPROVAL = "PENDING", "RUNNING", "AWAITING_APPROVAL"
DONE, FAILED, REJECTED = "DONE", "FAILED", "REJECTED"
TERMINALES = {DONE, FAILED, REJECTED}

_JSON_FIELDS = {"pending_approval", "approval", "action_result", "contributions"}
_ID = re.compile(r"^[0-9a-f]{32}$")

# Transición atómica (compare-and-set): evita que dos aprobaciones simultáneas
# reanuden el mismo job dos veces (y ejecuten la acción dos veces).
_CAS = """
if redis.call('HGET', KEYS[1], 'status') == ARGV[1] then
  redis.call('HSET', KEYS[1], 'status', ARGV[2], 'updated_at', ARGV[3])
  return 1
end
return 0
"""


def valid_job_id(job_id: str) -> bool:
    return bool(_ID.match(job_id))


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _key(job_id: str) -> str:
    return f"job:{job_id}"


class JobStore:
    def __init__(self, url: Optional[str] = None, ttl: Optional[int] = None):
        self.r = aioredis.from_url(url or config.REDIS_URL, decode_responses=True)
        self.ttl = ttl or config.JOB_TTL_SECONDS
        self._cas = self.r.register_script(_CAS)

    async def ping(self) -> bool:
        try:
            return bool(await self.r.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.r.aclose()

    # ------------------------------------------------------------------ escritura
    async def create(self, question: str, requires_approval: bool) -> dict:
        job_id = uuid.uuid4().hex
        t = ahora()
        await self._write(job_id, {
            "job_id": job_id, "status": PENDING, "question": question,
            "requires_approval": "1" if requires_approval else "0",
            "created_at": t, "updated_at": t,
        })
        return await self.get(job_id)

    async def update(self, job_id: str, **fields: Any) -> None:
        """None borra el campo. Los dict/list se guardan como JSON."""
        await self._write(job_id, {**fields, "updated_at": ahora()})

    async def transition(self, job_id: str, esperado: str, nuevo: str) -> bool:
        """Cambia el estado SOLO si sigue siendo `esperado`. Devuelve si lo logró."""
        ok = await self._cas(keys=[_key(job_id)], args=[esperado, nuevo, ahora()])
        return bool(ok)

    async def _write(self, job_id: str, fields: dict) -> None:
        sets, dels = {}, []
        for k, v in fields.items():
            if v is None:
                dels.append(k)
            elif k in _JSON_FIELDS or isinstance(v, (dict, list)):
                sets[k] = json.dumps(v, ensure_ascii=False)
            else:
                sets[k] = str(v)
        pipe = self.r.pipeline()
        if sets:
            pipe.hset(_key(job_id), mapping=sets)
        if dels:
            pipe.hdel(_key(job_id), *dels)
        pipe.expire(_key(job_id), self.ttl)
        await pipe.execute()

    # -------------------------------------------------------------------- lectura
    async def get(self, job_id: str) -> Optional[dict]:
        raw = await self.r.hgetall(_key(job_id))
        if not raw:
            return None
        job = {}
        for k, v in raw.items():
            if k in _JSON_FIELDS:
                try:
                    job[k] = json.loads(v)
                except ValueError:
                    job[k] = v
            elif k == "requires_approval":
                job[k] = v == "1"
            else:
                job[k] = v
        return job

    async def fail_orphans(self) -> int:
        """Al arrancar: los jobs PENDING/RUNNING de una ejecución anterior ya no tienen
        quien los procese (el worker vive dentro del proceso) -> pasarlos a FAILED para
        que ningún cliente espere eternamente. Los AWAITING_APPROVAL se conservan: su
        checkpoint está en Redis y se pueden aprobar aunque el servicio se haya reiniciado.

        Supone UNA sola réplica del servicio (con varias habría que usar Arq/Celery)."""
        n = 0
        async for key in self.r.scan_iter(match="job:*", count=200):
            estado = await self.r.hget(key, "status")
            if estado in (PENDING, RUNNING):
                await self.r.hset(key, mapping={
                    "status": FAILED, "updated_at": ahora(), "finished_at": ahora(),
                    "error": "Interrumpido por un reinicio del servicio antes de terminar.",
                })
                n += 1
        return n
