"""Observabilidad con Arize Phoenix (OpenInference).

Se instrumenta LangChain/LangGraph UNA vez al arrancar: eso traza solo, sin decorar
nada a mano, cada nodo del grafo, cada llamada al LLM (con tokens de entrada/salida,
de donde Phoenix calcula el costo) y cada herramienta.

Además, cada ejecución de un job se envuelve en un span raíz (`job_span`) con el
job_id como `session.id`: así todas las trazas de un mismo job (la ejecución y la
reanudación tras la aprobación) se agrupan en una Session de Phoenix.
"""

import logging
from contextlib import contextmanager

from app import config

log = logging.getLogger(__name__)
_activo = False


def init_observability() -> bool:
    """Devuelve True si quedó activa. Si Phoenix no está disponible NO rompe la API."""
    global _activo
    if _activo or not config.PHOENIX_ENABLED:
        return _activo
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        provider = register(
            project_name=config.PHOENIX_PROJECT_NAME,
            endpoint=config.PHOENIX_COLLECTOR_ENDPOINT,
            batch=True,                 # exporta en segundo plano, sin frenar las peticiones
            auto_instrument=False,      # instrumentamos explícitamente abajo
        )
        LangChainInstrumentor().instrument(tracer_provider=provider)
        _activo = True
        log.info("Phoenix activo -> %s (proyecto %s)",
                 config.PHOENIX_COLLECTOR_ENDPOINT, config.PHOENIX_PROJECT_NAME)
    except Exception as e:  # noqa: BLE001 - la observabilidad nunca debe tirar el servicio
        log.warning("No se pudo iniciar Phoenix (%s: %s). La API sigue sin trazas.",
                    type(e).__name__, e)
    return _activo


@contextmanager
def job_span(job_id: str, fase: str, entrada: str = ""):
    """Span raíz de una ejecución (`run` o `resume`). No-op si no hay OpenTelemetry."""
    try:
        from openinference.instrumentation import using_session
        from opentelemetry import trace
    except ImportError:
        yield None
        return
    atributos = {"openinference.span.kind": "CHAIN", "job.id": job_id, "session.id": job_id}
    if entrada:
        atributos["input.value"] = entrada
    with using_session(job_id), trace.get_tracer("huerta-viva.worker").start_as_current_span(
        f"job.{fase}", attributes=atributos
    ) as span:
        yield span
