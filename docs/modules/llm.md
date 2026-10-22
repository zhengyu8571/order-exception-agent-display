# 模型接入与调用治理模块架构

构造结构化决策请求，固定任务模型配置，预留调用预算并复用已入账响应。传输层通过 LangChain ChatOpenAI 对接兼容接口，复用进程级 HTTP 连接并共享调用截止时间。

![模型接入与调用治理模块架构](../diagrams/modules/module_llm.png)

[放大 SVG](../diagrams/modules/module_llm.svg) · [Mermaid 源码](../diagrams/modules/module_llm.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [decision_model.py](../../src/order_agent/llm/decision_model.py) | DecisionModel、JSON 契约、预算准入与结果入账 |
| [transport.py](../../src/order_agent/llm/transport.py) | ChatOpenAI 请求、流响应与统一截止时间 |
| [pool.py](../../src/order_agent/llm/pool.py) | 进程级 HTTP 客户端和专用事件循环 |
| [planner.py](../../src/order_agent/llm/planner.py) | V1 确定性候选生成与兼容入口 |
| [model_repository.py](../../src/order_agent/persistence/model_repository.py) | 预算和同轮响应重放账本 |

## 模块边界

模型没有业务写权限。模型失败不回退演示答案；未知 usage 保留预留量，同轮响应只在输入摘要一致时复用，不承诺供应商侧只调用或计费一次。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 模型接入与调用治理模块架构
NODE["Agent V2 决策节点"]
    subgraph CORE["模型接入模块"]
        DECIDE["decision_model.py<br/>Prompt、输入摘要与准入"]
        TRANSPORT["transport.py<br/>统一截止时间与流响应处理"]
        SDK["LangChain ChatOpenAI<br/>OpenAI 兼容协议"]
        POOL["pool.py<br/>复用 HTTP 客户端与事件循环"]
        PARSE["AgentDecision<br/>JSON 结构与动作校验"]
        DECIDE --> TRANSPORT --> SDK
        TRANSPORT --> POOL
        DECIDE --> PARSE
    end
    LEDGER["💾 model_repository<br/>预算预留 / 响应复用 / usage 入账"]
    PROVIDER["☁️ 外部模型供应商"]
    NODE --> DECIDE
    DECIDE -->|"reserve / finish"| LEDGER
    SDK -->|"使用复用客户端请求"| PROVIDER
    class DECIDE,TRANSPORT,POOL,PARSE component
    class SDK framework
    class NODE,PROVIDER,LEDGER external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
