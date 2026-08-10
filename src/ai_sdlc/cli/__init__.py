"""ai-sdlc CLI: a thin client for the Core Platform HTTP API.

This package never imports from `ai_sdlc.orchestration` or `ai_sdlc.agents`
(except indirectly via `ai_sdlc.platform.server`'s standalone module, which
is only ever shelled out to as a subprocess -- see `bootstrap.spawn_server`).
Every command in `main.py` talks to Core's REST API over HTTP via
`client.PlatformClient`.
"""
