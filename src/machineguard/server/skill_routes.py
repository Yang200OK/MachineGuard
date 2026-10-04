"""只读 Skill 能力与版本接口；工具执行保留在运行时服务内。"""

from fastapi import Depends, Request, Response

from .tool_runtime import fresh_principal, skill_summary, tool_schemas


def register_skill_routes(app, get_session, current, envelope):
    @app.get("/api/skills")
    def skills(request: Request, response: Response, principal=Depends(current), session=Depends(get_session)):
        principal = fresh_principal(session, principal)
        principal.require("devices:read")
        response.headers["Cache-Control"] = "no-store"
        registry = app.state.skill_registry
        return envelope(request, {"items": [skill_summary(registry, principal, name) for name in registry.active],
            "registry_sha256": registry.manifest_sha256, "max_tool_calls": 8,
            "execution_status": "agent_ready", "agent_selection_ready": True})

    @app.get("/api/skills/{name}")
    def skill(request: Request, response: Response, name: str, version: str | None = None,
              principal=Depends(current), session=Depends(get_session)):
        principal = fresh_principal(session, principal)
        principal.require("devices:read")
        response.headers["Cache-Control"] = "no-store"
        registry = app.state.skill_registry
        loaded = registry.load(name, version)
        summary = skill_summary(registry, principal, name, loaded.version)
        return envelope(request, {**summary, "version": loaded.version, "sha256": loaded.sha256,
            "body": loaded.body, "is_active_version": registry.active[name] == loaded.version,
            "tool_schemas": tool_schemas(summary["tools"])})
