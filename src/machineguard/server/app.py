"""FastAPI 身份入口，统一请求编号、错误与 PostgreSQL 事务。"""

from contextlib import asynccontextmanager
import logging
import time
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from . import auth_service
from .database import Database, SCHEMA_REVISION
from .models import User
from .schemas import CreateUserRequest, LoginRequest, UpdateUserRequest
from .settings import ServerSettings
from .business_config import load_business_configuration
from .device_service import DeviceRuntime
from .device_routes import register_device_routes
from .job_routes import register_job_routes
from .document_routes import register_document_routes
from .parse_routes import register_parse_routes
from .knowledge_routes import register_knowledge_routes
from .index_routes import register_index_routes
from .qa_routes import register_qa_routes
from .workspace_routes import register_workspace_routes
from .skill_loader import SkillRegistry
from .skill_routes import register_skill_routes
from .agent_routes import register_agent_routes
from .report_routes import register_report_routes
from .run_stream import register_stream_routes
from .delivery_routes import register_delivery_routes
from .graph_routes import register_graph_routes
from .mcp_config import load_mcp_configuration
from .mcp_service import McpRuntime
from .mcp_routes import register_mcp_routes
from machineguard.mcp.server import create_http_adapter


LOGGER = logging.getLogger("machineguard.api")


