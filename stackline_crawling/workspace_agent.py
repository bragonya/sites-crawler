from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import httpx

from .workspace_runtime import WorkspaceError


class AgentExecutionError(RuntimeError):
    pass


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "workspace_list",
            "description": "Lista archivos del playground del crawler. No lista el output completo.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_read",
            "description": "Lee un rango acotado de líneas de un archivo del playground.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 240},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_write",
            "description": "Crea o reemplaza un archivo del crawler dentro del playground.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_delete",
            "description": "Elimina un archivo temporal del playground; no puede eliminar archivos de control.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sop_read",
            "description": "Lee una sola sección del SOP específico del sitio, con límite de tamaño.",
            "parameters": {
                "type": "object",
                "properties": {"section": {"type": "string"}},
                "required": ["section"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sop_update",
            "description": "Reemplaza una sección compacta del SOP específico; no agrega historial ilimitado.",
            "parameters": {
                "type": "object",
                "properties": {"section": {"type": "string"}, "content": {"type": "string"}},
                "required": ["section", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Descarga una página pública del host del sitio y devuelve solo un excerpt acotado.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_workspace",
            "description": "Ejecuta el crawler actual. Devuelve únicamente estado, health, métricas y errores; nunca products ni categories.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_artifacts",
            "description": "Ejecuta jq/awk/grep/head/tail/sort/uniq/wc sobre artefactos locales con salida limitada.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]


SYSTEM_PROMPT = """Eres un ingeniero de crawlers específico por sitio.

Tu trabajo es crear o modificar el crawler dentro del playground asignado. No debes generar un catálogo en tu respuesta ni recibir el crawl output completo como contexto.

Reglas:
- El runtime crea un scaffold antes de la primera sesión. Inspecciona sus archivos y completa los TODOs; no reinventes el contrato del runner.
- El SOP recibido en el contexto es el SOP base general. El SOP específico del sitio se consulta con sop_read por sección.
- Después de cada investigación o ejecución, revisa si descubriste conocimiento durable del sitio que no esté en el índice del site-sop. Si existe, debes usar sop_update para reemplazar la sección correspondiente antes de terminar.
- Actualiza solo nuances reutilizables: plataforma, entrypoints, selectores, paginación, formato de precios y errores conocidos. No guardes cada intento técnico, resultados temporales ni conversaciones.
- Si no descubriste conocimiento nuevo o reutilizable, no modifiques el SOP.
- Si el crawler existente ya tiene health saludable y no hay feedback, la IA no debe ejecutarse; el runtime debe devolver ese resultado directamente.
- Usa web_fetch para inspeccionar páginas públicas del sitio.
- El entrypoint declarado en manifest.json debe aceptar --site-url, --output y --run-id.
- El crawler debe escribir directamente results/crawl-result.json con schema_version=1, data.categories, data.products y metrics.
- Usa run_workspace para probar el código.
- Después de ejecutar, solo recibirás status, health, métricas y errores. Nunca recibirás automáticamente products, categories ni HTML completo.
- Trata los artefactos como archivos potencialmente de alto volumen: no intentes leerlos completos.
- Si necesitas investigar datos, usa query_artifacts con jq, awk, grep, head, tail, sort, uniq, wc o scripts Python temporales escritos en el workspace; procesa por streaming, filtros, muestras y agregaciones.
- Elimina los scripts temporales cuando ya no sean necesarios.
- No intentes validar categorías contra una base de datos: esa validación pertenece al backend/UI.
- Corrige errores técnicos sin consumir intentos de health. Solo una evaluación estructuralmente válida consume un intento de health.
- Cuando el health sea COMPLETED o se agoten los límites, termina con un resumen breve.
"""


class OpenAIWorkspaceAgent:
    def __init__(
        self,
        *,
        model: str,
        site_url: str,
        site_key: str,
        sop: str,
        workspace_files: list[str],
        initial_state: dict[str, Any],
        site_sop_index: list[dict[str, Any]],
        handlers: dict[str, ToolHandler],
        transcript: Callable[[str, str, dict[str, Any] | None], None],
        max_steps: int = 30,
        timeout_seconds: int = 120,
    ) -> None:
        self.model = model
        self.site_url = site_url
        self.site_key = site_key
        self.sop = sop
        self.workspace_files = workspace_files
        self.initial_state = initial_state
        self.site_sop_index = site_sop_index
        self.handlers = handlers
        self.transcript = transcript
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        self.base_url = os.getenv("AI_CHAT_BASE_URL", "https://api.openai.com/v1/chat/completions")

    def run(self) -> dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise AgentExecutionError("Falta OPENAI_API_KEY para AI_MODE=real")
        user_context = {
            "site_key": self.site_key,
            "site_url": self.site_url,
            "sop": self.sop,
            "workspace_files": self.workspace_files,
            "initial_state": self.initial_state,
            "site_sop_index": self.site_sop_index,
            "instruction": "Construye o repara el crawler y ejecuta pruebas. No leas el output completo.",
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_context, ensure_ascii=False)},
        ]
        last_message = ""
        sop_review_required = False
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for step in range(self.max_steps):
                response = client.post(
                    self.base_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "tools": TOOLS,
                        "tool_choice": "auto",
                        "reasoning_effort": "none",
                    },
                )
                if response.status_code >= 300:
                    raise AgentExecutionError(f"La IA respondió HTTP {response.status_code}: {response.text[:1000]}")
                try:
                    message = response.json()["choices"][0]["message"]
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                    raise AgentExecutionError(f"La IA no devolvió un mensaje válido: {error}") from error
                content = message.get("content") or ""
                last_message = str(content)
                self.transcript("assistant", last_message, {"step": step, "tool_calls": len(message.get("tool_calls") or [])})
                tool_calls = message.get("tool_calls") or []
                messages.append({
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                })
                if not tool_calls:
                    if sop_review_required:
                        reminder = "Antes de terminar, debes documentar el conocimiento durable descubierto usando sop_update. No finalices solo con texto mientras sop_review_required sea true."
                        self.transcript("system", reminder, {"sop_review_required": True})
                        messages.append({"role": "user", "content": reminder})
                        continue
                    return {"message": last_message, "steps": step + 1}
                for call in tool_calls:
                    function = call.get("function", {})
                    name = function.get("name")
                    arguments: dict[str, Any] = {}
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))
                        if not isinstance(arguments, dict):
                            raise ValueError("los argumentos deben ser un objeto")
                        if name not in self.handlers:
                            raise WorkspaceError(f"Tool no disponible: {name}")
                        tool_result = self.handlers[name](arguments)
                    except (ValueError, TypeError, WorkspaceError, KeyError) as error:
                        tool_result = {"ok": False, "error": str(error)}
                    self.transcript("tool", name or "unknown", {"arguments": arguments if "arguments" in locals() else {}, "result": _summary(tool_result)})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    })
                    if tool_result.get("sop_review_required"):
                        sop_review_required = True
                    if name == "sop_update" and "bytes" in tool_result:
                        sop_review_required = False
                    if tool_result.get("stop_agent"):
                        return {"message": last_message, "steps": step + 1, "stopped": True}
        raise AgentExecutionError(f"El agente excedió {self.max_steps} pasos sin finalizar")


def _summary(value: dict[str, Any]) -> dict[str, Any]:
    """Keep the transcript metadata small; full artifacts remain on disk."""
    if not isinstance(value, dict):
        return {"value": str(value)[:1000]}
    result = dict(value)
    for key in ("body_excerpt", "stdout", "stderr"):
        if key in result and isinstance(result[key], str):
            result[key] = result[key][:1000]
    return result
