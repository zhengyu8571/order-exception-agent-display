# HTTP 接口与应用装配模块架构

提供 FastAPI 任务接口、订单代理接口和进度订阅，完成身份校验、错误映射及依赖装配。应用生命周期分别管理 Checkpoint 连接池与其他进程级资源。

![HTTP 接口与应用装配模块架构](../diagrams/modules/module_transport.png)

[放大 SVG](../diagrams/modules/module_transport.svg) · [Mermaid 源码](../diagrams/modules/module_transport.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [app.py](../../src/order_agent/transport/app.py) | 生命周期、图和任务服务装配、静态资源 |
| [auth.py](../../src/order_agent/transport/auth.py) | 演示角色令牌与主管权限 |
| [task_routes.py](../../src/order_agent/transport/task_routes.py) | 任务接口、分页和 SSE |
| [order_routes.py](../../src/order_agent/transport/order_routes.py) | 订单代理与演示场景入口 |
| [errors.py](../../src/order_agent/transport/errors.py) | 领域错误到 HTTP 响应映射 |

## 模块边界

接口层处理身份和协议，任务流转交给 TaskService；SSE 从持久事件日志读取，不承担任务执行。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow HTTP 接口与应用装配模块架构
CLIENT["🌐 浏览器 / API 客户端"]
    subgraph CORE["HTTP 接口与应用装配 · FastAPI"]
        APP["app.py / lifespan<br/>依赖装配与资源关闭"]
        AUTH["auth.py<br/>身份与主管权限"]
        TASK["task_routes.py<br/>创建 / 审批 / 恢复 / SSE"]
        ORDER["order_routes.py<br/>订单查询与演示场景代理"]
        ERROR["errors.py<br/>领域异常映射"]
        APP --> TASK
        APP --> ORDER
        TASK --> AUTH
        ORDER --> AUTH
        APP --> ERROR
    end
    SERVICE["TaskService"]
    TOOLS["OrderTools"]
    SAVER["LangGraph PostgresSaver<br/>独立 Checkpoint 连接池"]
    CLIENT -->|"HTTP / SSE"| TASK
    CLIENT --> ORDER
    TASK --> SERVICE
    ORDER --> TOOLS
    APP -->|"构建 graph 并注入任务服务"| SAVER
    class APP,AUTH,TASK,ORDER,ERROR component
    class SAVER framework
    class CLIENT,SERVICE,TOOLS external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