class UTF8JSONResponse(JSONResponse):
    """显式声明 UTF-8，保证 Windows PowerShell 5.1 正确解码中文。"""

    media_type = "application/json; charset=utf-8"


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    settings = ServerSettings() if settings is None else settings
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        # 仓库 Skill 必须先通过版本与哈希校验，启动后不自动替换正文。
        app.state.skill_registry = SkillRegistry()
        try:
            database.check()
        except Exception:
            database.engine.dispose()
            raise RuntimeError("数据库不可用或迁移版本不匹配；请检查本地配置并完成迁移。") from None
        try:
            app.state.device_runtime = DeviceRuntime(load_business_configuration())
            with database.sessions() as session:
                app.state.device_runtime.check_registration(session)
        except Exception:
            database.engine.dispose()
            raise RuntimeError("部署包校验或模型登记失败；请检查业务配置并运行 import_business.py。") from None
        app.state.mcp_runtime.runtime = app.state.device_runtime
        app.state.mcp_runtime.registry = app.state.skill_registry
        try:
            async with app.state.mcp_http.router.lifespan_context(app.state.mcp_http):
                yield
        finally:
            database.engine.dispose()

    app = FastAPI(title="MachineGuard", version="0.1.0", lifespan=lifespan,
                  default_response_class=UTF8JSONResponse)
    app.state.database = database
    app.state.mcp_config = load_mcp_configuration()
    app.state.mcp_runtime = McpRuntime(database, None, None, app.state.mcp_config)
    app.state.mcp_http, mcp_adapter = create_http_adapter(app.state.mcp_runtime)
    app.mount("/mcp", mcp_adapter)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Last-Event-ID", "MCP-Protocol-Version", "Mcp-Session-Id"],
                       expose_headers=["X-Request-ID", "Content-Disposition", "X-Export-SHA256", "Mcp-Session-Id"])

    def envelope(request: Request, data) -> dict:
        return {"request_id": str(request.state.request_id), "data": data}

    def error_response(request: Request, status: int, code: str, message: str, details=None):
        body = {"request_id": str(request.state.request_id), "error": {"code": code, "message": message}}
        if details is not None:
            body["error"]["details"] = details
        return UTF8JSONResponse(status_code=status, content=jsonable_encoder(body),
                            headers={"X-Request-ID": str(request.state.request_id), **({"WWW-Authenticate": "Bearer"} if status == 401 else {})})

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID")
        request.state.request_id = uuid4()
        started = time.perf_counter()
        if supplied is not None:
            try:
                request.state.request_id = UUID(supplied)
            except ValueError:
                return error_response(request, 400, "REQUEST_ID_INVALID", "X-Request-ID 必须为 UUID。")
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        LOGGER.info("request_id=%s method=%s status=%s duration_ms=%.3f", request.state.request_id,
                    request.method, response.status_code, (time.perf_counter() - started) * 1000)
        return response

    @app.exception_handler(auth_service.ServiceError)
    async def service_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # 不回显 input，避免登录密码进入失败响应和日志。
        details = [{"loc": item["loc"], "type": item["type"], "message": "参数不符合约束。"} for item in error.errors()]
        return error_response(request, 422, "VALIDATION_ERROR", "请求参数不符合要求。", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(request, error.status_code, "HTTP_ERROR", "请求路径或方法不可用。")

    @app.exception_handler(IntegrityError)
    async def conflict_error(request, error):
        return error_response(request, 409, "DATA_CONFLICT", "请求与现有数据约束冲突。")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        LOGGER.error("database_error request_id=%s type=%s", request.state.request_id, type(error).__name__)
        return error_response(request, 503, "DATABASE_ERROR", "数据库操作失败，请联系管理员核对服务状态。")

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        LOGGER.error("server_error request_id=%s type=%s", request.state.request_id, type(error).__name__)
        return error_response(request, 500, "INTERNAL_ERROR", "服务处理失败，请根据请求编号排查。")

    def get_session():
        with database.sessions() as session:
            yield session

    bearer = HTTPBearer(auto_error=False)

    def current(session: Session = Depends(get_session), credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise auth_service.ServiceError(401, "SESSION_REQUIRED", "请先登录或申请访客会话。")
        return auth_service.authenticate(session, credentials.credentials)

    register_mcp_routes(app, get_session, current, envelope)

    @app.get("/api/health")
    def health(request: Request):
        database.check()
        return envelope(request, {"status": "ok", "database": "postgresql", "schema_revision": SCHEMA_REVISION})

    @app.post("/api/auth/login")
    def login(request: Request, payload: LoginRequest, session: Session = Depends(get_session)):
        result = auth_service.login(session, payload.username, payload.password.get_secret_value(), settings, request.state.request_id)
        session.commit()
        return envelope(request, result)

    @app.post("/api/auth/guest", status_code=201)
    def guest(request: Request, session: Session = Depends(get_session)):
        result = auth_service.guest_session(session, settings, request.state.request_id)
        session.commit()
        return envelope(request, result)

    @app.get("/api/auth/me")
    def me(request: Request, principal=Depends(current)):
        return envelope(request, {"user": auth_service.user_view(principal.user), "session_id": str(principal.session.id),
                                  "expires_at": principal.session.expires_at.isoformat()})

    @app.post("/api/auth/logout")
    def logout(request: Request, principal=Depends(current), session: Session = Depends(get_session)):
        auth_service.logout(session, principal, request.state.request_id)
        session.commit()
        return envelope(request, {"revoked": True})

    @app.get("/api/users")
    def users(request: Request, offset: int = 0, limit: int = 20, principal=Depends(current), session: Session = Depends(get_session)):
        principal.require("users:manage")
        if offset < 0 or not 1 <= limit <= 100:
            raise auth_service.ServiceError(422, "PAGINATION_INVALID", "分页参数超出范围。")
        rows = session.scalars(select(User).where(User.is_guest.is_(False)).order_by(User.created_at, User.id).offset(offset).limit(limit)).all()
        return envelope(request, {"items": [auth_service.user_view(user) for user in rows], "offset": offset, "limit": limit})

    @app.post("/api/users", status_code=201)
    def create_user(request: Request, payload: CreateUserRequest, principal=Depends(current), session: Session = Depends(get_session)):
        user = auth_service.create_user(session, principal, payload, request.state.request_id)
        result = auth_service.user_view(user)
        session.commit()
        return envelope(request, result)

    @app.patch("/api/users/{user_id}")
    def update_user(request: Request, user_id: UUID, payload: UpdateUserRequest, principal=Depends(current), session: Session = Depends(get_session)):
        user = auth_service.update_user(session, principal, user_id, payload, request.state.request_id)
        result = auth_service.user_view(user)
        session.commit()
        return envelope(request, result)

    register_device_routes(app, get_session, current, envelope)
    register_job_routes(app, get_session, current, envelope)
    register_document_routes(app, get_session, current, envelope)
    register_parse_routes(app, get_session, current, envelope)
    register_knowledge_routes(app, get_session, current, envelope)
    register_index_routes(app, get_session, current, envelope)
    register_qa_routes(app, get_session, current, envelope)
    register_workspace_routes(app, get_session, current, envelope)
    register_skill_routes(app, get_session, current, envelope)
    register_agent_routes(app, get_session, current, envelope)
    register_report_routes(app, get_session, current, envelope)
    register_stream_routes(app)
    register_delivery_routes(app, get_session, current, envelope)
    register_graph_routes(app, get_session, current, envelope)
    return app
