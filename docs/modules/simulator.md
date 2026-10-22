# 独立订单模拟服务模块架构

通过独立 FastAPI 进程提供订单事实、资源调查和计划写入接口。写入时按操作 ID 核查账本，在业务事务内锁定订单与资源、复核批准条件，再同时提交计划和操作结果。

![独立订单模拟服务模块架构](../diagrams/modules/module_simulator.png)

[放大 SVG](../diagrams/modules/module_simulator.svg) · [Mermaid 源码](../diagrams/modules/module_simulator.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [api.py](../../src/order_agent/simulator/api.py) | 服务身份校验、业务查询与写入接口 |
| [service.py](../../src/order_agent/simulator/service.py) | 业务查询、方案写入、操作账本与事务 |
| [resources.py](../../src/order_agent/simulator/resources.py) | 独立版本资源、锁定读取与场景变更 |
| [planning.py](../../src/order_agent/simulator/planning.py) | 模拟规划快照和概览投影 |
| [contracts.py](../../src/order_agent/simulator/contracts.py) | 场景与资源变更契约 |

## 模块边界

修改范围是模拟订单交付计划，不扣减或预留共享库存，不代表真实采购、调拨或出库。业务事务独立于 Agent Checkpoint。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 独立订单模拟服务模块架构
CLIENT["🔌 OrderTools / HTTP 调用方"]
    subgraph CORE["订单模拟服务 · FastAPI :8101"]
        API["api.py<br/>服务令牌与请求契约"]
        READ["service.py<br/>订单、资源与操作查询"]
        WRITE["service.py<br/>计划写入与幂等核查"]
        RESOURCE["resources.py<br/>独立版本资源与锁定读取"]
        PLANNING["planning.py / contracts.py<br/>场景构造与规划投影"]
        TX["业务事务<br/>操作锁、乐观锁与原子提交"]
        API --> READ
        API --> WRITE --> TX
        READ --> RESOURCE
        TX --> RESOURCE
        API --> PLANNING
    end
    DOMAIN["domain 业务规则<br/>最新日期、证据与摘要复核"]
    DB[("💾 business schema<br/>订单 / 资源 / 操作账本")]
    CLIENT -->|"HTTP / JSON"| API
    TX --> DOMAIN
    TX -->|"计划更新与账本一起提交"| DB
    READ --> DB
    class API,READ,WRITE,RESOURCE,PLANNING,TX component
    class DOMAIN,CLIENT external
    class DB durable
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
