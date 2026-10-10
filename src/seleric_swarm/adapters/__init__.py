"""Adapters (L3): concrete implementations of ports over external services.

Each module wraps one external system (MCP, LLM, Postgres, Langfuse) behind the
port the domain logic depends on, so the domain never imports an SDK directly.
"""
