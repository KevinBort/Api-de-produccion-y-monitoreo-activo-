"""API de punta a punta: encola sin bloquear, estados en Redis, FAILED, HITL y concurrencia."""

import asyncio
import time

import httpx
import pytest

from app.main import app
from tests.conftest import instalar_agentes_falsos

pytestmark = pytest.mark.asyncio


async def _cliente():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _esperar(c, job_id, estados, timeout=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        job = (await c.get(f"/tasks/{job_id}")).json()
        if job["status"] in estados:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"timeout esperando {estados}; último estado: {job['status']}")


async def test_flujo_normal_done(monkeypatch, redis_o_skip, bandeja):
    instalar_agentes_falsos(monkeypatch)
    async with app.router.lifespan_context(app), await _cliente() as c:
        r = await c.post("/tasks", json={"question": "¿Cuánto cuesta la cuota?"})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "PENDING" and body["requires_approval"] is False
        job = await _esperar(c, body["job_id"], {"DONE", "FAILED"})
        assert job["status"] == "DONE" and "Respuesta de prueba" in job["result"]
        assert job["contributions"] and job["elapsed_s"] is not None


async def test_error_en_segundo_plano_termina_en_failed(monkeypatch, redis_o_skip):
    instalar_agentes_falsos(monkeypatch, falla=True)
    async with app.router.lifespan_context(app), await _cliente() as c:
        job_id = (await c.post("/tasks", json={"question": "¿Cuánto cuesta la cuota?"})).json()["job_id"]
        job = await _esperar(c, job_id, {"DONE", "FAILED"})
        assert job["status"] == "FAILED" and "fallo simulado" in job["error"]


async def test_hitl_aprobar(monkeypatch, redis_o_skip, bandeja):
    instalar_agentes_falsos(monkeypatch)
    async with app.router.lifespan_context(app), await _cliente() as c:
        body = (await c.post("/tasks", json={"question": "Envía el informe de cuotas al consejo"})).json()
        assert body["requires_approval"] is True
        job = await _esperar(c, body["job_id"], {"AWAITING_APPROVAL", "DONE", "FAILED"})
        assert job["status"] == "AWAITING_APPROVAL" and job["pending_approval"]["borrador"]
        assert bandeja == []
        r = await c.post(f"/tasks/{body['job_id']}/approve", json={"approved": True, "comment": "ok"})
        assert r.status_code == 202
        job = await _esperar(c, body["job_id"], {"DONE", "FAILED"})
        assert job["status"] == "DONE" and job["action_result"]["accion"] and len(bandeja) == 1


async def test_hitl_rechazar(monkeypatch, redis_o_skip, bandeja):
    instalar_agentes_falsos(monkeypatch)
    async with app.router.lifespan_context(app), await _cliente() as c:
        job_id = (await c.post("/tasks", json={"question": "Notifica a los socios la nueva cuota"})).json()["job_id"]
        await _esperar(c, job_id, {"AWAITING_APPROVAL"})
        await c.post(f"/tasks/{job_id}/approve", json={"approved": False, "comment": "no"})
        job = await _esperar(c, job_id, {"REJECTED", "DONE", "FAILED"})
        assert job["status"] == "REJECTED" and bandeja == []


async def test_aprobar_dos_veces_o_sin_pausa_da_409(monkeypatch, redis_o_skip, bandeja):
    instalar_agentes_falsos(monkeypatch)
    async with app.router.lifespan_context(app), await _cliente() as c:
        normal = (await c.post("/tasks", json={"question": "¿Cuánto cuesta la cuota?"})).json()["job_id"]
        await _esperar(c, normal, {"DONE"})
        assert (await c.post(f"/tasks/{normal}/approve", json={"approved": True})).status_code == 409

        crit = (await c.post("/tasks", json={"question": "Envía el informe al consejo"})).json()["job_id"]
        await _esperar(c, crit, {"AWAITING_APPROVAL"})
        rs = await asyncio.gather(*[c.post(f"/tasks/{crit}/approve", json={"approved": True}) for _ in range(4)])
        assert sorted(r.status_code for r in rs) == [202, 409, 409, 409]   # solo UNA reanuda
        await _esperar(c, crit, {"DONE"})
        assert len(bandeja) == 1                                           # la acción corrió UNA vez


async def test_404_e_ids_invalidos(monkeypatch, redis_o_skip):
    instalar_agentes_falsos(monkeypatch)
    async with app.router.lifespan_context(app), await _cliente() as c:
        assert (await c.get("/tasks/" + "0" * 32)).status_code == 404
        assert (await c.get("/tasks/../../etc")).status_code == 404
        assert (await c.post("/tasks/xyz/approve", json={"approved": True})).status_code == 404
        assert (await c.post("/tasks", json={"question": "hi"})).status_code == 422   # muy corta


async def test_no_bloquea_el_event_loop_con_5_jobs_lentos(monkeypatch, redis_o_skip):
    """5 jobs con un nodo SÍNCRONO de 1 s cada uno: /health debe seguir respondiendo ya."""
    instalar_agentes_falsos(monkeypatch, demora=1.0)
    async with app.router.lifespan_context(app), await _cliente() as c:
        t0 = time.monotonic()
        ids = [(await c.post("/tasks", json={"question": f"¿Cuánto cuesta la cuota? #{i}"})).json()["job_id"]
               for i in range(5)]
        t_post = time.monotonic() - t0
        peor = 0.0
        while True:
            t1 = time.monotonic()
            assert (await c.get("/health")).status_code == 200
            peor = max(peor, time.monotonic() - t1)
            estados = [(await c.get(f"/tasks/{i}")).json()["status"] for i in ids]
            if all(e == "DONE" for e in estados):
                break
            await asyncio.sleep(0.05)
        total = time.monotonic() - t0
        print(f"\n5 POST en {t_post:.2f}s | peor latencia de /health: {peor*1000:.0f} ms | total {total:.2f}s")
        assert t_post < 0.5          # encolar es instantáneo
        assert peor < 0.25           # el event loop nunca se bloqueó
        assert total < 3.5           # corrieron en paralelo (en serie serían >= 5 s)


async def test_huerfanos_se_marcan_failed_al_arrancar(monkeypatch, redis_o_skip):
    from app.jobs import JobStore
    instalar_agentes_falsos(monkeypatch)
    s = JobStore()
    job = await s.create("pregunta vieja de otra ejecución", False)
    await s.update(job["job_id"], status="RUNNING")
    await s.close()
    async with app.router.lifespan_context(app), await _cliente() as c:
        j = (await c.get(f"/tasks/{job['job_id']}")).json()
        assert j["status"] == "FAILED" and "reinicio" in j["error"]
