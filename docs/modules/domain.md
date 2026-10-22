# 业务契约与确定性规则模块架构

定义模型动作、候选资源组合、批准与写入契约，确定性计算资源可用时间、数量、交付合法性和费用，并生成方案、证据与资源的规范摘要。

![业务契约与确定性规则模块架构](../diagrams/modules/module_domain.png)

[放大 SVG](../diagrams/modules/module_domain.svg) · [Mermaid 源码](../diagrams/modules/module_domain.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [contracts.py](../../src/order_agent/domain/contracts.py) | 任务、批准与 V1 兼容契约 |
| [decisions.py](../../src/order_agent/domain/decisions.py) | 互斥动作、资源分配与 V2 写入契约 |
| [adaptive_rules.py](../../src/order_agent/domain/adaptive_rules.py) | V2 数量、证据、日期与费用校验 |
| [scheduling.py](../../src/order_agent/domain/scheduling.py) | 规划起点与调拨可用日期 |
| [bindings.py](../../src/order_agent/domain/bindings.py) | 稳定摘要与审批绑定 |
| [errors.py](../../src/order_agent/domain/errors.py) | 领域异常类型 |

## 模块边界

规则不访问数据库、不发起 HTTP 或模型调用；Agent 校验和业务事务复核复用同一套规则。读取时间不参与资源身份摘要，日期时效单独检查。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 业务契约与确定性规则模块架构
CALLER["Agent 节点 / TaskService / 业务事务"]
    subgraph CORE["业务契约与规则 · 纯计算"]
        CONTRACT["contracts.py / decisions.py<br/>任务、动作、方案与写入契约"]
        PYDANTIC["Pydantic<br/>严格字段与动作互斥约束"]
        RULES["adaptive_rules.py<br/>数量、证据、时效与费用"]
        DATE["scheduling.py<br/>当前日期与运输可用时间"]
        BIND["bindings.py<br/>方案 / 证据 / 资源摘要"]
        ERR["errors.py<br/>领域异常类型"]
        CONTRACT --> PYDANTIC
        RULES --> CONTRACT
        RULES --> DATE
        BIND -->|"规范 JSON + SHA-256"| HASH["批准内容身份"]
    end
    CALLER --> CONTRACT
    CALLER --> RULES
    CALLER --> BIND
    CALLER --> ERR
    class CONTRACT,RULES,DATE,BIND,ERR,HASH component
    class PYDANTIC framework
    class CALLER external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
