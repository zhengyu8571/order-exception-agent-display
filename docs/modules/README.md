# 模块架构

本页展示已实现模块的内部组件、调用关系与依赖边界。系统整体见 [总体架构](../architecture.md)。

| 模块架构 | 源码目录 |
| --- | --- |
| [任务工作台](web.md) | [web/](../../web/) |
| [HTTP 接口与应用装配](transport.md) | [src/order_agent/transport/](../../src/order_agent/transport/) |
| [任务与审批服务](services.md) | [src/order_agent/services/](../../src/order_agent/services/) |
| [Agent 图编排](agent.md) | [src/order_agent/agent/](../../src/order_agent/agent/) |
| [业务契约与确定性规则](domain.md) | [src/order_agent/domain/](../../src/order_agent/domain/) |
| [模型接入与调用治理](llm.md) | [src/order_agent/llm/](../../src/order_agent/llm/) |
| [业务工具与受限调查](integrations.md) | [src/order_agent/integrations/](../../src/order_agent/integrations/) |
| [持久化与数据库访问](persistence.md) | [src/order_agent/persistence/](../../src/order_agent/persistence/) |
| [独立订单模拟服务](simulator.md) | [src/order_agent/simulator/](../../src/order_agent/simulator/) |
| [决策评测](evaluation.md) | [src/order_agent/evaluation/](../../src/order_agent/evaluation/) |

蓝色为项目组件，紫色为框架机制或依赖，绿色为持久存储，灰色为当前模块之外的组件。连线含义以标签和组件关系为准；不同模块不等于独立部署进程，当前仅 Agent API 和订单模拟 API 分进程运行。

每张图提供 PNG、SVG 与 Mermaid 源码；连线标签统一加引号，兼容 Mermaid 10.1.0。模块图中的 V2 决策需要真实模型配置，默认 V1 演示使用确定性候选生成。
