# 持久化与数据库访问模块架构

管理项目连接池、版本化 SQL 迁移及任务、审批、模型和调查账本。任务推进使用会话锁，事件写入和预算准入各使用独立事务锁与约束。

![持久化与数据库访问模块架构](../diagrams/modules/module_persistence.png)

[放大 SVG](../diagrams/modules/module_persistence.svg) · [Mermaid 源码](../diagrams/modules/module_persistence.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [database.py](../../src/order_agent/persistence/database.py) | 按 DSN 复用连接池、连接上下文与关闭 |
| [migrations.py](../../src/order_agent/persistence/migrations.py) | 迁移顺序、校验和与数据库升级 |
| [task_repository.py](../../src/order_agent/persistence/task_repository.py) | 任务、批准、事件和会话锁 |
| [model_repository.py](../../src/order_agent/persistence/model_repository.py) | 模型预算预留、usage 与响应账本 |
| [investigation_repository.py](../../src/order_agent/persistence/investigation_repository.py) | 调查去重、预算及完成记录 |
| [](../../src/order_agent/persistence/sql/) | agent / business 版本化迁移 |

## 模块边界

项目迁移只管理业务和 Agent 表；LangGraph 自行管理 Checkpoint 表和迁移，PostgresSaver 在 transport 中通过另一连接池装配。三类表位于同一个 PostgreSQL。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 持久化与数据库访问模块架构
CALLER["TaskService / Agent / 业务服务"]
    subgraph CORE["项目持久化模块"]
        TASK["task_repository.py<br/>任务 / 批准 / 事件 / 会话锁"]
        MODEL["model_repository.py<br/>模型预算与响应账本"]
        INV["investigation_repository.py<br/>调查去重与预算"]
        MIG["migrations.py + sql/<br/>项目迁移与校验和"]
        POOL["database.py<br/>连接上下文与进程级池"]
        TASK --> POOL
        MODEL --> POOL
        INV --> POOL
        MIG --> POOL
    end
    DB[("💾 PostgreSQL<br/>agent / business 表")]
    SAVER["transport 装配 PostgresSaver<br/>独立连接池与框架迁移"]
    CHECKPOINT[("同一 PostgreSQL<br/>LangGraph Checkpoint 表")]
    CALLER --> TASK
    CALLER --> MODEL
    CALLER --> INV
    CALLER --> POOL
    POOL --> DB
    SAVER --> CHECKPOINT
    class TASK,MODEL,INV,MIG,POOL component
    class DB,CHECKPOINT durable
    class SAVER framework
    class CALLER external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
