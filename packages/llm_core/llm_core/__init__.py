"""Shared LLM core for OpenAIR (U-05).

The provider-neutral layer shared by the API process and the container worker:
- ``token_usage``: provider-neutral usage normalization + wire/normalized
  conversion + the request-scoped usage collector.
- ``openai_compatible_client``: thin HTTP transport for OpenAI-compatible
  chat-completions / responses calls.
- ``claude_turn``: shared Claude Agent SDK turn (non-stream + stream).

These modules depend only on each other + stdlib + httpx (+ claude_agent_sdk,
imported lazily). They do NOT depend on ``app.*`` so the package is installable
in both the API venv and the worker image without dragging in the API app.

Importers use the full module path, e.g. ``from llm_core.token_usage import
to_wire_usage``.
"""
