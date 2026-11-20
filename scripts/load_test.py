"""Prueba de carga: N peticiones CONCURRENTES contra la API (por defecto 5).

    python scripts/load_test.py                       # 5 peticiones a http://localhost:8000
    python scripts/load_test.py --n 5 --url http://localhost:8000

Qué hace: lanza las N tareas a la vez, espera a que cada una llegue a un estado final
(haciendo polling a GET /tasks/{id}) y mide la latencia de punta a punta de cada una.

Las tareas son SOLO de lectura y se envían con requires_approval=false: ninguna queda
pausada esperando aprobación, así la latencia medida es representativa.

Las métricas que pide la consigna (costo por ejecución y p95) las calcula y muestra
Phoenix en http://localhost:6006 con las trazas de esta corrida; este script imprime
la p95 del lado del cliente como contraste, y deja el detalle en load_test_results.json.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

PREGUNTAS = [
    "Investiga cuánto cuesta la cuota para un socio jubilado y calcula cuánto pagaría en un año.",
    "¿Qué opinan los socios sobre las cuotas? Dime el porcentaje de opiniones positivas.",
    "Resume los requisitos para ser socio y qué dicen las reseñas sobre los talleres.",
    "¿Qué sanciones prevé el reglamento y qué opinan los socios sobre la publicación de normas?",
    "Calcula el porcentaje de opiniones positivas sobre el compostaje.",
]
FINALES = {"DONE", "FAILED", "REJECTED"}


def percentil(valores: list[float], p: float) -> float:
    """Interpolación lineal (igual que numpy.percentile por defecto)."""
    xs = sorted(valores)
    k = (len(xs) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


async def un_job(client: httpx.AsyncClient, i: int, pregunta: str, poll: float, timeout: float) -> dict:
    t0 = time.perf_counter()
    r = await client.post("/tasks", json={"question": pregunta, "requires_approval": False})
    r.raise_for_status()
    job_id = r.json()["job_id"]
    t_encolado = time.perf_counter() - t0
    job = {}
    while time.perf_counter() - t0 < timeout:
        job = (await client.get(f"/tasks/{job_id}")).json()
        if job["status"] in FINALES:
            break
        await asyncio.sleep(poll)
    else:
        job = {"status": "TIMEOUT"}
    return {
        "n": i + 1, "job_id": job_id, "status": job["status"],
        "latencia_s": round(time.perf_counter() - t0, 2),
        "encolado_en_s": round(t_encolado, 3),
        "elapsed_servidor_s": job.get("elapsed_s"),
        "error": job.get("error"), "pregunta": pregunta,
    }


async def main(url: str, n: int, poll: float, timeout: float) -> None:
    preguntas = [PREGUNTAS[i % len(PREGUNTAS)] for i in range(n)]
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        health = (await client.get("/health")).json()
        print(f"API: {url} | {health}\nLanzando {n} peticiones concurrentes...\n")
        t0 = time.perf_counter()
        res = await asyncio.gather(*[un_job(client, i, q, poll, timeout) for i, q in enumerate(preguntas)])
        total = time.perf_counter() - t0

    print(f"{'#':>2}  {'job_id':<10} {'estado':<8} {'latencia':>9}  {'encolado':>9}")
    for r in res:
        print(f"{r['n']:>2}  {r['job_id'][:8]:<10} {r['status']:<8} {r['latencia_s']:>8.2f}s  {r['encolado_en_s']*1000:>7.0f}ms"
              + (f"   ERROR: {r['error']}" if r["error"] else ""))
    lat = [r["latencia_s"] for r in res if r["status"] == "DONE"]
    ok = len(lat)
    print(f"\nCompletadas: {ok}/{n} | tiempo total de la corrida: {total:.2f}s")
    if lat:
        print(f"Latencia cliente  p50={percentil(lat, 50):.2f}s  p95={percentil(lat, 95):.2f}s  max={max(lat):.2f}s")
        if n <= 10:
            print("(con tan pocas muestras la p95 es prácticamente la corrida más lenta)")
    print("\nAhora abre Phoenix (http://localhost:6006) -> proyecto de las trazas -> captura:"
          "\n  1) árbol de una traza   2) costo por ejecución   3) latencia p95   (ver README)")
    salida = Path("load_test_results.json")
    salida.write_text(json.dumps({"url": url, "n": n, "total_s": round(total, 2), "resultados": res},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detalle guardado en {salida}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--n", type=int, default=5, help="peticiones concurrentes (default 5)")
    ap.add_argument("--poll", type=float, default=0.5, help="segundos entre consultas de estado")
    ap.add_argument("--timeout", type=float, default=600, help="segundos máximos por tarea")
    a = ap.parse_args()
    asyncio.run(main(a.url, a.n, a.poll, a.timeout))
