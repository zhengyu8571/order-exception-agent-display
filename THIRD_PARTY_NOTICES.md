# Source provenance and attribution

This project selectively adapts the FastAPI lifespan / pooled PostgreSQL saver
scaffolding pattern from **agent-service-toolkit**. It does not vendor the full
repository or its sample agents, chat application, RAG, or long-term memory.

- Upstream: https://github.com/JoshuaC215/agent-service-toolkit
- Reference commit: `58f6a059f9787db1a68946030b35dd61023662fa`
- Reference commit date: 2026-10-18
- Referenced files: `src/service/service.py`, `src/memory/postgres.py`
- Adapted local area: `src/order_agent/transport/app.py::lifespan`
  (the original `src/order_agent/api.py` now only re-exports the application).
- Upstream license: MIT; retained in `licenses/agent-service-toolkit-MIT.txt`.

Aegra (https://github.com/aegra/aegra) was consulted for architectural study.
No Aegra source, Redis queue, Worker, lease, or recovery daemon is included.

LangGraph provides graph scheduling, reducers, checkpoint storage and interrupt
resume. Those are dependency capabilities, not implementations authored here.
The project defines domain state, deterministic validation, approval binding,
HTTP tool contracts, transactional business idempotency, optimistic concurrency,
the task dashboard and fault-injection acceptance tests.
