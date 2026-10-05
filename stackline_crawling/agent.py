"""Compatibilidad de imports para el agente de workspace.

La implementación activa vive en workspace_agent.py. Este módulo ya no envía
crawl output a la IA para normalización.
"""

from .workspace_agent import AgentExecutionError

__all__ = ["AgentExecutionError"]
