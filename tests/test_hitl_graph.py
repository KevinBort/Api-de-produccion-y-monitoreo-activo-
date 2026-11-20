"""El nodo de aprobación humana: pausa, reanuda y respeta el rechazo (sin API ni Redis)."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph import build_graph
from app.state import initial_state
from tests.conftest import instalar_agentes_falsos


def _grafo(monkeypatch):
    instalar_agentes_falsos(monkeypatch)
    return build_graph(checkpointer=InMemorySaver())


def _cfg(hilo):
    return {"configurable": {"thread_id": hilo}}


def test_tarea_normal_no_se_pausa(monkeypatch, bandeja):
    g = _grafo(monkeypatch)
    g.invoke(initial_state("¿Cuánto cuesta la cuota?"), _cfg("t1"))
    snap = g.get_state(_cfg("t1"))
    assert snap.next == () and snap.values["task_completed"]
    assert bandeja == []                                   # sin efecto secundario


def test_tarea_critica_se_pausa_y_no_ejecuta_nada_antes_de_aprobar(monkeypatch, bandeja):
    g = _grafo(monkeypatch)
    g.invoke(initial_state("Envía el informe al consejo", requires_approval=True), _cfg("t2"))
    snap = g.get_state(_cfg("t2"))
    assert snap.next == ("human_approval",)                # pausado
    payload = snap.tasks[0].interrupts[0].value
    assert payload["tipo"] == "aprobacion_requerida" and "Respuesta de prueba" in payload["borrador"]
    assert bandeja == []                                   # ¡nada se envió todavía!


def test_aprobada_ejecuta_la_accion_una_sola_vez(monkeypatch, bandeja):
    g = _grafo(monkeypatch)
    g.invoke(initial_state("Envía el informe al consejo", requires_approval=True), _cfg("t3"))
    g.invoke(Command(resume={"approved": True, "comment": "ok"}), _cfg("t3"))
    snap = g.get_state(_cfg("t3"))
    assert snap.next == () and snap.values["action_result"]["accion"]
    assert len(bandeja) == 1 and bandeja[0]["comentario_aprobador"] == "ok"


def test_rechazada_termina_sin_ejecutar_la_accion(monkeypatch, bandeja):
    g = _grafo(monkeypatch)
    g.invoke(initial_state("Envía el informe al consejo", requires_approval=True), _cfg("t4"))
    g.invoke(Command(resume={"approved": False, "comment": "no"}), _cfg("t4"))
    snap = g.get_state(_cfg("t4"))
    assert snap.next == () and snap.values["approval"]["approved"] is False
    assert bandeja == [] and not snap.values["action_result"]
