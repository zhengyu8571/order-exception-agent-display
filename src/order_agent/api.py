"""Stable uvicorn entry point for the Agent HTTP service."""

from order_agent.transport.app import app

__all__ = ["app"]
