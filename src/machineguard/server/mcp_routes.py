"""网站本人 MCP 凭据与审计管理，不通过 MCP 暴露管理操作。"""

from uuid import UUID
import sys
from machineguard.paths import get_project_root
from fastapi import Depends, Query, Request, Response
from .mcp_contract import CredentialRequest, MCP_TOOLS, has_tool_permission
from . import mcp_service as service


def register_mcp_routes(app, get_session, current, envelope):
    @app.get("/api/mcp/config")
    def config(request: Request, response: Response, principal=Depends(current)):
        service.formal_account(principal); response.headers["Cache-Control"] = "no-store"
        return envelope(request, {"resource_url": app.state.mcp_config.resource_url,
            "stdio_command": sys.executable, "stdio_args": [str(get_project_root() / "scripts/serve_mcp.py")],
            "max_calls_per_minute": app.state.mcp_config.max_calls_per_minute,
            "tools": [{"name": name, "description": tool.description, "writes_prediction": tool.writes}
                      for name, tool in MCP_TOOLS.items() if has_tool_permission(principal, name)]})

    @app.post("/api/mcp/credentials")
    def issue(request: Request, response: Response, payload: CredentialRequest,
              principal=Depends(current), session=Depends(get_session)):
        result = service.issue_credential(session, principal, payload, app.state.mcp_config, request.state.request_id)
        session.commit(); response.headers["Cache-Control"] = "no-store"
        return envelope(request, result)

    @app.get("/api/mcp/credentials")
    def listing(request: Request, response: Response, offset: int = Query(0, ge=0),
                principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.list_credentials(session, principal, offset))

    @app.post("/api/mcp/credentials/{credential_id}/revoke")
    def revoke(request: Request, response: Response, credential_id: UUID,
               principal=Depends(current), session=Depends(get_session)):
        result = service.revoke_credential(session, principal, credential_id, request.state.request_id)
        session.commit(); response.headers["Cache-Control"] = "no-store"
        return envelope(request, result)

    @app.get("/api/mcp/activity")
    def activity(request: Request, response: Response, offset: int = Query(0, ge=0),
                 principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.activity(session, principal, offset))
