"""Compatibility imports for persistence helpers."""

from order_agent.persistence.task_repository import (
    consume_fault,
    event,
    set_status,
    task_lock,
    task_row,
)

__all__ = ["consume_fault", "event", "set_status", "task_lock", "task_row"]
