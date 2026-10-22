# 业务工具与受限调查模块架构

把业务工具调用转换为独立订单服务的 HTTP 契约，并限制模型可请求的只读调查工具、参数和资源范围。查询与批准后的执行共用客户端适配，写入不暴露给模型工具目录。

![业务工具与受限调查模块架构](../diagrams/modules/module_integrations.png)

[放大 SVG](../diagrams/modules/module_integrations.svg) · [Mermaid 源码](../diagrams/modules/module_integrations.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [investigation.py](../../src/order_agent/integrations/investigation.py) | TOOL_CATALOG、参数白名单、资源范围与只读路径 |
| [order_client.py](../../src/order_agent/integrations/order_client.py) | OrderTools、身份令牌、HTTP 错误映射与操作查询 |

## 模块边界

模型不能传入任意 URL、SQL 或其他订单 ID。HTTP 超时和业务冲突映射为明确异常，结果未知时由上层按稳定操作身份核查。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 业务工具与受限调查模块架构
MODEL["模型提出的调查请求"]
    EXEC["Agent 程序执行节点<br/>批准并复核后调用"]
    subgraph CORE["业务工具集成模块"]
        CATALOG["TOOL_CATALOG<br/>路线 / 批次 / 客户政策"]
        CHECK["InvestigationTools.validate<br/>参数与本订单资源范围"]
        INVOKE["InvestigationTools.invoke<br/>固定只读 GET 路径"]
        CLIENT["OrderTools.request<br/>令牌、超时与异常映射"]
        APPLY["OrderTools.apply_adaptive<br/>批准后的计划写入"]
        QUERY["OrderTools.operation<br/>按操作 ID 核查"]
        CHECK --> CATALOG
        CHECK --> INVOKE --> CLIENT
        APPLY --> CLIENT
        QUERY --> CLIENT
    end
    BIZ["🔌 独立订单模拟 API"]
    MODEL --> CHECK
    EXEC --> APPLY
    EXEC --> QUERY
    CLIENT -->|"HTTP / JSON"| BIZ
    class CATALOG,CHECK,INVOKE,CLIENT,APPLY,QUERY component
    class MODEL,EXEC,BIZ external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
