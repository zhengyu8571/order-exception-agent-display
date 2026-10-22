from langgraph.graph import END, START, StateGraph

from order_agent.agent.adaptive_nodes import AdaptiveWorkflowNodes
from order_agent.agent.state import State
from order_agent.integrations.order_client import OrderTools


def build_graph(checkpointer, *, model=None, tools=None):
    nodes = AdaptiveWorkflowNodes(tools or OrderTools(), model=model)
    graph = StateGraph(State)
    sources = ["order", "stock", "arrivals", "constraints"]
    # START 扇出 + 多起点 join 由 LangGraph 调度；项目定义节点职责，不实现底层线程调度器。
    for source in sources:
        graph.add_node("fetch_" + source, nodes.fetch(source))
        graph.add_edge(START, "fetch_" + source)
    graph.add_node("propose", nodes.propose_node)
    graph.add_node("validate", nodes.validate_node)
    graph.add_node("approval", nodes.approval_node)
    graph.add_node("revalidate", nodes.revalidate_node)
    graph.add_node("execute", nodes.execute_node)
    graph.add_node("verify", nodes.verify_node)
    graph.add_node("supplemental", nodes.supplemental_node)
    graph.add_node("investigate_join", nodes.investigate_join_node)
    graph.add_edge(["fetch_" + source for source in sources], "propose")
    # 主图固定，模型只在受限的业务决策范围内选择调查、提案、修正或转人工。
    # 补充调查由路由返回 Send 列表动态扇出，扇出数量取决于本轮请求数量。
    graph.add_conditional_edges("propose", nodes.route_propose)
    graph.add_edge("supplemental", "investigate_join")
    graph.add_conditional_edges("investigate_join", nodes.route_join)
    graph.add_conditional_edges(
        "validate",
        lambda s: "approval"
        if s["phase"] == "WAITING_APPROVAL"
        else "propose"
        if s["phase"] == "RETHINK"
        else END,
    )
    graph.add_conditional_edges(
        "approval", lambda s: "revalidate" if s["phase"] == "APPROVED" else END
    )
    graph.add_conditional_edges(
        "revalidate", lambda s: "execute" if s["phase"] == "EXECUTING" else END
    )
    graph.add_conditional_edges(
        "execute", lambda s: "verify" if s["phase"] == "VERIFYING" else END
    )
    graph.add_edge("verify", END)
    return graph.compile(checkpointer=checkpointer, name="order-exception")
