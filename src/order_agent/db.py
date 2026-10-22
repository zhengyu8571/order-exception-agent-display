"""Compatibility exports; schema arguments now identify a migration component."""

from order_agent.persistence.database import connection
from order_agent.persistence.migrations import apply_migrations as initialize

BUSINESS_SCHEMA = "business"
AGENT_SCHEMA = "agent"
__all__ = ["connection", "initialize", "BUSINESS_SCHEMA", "AGENT_SCHEMA"]
