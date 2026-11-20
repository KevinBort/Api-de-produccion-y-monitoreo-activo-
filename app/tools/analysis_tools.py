"""Herramientas del agente ANALISTA.

  - analizar_sentimiento_resenas: lee las reseñas del ESTADO (InjectedState),
    así el LLM no tiene que copiarlas en la llamada; las clasifica TODAS en UNA
    sola llamada al LLM (pre-entrega 7: antes era una llamada por reseña, lo que
    disparaba tokens, latencia y rate limits) y calcula los porcentajes con Python.
  - calcular: aritmética exacta y segura (sin eval).
"""

import ast
import operator as op
import re
from typing import Annotated, Callable, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

Etiqueta = Literal["positivo", "neutral", "negativo"]


# ----------------------------------------------------------------- sentimiento
class Sentimiento(BaseModel):
    sentimiento: Etiqueta


_POS = ["me encanta", "excelente", "genial", "lo mejor", "maravillos", "hermos",
        "fantástic", "agradecid", "orgullo", "recomendable", "tesoro", "muy bien",
        "muy linda", "feliz", "gracias", "justos", "cálida", "razonable"]
_NEG = ["injusto", "frustrante", "desastre", "decepcionante", "pésima", "absurd",
        "desmotiv", "durísima", "poco empática", "espantoso", "imposible", "error",
        "desanima", "perdí", "sufren", "eterna"]


def clasificar_lexico(texto: str) -> Etiqueta:
    """Plan B sin LLM: cuenta palabras clave. Solo se usa si el modelo falla."""
    t = texto.lower()
    pos = sum(p in t for p in _POS)
    neg = sum(n in t for n in _NEG)
    if pos > neg:
        return "positivo"
    if neg > pos:
        return "negativo"
    return "neutral"


def _clasificador_llm() -> Callable[[str], Etiqueta]:
    """Clasificador basado en el LLM (una llamada por reseña, salida estructurada)."""
    from app.llm import get_llm  # import tardío: los tests sin Ollama no lo necesitan

    estructurado = get_llm(0).with_structured_output(Sentimiento)
    sistema = (
        "Clasificas el sentimiento de reseñas de socios de una cooperativa de huertas. "
        "Responde positivo, negativo o neutral. Usa neutral cuando la opinión es "
        "mixta, descriptiva o sin una postura clara."
    )

    def clasificar(texto: str) -> Etiqueta:
        r = estructurado.invoke([("system", sistema), ("user", texto)])
        return r.sentimiento

    return clasificar


def analizar_lista(resenas: list[dict], clasificador: Callable[[str], Etiqueta]) -> dict:
    """Clasifica cada reseña y calcula conteos y porcentajes con Python."""
    detalle, fallbacks = [], 0
    for r in resenas:
        try:
            etiqueta = clasificador(r["texto"])
            metodo = "llm"
        except Exception:
            etiqueta, metodo = clasificar_lexico(r["texto"]), "lexico"
            fallbacks += 1
        detalle.append({"id": r.get("id", ""), "sentimiento": etiqueta, "metodo": metodo})

    total = len(detalle)
    conteo = {e: sum(d["sentimiento"] == e for d in detalle) for e in ("positivo", "neutral", "negativo")}
    porcentajes = {e: round(100 * n / total, 1) if total else 0.0 for e, n in conteo.items()}
    return {
        "tipo": "sentimiento",
        "total": total,
        "conteo": conteo,
        "porcentajes": porcentajes,
        "detalle": detalle,
        "resenas_con_plan_b": fallbacks,
    }



# ------------------------------------------------------ clasificación en UNA llamada
class ItemSentimiento(BaseModel):
    n: int = Field(description="Número de la reseña tal como aparece en la lista (empieza en 1)")
    sentimiento: Etiqueta


class LoteSentimiento(BaseModel):
    resultados: list[ItemSentimiento] = Field(description="Una entrada por cada reseña de la lista")


_SISTEMA_LOTE = (
    "Clasificas el sentimiento de reseñas de socios de una cooperativa de huertas. "
    "Recibes una lista numerada. Devuelve UNA entrada por reseña, con su número y su "
    "sentimiento: positivo, negativo o neutral. Usa neutral cuando la opinión es "
    "mixta, descriptiva o sin una postura clara."
)


def _clasificador_lote_llm() -> Callable[[list[str]], dict[int, Etiqueta]]:
    """Clasifica todas las reseñas con UNA llamada. Devuelve {posición(0-based): etiqueta}."""
    from app.llm import get_llm  # import tardío: los tests sin LLM no lo necesitan

    estructurado = get_llm(0).with_structured_output(LoteSentimiento)

    def clasificar(textos: list[str]) -> dict[int, Etiqueta]:
        lista = "\n".join(f"{i}. {t}" for i, t in enumerate(textos, 1))
        r = estructurado.invoke([("system", _SISTEMA_LOTE), ("user", lista)])
        return {it.n - 1: it.sentimiento for it in r.resultados if 1 <= it.n <= len(textos)}

    return clasificar


