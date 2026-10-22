# 任务与审批服务模块架构

协调创建、审批、恢复、重规划和任务详情，使用同任务会话锁串行化推进。审批先持久化，随后恢复图；详情结合任务投影与 Checkpoint 确定可恢复状态。

![任务与审批服务模块架构](../diagrams/modules/module_services.png)

[放大 SVG](../diagrams/modules/module_services.svg) · [Mermaid 源码](../diagrams/modules/module_services.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [tasks.py](../../src/order_agent/services/tasks.py) | TaskService 与初始状态、驱动、审批和恢复 |
| [task_repository.py](../../src/order_agent/persistence/task_repository.py) | 任务锁、任务投影、审批及持久事件 |
| [bindings.py](../../src/order_agent/domain/bindings.py) | 批准内容的摘要绑定 |

## 模块边界

任务服务采用请求驱动，没有后台 Worker。审批记录、图 Checkpoint 和外部业务写入是不同的提交边界。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 任务与审批服务模块架构
ROUTE["🔌 任务 HTTP 接口"]
    subgraph CORE["TaskService · 请求驱动编排"]
        CREATE["create / initial_state<br/>持久任务与固定配置"]
        APPROVE["approve<br/>角色、版本与摘要校验"]
        RECOVER["recover / replan<br/>恢复或建立新一轮调查"]
        DRIVE["drive<br/>推进图并写阶段事件"]
        DETAIL["detail / page<br/>任务投影与图状态组合"]
        CREATE --> DRIVE
        APPROVE -->|"批准已提交后恢复"| DRIVE
        RECOVER --> DRIVE
    end
    REPO["💾 task_repository<br/>任务会话锁 / 审批 / 事件"]
    GRAPH["LangGraph graph<br/>stream / get_state / Command"]
    BINDING["domain.bindings<br/>方案、证据与资源摘要"]
    ROUTE --> CREATE
    ROUTE --> APPROVE
    ROUTE --> RECOVER
    ROUTE --> DETAIL
    APPROVE --> BINDING
    APPROVE --> REPO
    CREATE --> REPO
    RECOVER --> REPO
    DRIVE --> GRAPH
    DRIVE --> REPO
    DETAIL --> GRAPH
    DETAIL --> REPO
    class CREATE,APPROVE,RECOVER,DRIVE,DETAIL component
    class GRAPH framework
    class ROUTE,REPO,BINDING external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
