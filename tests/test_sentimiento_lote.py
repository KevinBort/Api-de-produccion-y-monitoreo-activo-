"""Clasificación de sentimiento en UNA llamada (con plan B por reseña)."""

from app.tools.analysis_tools import analizar_lista_lote

RESENAS = [{"id": f"r{i}", "texto": t} for i, t in enumerate(
    ["me encanta todo", "es injusto y frustrante", "la cuota es normal", "excelente"])]


def test_una_sola_llamada_para_todas():
    llamadas = []
    def lote(textos):
        llamadas.append(len(textos))
        return {0: "positivo", 1: "negativo", 2: "neutral", 3: "positivo"}
    r = analizar_lista_lote(RESENAS, lote)
    assert llamadas == [4]                      # una llamada, no cuatro
    assert r["conteo"] == {"positivo": 2, "neutral": 1, "negativo": 1}
    assert r["resenas_con_plan_b"] == 0


def test_si_el_modelo_omite_una_se_usa_plan_b_solo_para_ella():
    r = analizar_lista_lote(RESENAS, lambda t: {0: "positivo", 1: "negativo", 2: "neutral"})
    assert [d["metodo"] for d in r["detalle"]] == ["llm-lote"] * 3 + ["lexico"]
    assert r["resenas_con_plan_b"] == 1


def test_si_falla_la_llamada_todo_va_por_plan_b():
    def falla(t):
        raise RuntimeError("429")
    r = analizar_lista_lote(RESENAS, falla)
    assert r["resenas_con_plan_b"] == 4 and r["total"] == 4
