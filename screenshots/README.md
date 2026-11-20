# Capturas del dashboard de Phoenix (evidencia de observabilidad)

Guarda aquí las capturas (PNG) después de correr `python scripts/load_test.py`:

| Archivo sugerido | Qué debe verse |
|---|---|
| `01_trazas_arbol.png` | Una traza abierta: árbol `job.run` → `LangGraph` → nodos (supervisor, researcher, analyst, synthesizer) → llamadas al LLM |
| `02_costo_por_ejecucion.png` | Costo por ejecución de las 5 peticiones (columna *Cost* de la tabla de trazas, o la pestaña de costos del proyecto) |
| `03_latencia_p95.png` | Latencia p95 de la corrida (gráfico de latencia del proyecto, o la columna *Latency* de las 5 trazas) |
| `04_hitl_traza.png` | (opcional) Traza de una tarea crítica: `job.run` que termina en la pausa y `job.resume` tras aprobar, en la misma *Session* |
