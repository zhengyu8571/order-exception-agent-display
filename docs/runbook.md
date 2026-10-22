# 本地运行、测试与显式恢复

## 环境与启动

建议 Python 3.12，安装依赖使用 `uv sync --frozen`；Windows 可直接运行 `scripts/setup.ps1` 安装工具、依赖并启动项目专用 PostgreSQL。两个 API 默认使用 8100 / 8101，便携数据库使用 55432。多个目录的实例不能同时使用这些端口。

从本仓库根目录执行：

```powershell
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
.\.venv\Scripts\python.exe scripts/dev.py start
```

`.env.example` 中的数据库密码和静态角色令牌仅用于本机模拟。真实模型密钥写入忽略的 `.env`。

停止本目录管理的服务和数据库：

```powershell
.\.venv\Scripts\python.exe scripts/dev.py stop
.\.runtime\pgsql\bin\pg_ctl.exe -D .runtime/pgdata stop
```

Docker 路径见 [首页](../README.md)。Linux / macOS 源码运行需自行准备 PostgreSQL 和 `DATABASE_URL`，不能使用 Windows 便携数据库脚本。

## 启用真实模型

在忽略的 `.env` 中设置 `PLANNER_MODE=llm`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL`；DashScope 参考 [.env.dashscope.example](../.env.dashscope.example)，将字段合并至基础配置。

修改后重启 API。缺凭据、模型请求失败、超时或预算耗尽会按错误 / 受阻边界处理，不回退固定答案。默认离线测试不调用付费模型；`scripts/live_model_acceptance.py` 与决策评测的 `--live` 会实际请求外部供应商。

## 操作与恢复

| 接口 | 作用 |
| --- | --- |
| `POST /api/tasks` | 同步创建并推进至审批或错误边界 |
| `POST /api/tasks?defer_start=true` | 只创建 CREATED，返回 ID 后再由请求启动 |
| `POST /api/tasks/{id}/approval` | 主管按对应版本批准或拒绝 |
| `POST /api/tasks/{id}/recover` | 显式推进或恢复未完成任务 |
| `POST /api/tasks/{id}/replan` | 重新调查，产生新方案与审批边界 |
| `GET /api/tasks/{id}/events?after=...` | 读取已提交事件 |
| `GET /api/tasks/{id}/stream?after=...` | SSE 订阅并支持游标补读 |

恢复时保留 task_id / thread_id。等待审批的 recover 不会自动批准；旧批准不能用于新方案。业务超时不代表未提交，先查操作账本。恢复不应删除 pgdata、Checkpoint 或账本。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not integration and not live_llm"
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy
npm --prefix web ci --ignore-scripts
npm --prefix web run check
npm --prefix web run format:check
npm --prefix web test
```

完整验收先启动 PostgreSQL，再运行：

```powershell
$env:TEST_DATABASE_URL = "postgresql://order_agent:order_agent_local@127.0.0.1:55432/order_agent_test"
.\.venv\Scripts\python.exe scripts/acceptance.py
```

测试数据库名必须以 `_test` 结尾，默认自动创建 `order_agent_test`。迁移测试会创建 / 删除随机专用 `_test` 库，需要相应权限；不清空演示库。验收占用 18100 / 18101，使用真实子进程并注入退出和超时故障。

验收结果输出到 `docs/acceptance-latest.json` 与 `.runtime/acceptance.xml`。默认离线验收不会调用真实模型。

故障开关正常启动为 false。完整故障注入只对独立测试实例运行。当前项目为单工作空间模拟，静态角色令牌不是完整企业身份系统。
