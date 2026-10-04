"""官方 SDK 的 stdio/HTTP 适配；逐个请求重新校验身份。"""

import json
import logging
import re
from uuid import uuid4

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from starlette.responses import JSONResponse
from machineguard.server.auth_service import ServiceError
from machineguard.server.mcp_contract import MCP_TOOLS

LOGGER = logging.getLogger("machineguard.mcp")
CAPABILITY_URI = "machineguard://capabilities"
REPORT_TEMPLATE = "machineguard://reports/{report_id}"


def protocol_error(result):
    error = result["error"]
    return MCPError(code=-32001 if error["status"] == 401 else -32000,
                    message=error["message"], data={"request_id": result.get("request_id"), "code": error["code"]})


def create_mcp_server(runtime, *, stdio_token=None):
    def credential(ctx):
        if stdio_token is not None:
            return stdio_token
        if ctx.request is None:
            raise ServiceError(401, "MCP_CREDENTIAL_REQUIRED", "HTTP MCP 调用缺少请求身份。")
        supplied = ctx.request.headers.get("authorization", "")
        if not supplied.startswith("Bearer "):
            raise ServiceError(401, "MCP_CREDENTIAL_REQUIRED", "请提供独立 MCP Bearer 凭据。")
        return supplied[7:]

    async def perform(ctx, operation, arguments):
        return await anyio.to_thread.run_sync(lambda: runtime.execute(credential(ctx), operation, arguments,
            "stdio" if stdio_token is not None else "streamable_http", method=ctx.method))

    async def capabilities(ctx, params=None):
        if params is not None and getattr(params, "cursor", None) is not None:
            raise MCPError(code=-32602, message="能力列表没有续页游标。")
        result = await perform(ctx, "capabilities", {})
        if "error" in result:
            raise protocol_error(result)
        return result["data"]

    async def list_tools(ctx, params):
        data = await capabilities(ctx, params)
        return types.ListToolsResult(tools=[types.Tool(name=item["name"], description=item["description"],
            inputSchema=item["inputSchema"], annotations=types.ToolAnnotations(readOnlyHint=not item["writes_prediction"],
                destructiveHint=False, idempotentHint=True,
                openWorldHint=item["name"] == "search_maintenance_knowledge")) for item in data["tools"]])

    async def call_tool(ctx, params):
        result = await perform(ctx, params.name, params.arguments or {})
        return types.CallToolResult(isError="error" in result, structuredContent=result,
            content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, allow_nan=False))])

    async def list_resources(ctx, params):
        await capabilities(ctx, params)
        return types.ListResourcesResult(resources=[types.Resource(uri=CAPABILITY_URI, name="受控能力说明", mimeType="application/json")])

    async def list_templates(ctx, params):
        data = await capabilities(ctx, params)
        return types.ListResourceTemplatesResult(resourceTemplates=[types.ResourceTemplate(uriTemplate=REPORT_TEMPLATE,
            name="授权正式报告", mimeType="application/json")] if data["reports_available"] else [])

    async def read_resource(ctx, params):
        uri = str(params.uri)
        if uri == CAPABILITY_URI:
            result = await perform(ctx, "capabilities", {})
        else:
            match = re.fullmatch(r"machineguard://reports/([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})", uri)
            result = await perform(ctx, "read_report" if match else "unregistered_resource",
                                   {"report_id": match.group(1)} if match else {"uri": uri})
        if "error" in result:
            raise protocol_error(result)
        return types.ReadResourceResult(contents=[types.TextResourceContents(uri=params.uri, mimeType="application/json",
            text=json.dumps(result, ensure_ascii=False, allow_nan=False))])

    async def list_prompts(ctx, params):
        data = await capabilities(ctx, params)
        names = {tool["name"] for tool in data["tools"]}
        return types.ListPromptsResult(prompts=[types.Prompt(name="evidence-path-analysis", description="按已审核方向查询最多两跳，并逐条核对原文。")]
            if "search_evidence_paths" in names else [])

    async def get_prompt(ctx, params):
        data = await capabilities(ctx)
        if (params.name != "evidence-path-analysis" or params.arguments
                or not any(item["name"] == "search_evidence_paths" for item in data["tools"])):
            raise MCPError(code=-32602, message="提示词不存在、未授权或携带未知参数。")
        return types.GetPromptResult(description="有界证据路径操作说明", messages=[types.PromptMessage(role="user",
            content=types.TextContent(type="text", text="请先明确原文中的起点及可选终点实体名；调用 search_evidence_paths，max_hops 只能为1或2。不同文档同名时澄清，不合并。按每条已审核边列出固定原文、版本与页面。没有路径明确说明；不得推断新关系、因果或现场故障。提示词不授予工具权限。"))])

    server = Server("MachineGuard", version="0.1.0", instructions="仅调用已授权业务能力；数据、引用和预测以服务器返回为准。",
        on_list_tools=list_tools, on_call_tool=call_tool, on_list_resources=list_resources,
        on_list_resource_templates=list_templates, on_read_resource=read_resource,
        on_list_prompts=list_prompts, on_get_prompt=get_prompt,
        get_tool_input_schema=lambda name: MCP_TOOLS[name].arguments.model_json_schema() if name in MCP_TOOLS else None)

    async def authorize(ctx, call_next):
        try:
            await anyio.to_thread.run_sync(lambda: runtime.verify(credential(ctx)))
        except ServiceError as error:
            raise protocol_error({"error": {"status": error.status, "code": error.code, "message": error.message}}) from None
        return await call_next(ctx)

    # 不启用外部遥测；项目自己的数据库审计记录是调用追溯依据。
    server.middleware.clear()
    server.middleware.append(authorize)
    return server


class AuthenticatedHttp:
    """进入协议解析前验证 HTTP 凭据；模型参数或会话编号不能作为身份。"""
    def __init__(self, app, runtime):
        self.app, self.runtime = app, runtime

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = [value.decode("latin-1") for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
        request_id = str(scope.get("state", {}).get("request_id", uuid4()))
        try:
            if scope.get("query_string") or len(headers) != 1 or not headers[0].startswith("Bearer "):
                raise ServiceError(401, "MCP_CREDENTIAL_REQUIRED", "请通过 Authorization 提供独立 MCP 凭据，不接受 URL 凭据。")
            await anyio.to_thread.run_sync(lambda: self.runtime.verify(headers[0][7:]))
        except ServiceError as error:
            LOGGER.info("mcp_auth_denied request_id=%s code=%s", request_id, error.code)
            response = JSONResponse({"request_id": request_id, "error": {"code": error.code, "message": error.message}},
                status_code=error.status, headers={"Cache-Control": "no-store", "WWW-Authenticate": 'Bearer realm="MachineGuard MCP"'})
            await response(scope, receive, send)
            return

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store")]
            await send(message)
        await self.app(scope, receive, secured_send)


def create_http_adapter(runtime):
    server = create_mcp_server(runtime)
    config = runtime.config
    app = server.streamable_http_app(streamable_http_path="/", json_response=True, stateless_http=True,
        max_request_body_size=config.max_body_bytes,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=config.allowed_hosts, allowed_origins=config.allowed_origins))
    return app, AuthenticatedHttp(app, runtime)
