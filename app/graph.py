"""Grafo principal: topología jerárquica hub-and-spoke + aprobación humana.

    START -> supervisor -> {researcher | analyst | synthesizer}
    researcher -> supervisor      (todos los especialistas vuelven al hub)
    analyst    -> supervisor
    synthesizer -> END                                   (tarea normal)
    synthesizer -> human_approval ⏸ -> ejecutar_accion -> END   (tarea crítica, ver hitl.py)

El grafo se compila con un CHECKPOINTER (Redis en producción): guarda el estado tras
cada nodo, y es lo que permite pausar en `interrupt()` y reanudar más tarde.
"""

from contextlib import asynccontextmanager

from langgraph.graph import END, START, StateGraph

from app import config
from app.agents.analyst_agent import make_analyst_node
from app.agents.research_agent import make_research_node
from app.agents.synthesizer_agent import make_synthesizer_node
from app.hitl import ejecutar_accion_node, human_approval_node, tras_aprobacion, tras_sintetizar
from app.llm import get_llm
from app.state import AgentState
from app.supervisor import make_supervisor, route


def build_graph(llm=None, checkpointer=None):
    llm = llm or get_llm(0)
    g = StateGraph(AgentState)
    g.add_node("supervisor", make_supervisor(llm))
    g.add_node("researcher", make_research_node(llm))
    g.add_node("analyst", make_analyst_node(llm))
    g.add_node("synthesizer", make_synthesizer_node(llm))
    g.add_node("human_approval", human_approval_node)
    g.add_node("ejecutar_accion", ejecutar_accion_node)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route)   # el Literal del retorno define los destinos
    g.add_edge("researcher", "supervisor")
    g.add_edge("analyst", "supervisor")
    # Tras sintetizar: ¿es una tarea crítica? -> pedir aprobación; si no, terminar.
    g.add_conditional_edges("synthesizer", tras_sintetizar,
                            {"human_approval": "human_approval", END: END})
    g.add_conditional_edges("human_approval", tras_aprobacion,
                            {"ejecutar_accion": "ejecutar_accion", END: END})
    g.add_edge("ejecutar_accion", END)
    return g.compile(checkpointer=checkpointer)


@asynccontextmanager
async def open_checkpointer():
    """Checkpointer según CHECKPOINT_BACKEND.

    - redis  (producción): AsyncRedisSaver. Requiere Redis con los módulos RedisJSON y
      RediSearch -> imagen `redis/redis-stack-server`, NO `redis` a secas.
    - memory (desarrollo/tests): no persiste nada.
    """
    if config.CHECKPOINT_BACKEND == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    ttl = {"default_ttl": config.CHECKPOINT_TTL_MINUTES, "refresh_on_read": True}
    async with AsyncRedisSaver.from_conn_string(config.REDIS_URL, ttl=ttl) as saver:
        await saver.asetup()   # crea los índices de RediSearch (idempotente)
        yield saver
