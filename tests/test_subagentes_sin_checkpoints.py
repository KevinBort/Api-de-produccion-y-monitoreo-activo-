"""Regresión: los sub-agentes ReAct (investigador/analista) NO deben heredar el checkpointer.

Si lo heredaran, cada ejecución guardaría decenas de checkpoints internos en Redis.
Se usan los nodos REALES del M6 con un modelo falso que no llama a ninguna herramienta.
"""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.analyst_agent import make_analyst_node
from app.agents.research_agent import make_research_node
from app.state import AgentState, initial_state


class ModeloFalso(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):      # create_react_agent lo exige
        return self


def _namespaces(fabrica):
    saver = InMemorySaver()
    llm = ModeloFalso(messages=iter(lambda: AIMessage(content="sin herramientas"), None))
    g = StateGraph(AgentState)
    g.add_node("nodo", fabrica(llm))
    g.add_edge(START, "nodo")
    g.add_edge("nodo", END)
    g.compile(checkpointer=saver).invoke(initial_state("¿Cuánto cuesta la cuota?"),
                                         {"configurable": {"thread_id": "t"}})
    return {ns for por_hilo in saver.storage.values() for ns in por_hilo}


def test_investigador_no_guarda_checkpoints_internos():
    assert _namespaces(make_research_node) == {""}      # solo el grafo exterior


def test_analista_no_guarda_checkpoints_internos():
    assert _namespaces(make_analyst_node) == {""}
