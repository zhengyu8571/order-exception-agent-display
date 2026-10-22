# Agent 图编排模块架构

定义 V2 图拓扑、状态增量合并和业务节点。四路初始调查固定并行；模型提出的补查请求由 Send 动态扇出，在汇合节点集中合并证据并校验版本。

![Agent 图编排模块架构](../diagrams/modules/module_agent.png)

[放大 SVG](../diagrams/modules/module_agent.svg) · [Mermaid 源码](../diagrams/modules/module_agent.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [graph.py](../../src/order_agent/agent/graph.py) | 图拓扑、固定并行和 Send 路由 |
| [state.py](../../src/order_agent/agent/state.py) | State、Reducer 与稳定操作身份 |
| [adaptive_nodes.py](../../src/order_agent/agent/adaptive_nodes.py) | V2 决策、补查、规则反馈与资源复核 |
| [nodes.py](../../src/order_agent/agent/nodes.py) | 基础调查、V1 兼容及审批执行节点 |
| [observations.py](../../src/order_agent/agent/observations.py) | 模型可见事实、查询身份与无解反证 |

## 模块边界

模型只选择受约束动作，图的拓扑和执行闸门由程序定义。LangGraph 提供调度、Reducer、Checkpoint 与中断机制，项目定义节点业务和审批执行协议。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow Agent 图编排模块架构
SERVICE["TaskService"]
    subgraph CORE["Agent 模块 · V2 主图"]
        GRAPH["graph.py<br/>StateGraph 拓扑与路由"]
        STATE["state.py<br/>facts / supplementary Reducer"]
        FETCH["基础调查节点<br/>订单 / 库存 / 到货 / 约束"]
        DECIDE["adaptive_nodes.py + observations.py<br/>决策、观察与规则反馈"]
        SUPP["supplemental / investigate_join<br/>Send 扇出与证据汇合"]
        EXEC["审批 / 复核 / 执行 / 验证节点"]
        GRAPH --> STATE
        GRAPH --> FETCH
        GRAPH --> DECIDE
        GRAPH --> SUPP
        GRAPH --> EXEC
        DECIDE -->|"补查请求"| SUPP
        SUPP -.->|"新增证据"| DECIDE
    end
    DEPS["domain / llm / integrations<br/>规则、模型与业务工具"]
    RUNTIME["LangGraph 运行时<br/>调度 / Send / Interrupt / Command"]
    SAVER["PostgresSaver<br/>Checkpoint / pending writes"]
    SERVICE --> GRAPH
    DECIDE --> DEPS
    FETCH --> DEPS
    EXEC --> DEPS
    GRAPH -->|"编译、调度与中断"| RUNTIME
    GRAPH -->|"compile 注入 Checkpointer"| SAVER
    class GRAPH,STATE,FETCH,DECIDE,SUPP,EXEC component
    class RUNTIME,SAVER framework
    class SERVICE,DEPS external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
