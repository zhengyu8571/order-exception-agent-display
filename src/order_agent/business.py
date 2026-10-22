"""Stable uvicorn entry point for the simulated business service."""

from order_agent.simulator.api import app

__all__ = ["app"]
