# 任务工作台模块架构

组织任务列表、调查证据、候选方案、主管审批与恢复操作。页面状态和进度订阅相互配合，慢请求期间仍可展示节点进度。

![任务工作台模块架构](../diagrams/modules/module_web.png)

[放大 SVG](../diagrams/modules/module_web.svg) · [Mermaid 源码](../diagrams/modules/module_web.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [app.js](../../web/app.js) | 页面状态、交互与刷新调度 |
| [task-actions.js](../../web/task-actions.js) | 先取得任务 ID，再订阅与启动 |
| [api.js](../../web/api.js) | 携带身份令牌的 HTTP 请求 |
| [progress.js](../../web/progress.js) | SSE 帧解析、游标去重与重连 |
| [render.js](../../web/render.js) | 任务列表与详情渲染 |
| [approval.js](../../web/approval.js) | 审批动作摘要与恢复入口 |
| [decision.js](../../web/decision.js) | 决策过程展示 |

## 模块边界

工作台发起业务请求并展示服务端状态；SSE 重连只恢复观察，不重新批准或重做业务操作。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 任务工作台模块架构
subgraph CORE["任务工作台 · 原生 JavaScript"]
        APP["app.js<br/>页面状态与交互调度"]
        ACTION["task-actions.js<br/>创建任务后显式启动"]
        API["api.js<br/>身份令牌与 JSON 请求"]
        RENDER["render.js<br/>任务列表与详情"]
        CARDS["approval.js / decision.js<br/>审批摘要与决策展示"]
        PROGRESS["progress.js<br/>游标去重与断线重连"]
        APP --> ACTION --> API
        APP --> API
        APP --> RENDER --> CARDS
        APP --> PROGRESS
    end
    SERVER["🔌 Agent API<br/>任务与持久事件"]
    API -->|"HTTP / JSON"| SERVER
    PROGRESS -->|"携带 after 游标订阅"| SERVER
    SERVER -.->|"SSE 进度事件"| PROGRESS
    PROGRESS -.->|"更新事件与任务详情"| APP
    class APP,ACTION,API,RENDER,CARDS,PROGRESS component
    class SERVER external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
