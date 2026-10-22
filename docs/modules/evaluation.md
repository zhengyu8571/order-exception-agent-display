# 决策评测模块架构

使用固定合成语料与确定性参照，对比规则基线和 Agent 决策，记录合法性、覆盖率、延迟、调用与费用口径。Agent 路径复用生产图和模型适配，仅运行到审批或停止边界。

![决策评测模块架构](../diagrams/modules/module_evaluation.png)

[放大 SVG](../diagrams/modules/module_evaluation.svg) · [Mermaid 源码](../diagrams/modules/module_evaluation.mmd) · [模块目录](README.md)

## 主要组件

| 组件 | 职责 |
| --- | --- |
| [cases.json](../../src/order_agent/evaluation/cases.json) | 固定合成语料 |
| [dataset.py](../../src/order_agent/evaluation/dataset.py) | 语料加载、摘要与相对日期实例化 |
| [harness.py](../../src/order_agent/evaluation/harness.py) | 规则与 Agent 的统一评测入口 |
| [baseline.py](../../src/order_agent/evaluation/baseline.py) | 确定性规则基线 |
| [strategies.py](../../src/order_agent/evaluation/strategies.py) | 有限资源枚举参照 |
| [scoring.py](../../src/order_agent/evaluation/scoring.py) | 逐例判分、汇总与费用口径 |
| [holdout.py](../../src/order_agent/evaluation/holdout.py) | 冻结清单与多轮结果比较 |
| [tools.py](../../src/order_agent/evaluation/tools.py) | 基于案例的只读工具替身 |

## 模块边界

评测不批准或修改业务订单。脚本化响应验证机制，真实模型批次需显式运行；失败保留在分母中，未知 usage 和费用不能按零计，也不预设 Agent 优于规则。

<details>
<summary>查看 Mermaid 图源码</summary>

```mermaid
flowchart TB
    accTitle: OrderFlow 决策评测模块架构
ENTRY["评测脚本 / 显式 live 入口"]
    subgraph CORE["决策评测模块"]
        DATA["cases.json / dataset.py<br/>固定语料与日期实例化"]
        HARNESS["harness.py<br/>统一评测编排"]
        BASE["baseline.py / strategies.py<br/>规则基线与有界枚举参照"]
        TOOLS["tools.py / FixtureTools<br/>案例只读工具"]
        SCORE["scoring.py<br/>分母、失败、延迟与费用"]
        HOLD["holdout.py<br/>源码、语料冻结与轮次比较"]
        HARNESS --> DATA
        HARNESS --> BASE
        HARNESS --> TOOLS
        HARNESS --> SCORE
        HOLD --> HARNESS
    end
    GRAPH["生产 Agent 图与模型适配<br/>仅推进至审批或停止边界"]
    REPORT["📊 逐例结果与聚合指标"]
    ENTRY --> HARNESS
    ENTRY --> HOLD
    HARNESS --> GRAPH
    GRAPH -->|"注入案例工具"| TOOLS
    SCORE --> REPORT
    class DATA,HARNESS,BASE,TOOLS,SCORE,HOLD component
    class ENTRY,GRAPH,REPORT external
classDef component fill:#e0f2fe,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef framework fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#3b0764
    classDef durable fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d
    classDef external fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#334155
```

</details>
