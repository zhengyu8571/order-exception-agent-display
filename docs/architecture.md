# 架构与一致性设计

OrderFlow 处理订单库存不足：从只读事实调查开始，经过合法性校验和主管批准，修改交付计划，最后核对订单与业务账本。系统由任务工作台、Agent API、独立订单模拟 API 和一个 PostgreSQL 组成。

![系统架构](diagrams/orderflow_architecture.png)

[放大 SVG](diagrams/orderflow_architecture.svg) · [可编辑 Mermaid](diagrams/orderflow_architecture.mmd)

## 模块与职责

各模块的组件图、调用关系与边界说明见 [分模块架构目录](modules/README.md)。

| 模块 | 职责 |
| --- | --- |
| [web/](modules/web.md) | 工作台状态、审批界面与可重连进度订阅 |
| [transport/](modules/transport.md) | FastAPI 路由、角色令牌、SSE 与生命周期装配 |
| [services/tasks.py](modules/services.md) | 请求驱动执行、任务锁、审批保存、显式 recover / replan |
| [agent/](modules/agent.md) | State / Reducer、图拓扑、V2 节点和补查汇合 |
| [domain/](modules/domain.md) | 决策结构、资源组合规则、摘要绑定和跨天时效 |
| [llm/](modules/llm.md) | OpenAI 兼容模型接入、连接复用、统一截止时间 |
| [integrations/](modules/integrations.md) | HTTP 业务工具、受限补查白名单 |
| [persistence/](modules/persistence.md) | SQL 迁移、任务和审批、模型与调查账本、事件日志 |
| [simulator/](modules/simulator.md) | 业务事实和模拟数据、事务内资源复核、计划更新与操作账本 |
| [evaluation/](modules/evaluation.md) | 固定合成语料、确定性基线、评分与费用口径 |

`api.py` / `business.py` 是兼容启动入口。工作台资源随源码目录运行，独立 wheel 不包含完整前端运行目录。

## V2 决策主线

1. 创建持久任务，工作台先取得 ID 并订阅事件，再显式请求推进；没有后台自动启动器。
2. LangGraph 调度订单、库存、采购、交付约束四路初始查询，用 Reducer 合并结果。
3. 模型选择白名单补查、候选提案或转人工。`Send` 按请求动态扇出，汇合节点集中合并证据和核对版本。
4. Pydantic 验证结构，确定性规则检查数量、证据、日期与分批许可；反馈循环受调用、调查与无进展预算约束。
5. 合法提案进入 `interrupt`，批准绑定 `revision` 和方案 / 证据 / 资源摘要。审批先提交数据库，再通过 `Command(resume=...)` 恢复。
6. 执行前再次读取业务事实与当前日期；审批等待不能抵扣运输时间。事实过期需重新调查和审批。
7. 使用稳定操作 ID 调用业务服务，查询实际订单与账本，一致才标记完成。

默认 V1 `demo` 是独立的确定性演示流程，不经过真实模型自主补查循环。V2 需要显式启用 `llm` 并提供凭据。

## 三类持久状态

- **LangGraph 框架表**：保存图状态、中断和 pending writes。框架负责调度与快照恢复。
- **agent schema**：保存任务、批准、模型调用、调查和持久事件。项目负责业务授权、预算及可观察性。
- **business schema**：保存订单、模拟资源和操作账本。业务服务负责事务原子性与重复请求核查。

同一个 PostgreSQL 不意味着这些操作处于一个跨服务事务。Checkpoint 不能证明外部 HTTP 写入是否发生。

## 外部写入协议

`operation_id = task_id:v{revision}:{candidate_id}:apply` 在同一已批准动作的恢复中保持稳定。

业务服务按该 ID 加事务 advisory lock，先查操作账本：相同参数返回已提交结果，不同参数拒绝。首次请求锁定订单和资源、检查版本与全部硬约束，再把计划更新、订单版本递增和操作结果一起提交。

HTTP 超时视为结果未知。恢复先按同一 ID 核查和重放，再对账，不能生成新 ID 盲目重做。此协议覆盖本模拟服务的计划写入，不承诺跨真实 ERP、仓储、供应商的分布式 exactly-once。

## 故障恢复

| 故障窗口 | 已提交记录 | 恢复依据 |
| --- | --- | --- |
| 等待批准时进程退出 | 图中断与候选方案 | 同 thread_id 恢复等待，不能自动批准 |
| 审批提交后、图恢复前退出 | 对应版本批准 | 读取原批准，再恢复中断 |
| 模型响应入账后、Checkpoint 前退出 | 同轮调用响应与输入摘要 | 输入一致时复用已知响应 |
| 并行查询某分支失败 | 成功分支 pending writes | 继续未完成分支 |
| 业务提交后、执行节点保存前退出 | 计划与操作账本 | 同 operation_id 核查和重放 |

进程退出后的继续执行需要显式 `recover`。SSE 只观察持久事件，不驱动业务恢复；事件 ID 分配前的同任务事务锁防止较大 ID 越过较小的未提交事件。

对应测试：[恢复](../tests/test_recovery.py)、[审批与副作用边界](../tests/test_acceptance_boundaries.py)、[资源绑定](../tests/test_resource_bindings.py)、[跨天规则](../tests/test_scheduling_boundaries.py)、[进度游标](../tests/test_progress_boundaries.py)。

## 业务流程图

![订单异常处理流程](diagrams/orderflow_workflow.png)

[放大 SVG](diagrams/orderflow_workflow.svg) · [Mermaid 源码](diagrams/orderflow_workflow.mmd)