def analizar_lista_lote(resenas: list[dict], clasificador_lote: Callable[[list[str]], dict[int, Etiqueta]]) -> dict:
    """Igual que analizar_lista, pero con una sola llamada al LLM para todo el lote.

    Plan B por reseña: si el modelo omite alguna (o falla la llamada completa) se usa
    el léxico solo para esas, sin repetir la llamada (así el coste es siempre 1 llamada)."""
    try:
        por_pos = clasificador_lote([r["texto"] for r in resenas]) if resenas else {}
    except Exception:
        por_pos = {}
    detalle, fallbacks = [], 0
    for i, r in enumerate(resenas):
        if i in por_pos:
            etiqueta, metodo = por_pos[i], "llm-lote"
        else:
            etiqueta, metodo = clasificar_lexico(r["texto"]), "lexico"
            fallbacks += 1
        detalle.append({"id": r.get("id", ""), "sentimiento": etiqueta, "metodo": metodo})

    total = len(detalle)
    conteo = {e: sum(d["sentimiento"] == e for d in detalle) for e in ("positivo", "neutral", "negativo")}
    porcentajes = {e: round(100 * n / total, 1) if total else 0.0 for e, n in conteo.items()}
    return {
        "tipo": "sentimiento", "total": total, "conteo": conteo, "porcentajes": porcentajes,
        "detalle": detalle, "resenas_con_plan_b": fallbacks,
    }


@tool(response_format="content_and_artifact")
def analizar_sentimiento_resenas(
    state: Annotated[dict, InjectedState],
) -> tuple[str, dict]:
    """Analiza el sentimiento (positivo, neutral, negativo) de TODAS las reseñas que
    el investigador dejó en el estado y devuelve conteos y porcentajes.
    No necesita argumentos."""
    resenas = (state.get("research_output") or {}).get("resenas", [])
    if not resenas:
        return "ERROR: no hay reseñas en el estado. El investigador debe buscarlas primero.", {
            "tipo": "error", "detalle": "sin reseñas"}
    try:
        res = analizar_lista_lote(resenas, _clasificador_lote_llm())
    except Exception:
        res = analizar_lista(resenas, clasificar_lexico)  # si el LLM ni arranca, plan B completo
    texto = (
        f"Reseñas analizadas: {res['total']}. "
        f"Positivas: {res['conteo']['positivo']} ({res['porcentajes']['positivo']}%), "
        f"neutrales: {res['conteo']['neutral']} ({res['porcentajes']['neutral']}%), "
        f"negativas: {res['conteo']['negativo']} ({res['porcentajes']['negativo']}%)."
    )
    return texto, res


# --------------------------------------------------------------------- cálculo
_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
    ast.Div: op.truediv, ast.Mod: op.mod, ast.Pow: op.pow,
}


def _evaluar(nodo):
    if isinstance(nodo, ast.Expression):
        return _evaluar(nodo.body)
    if isinstance(nodo, ast.Constant) and type(nodo.value) in (int, float):
        return nodo.value
    if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, (ast.USub, ast.UAdd)):
        v = _evaluar(nodo.operand)
        return -v if isinstance(nodo.op, ast.USub) else v
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPS:
        a, b = _evaluar(nodo.left), _evaluar(nodo.right)
        if isinstance(nodo.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise ValueError("potencia demasiado grande")
        return _OPS[type(nodo.op)](a, b)
    raise ValueError("expresión no permitida")


def evaluar_expresion(expresion: str) -> float:
    limpia = re.sub(r"\s+", "", expresion)
    return _evaluar(ast.parse(limpia, mode="eval"))


@tool(response_format="content_and_artifact")
def calcular(expresion: str) -> tuple[str, dict]:
    """Calcula una expresión aritmética exacta. Operadores: + - * / % ** y paréntesis.
    Escribe los números SIN separador de miles (4500, no 4.500) y con punto decimal.
    Ejemplo: '4500*12' o '(4500-2500)*12'."""
    try:
        r = evaluar_expresion(expresion)
    except Exception as e:
        return f"ERROR: no pude calcular '{expresion}' ({e}).", {
            "tipo": "error", "detalle": str(e)}
    r = round(r, 4)
    return f"{expresion} = {r}", {"tipo": "calculo", "expresion": expresion, "resultado": r}


ANALYSIS_TOOLS = [analizar_sentimiento_resenas, calcular]
