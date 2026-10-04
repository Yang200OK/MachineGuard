"""资料目录及本人记录生命周期接口。"""
from uuid import UUID
from fastapi import Depends, Request, Query
from pydantic import Field, field_validator
from .schemas import StrictRequest
from . import workspace_service as service
from .document_models import DocumentVersion
from .document_service import authorize, version_view
from .auth_service import ServiceError


class DirectoryRequest(StrictRequest):
    category: str | None = Field(default=None, max_length=40)
    archived: bool | None = None


class JobRecordsRequest(StrictRequest):
    ids: list[UUID] = Field(min_length=1, max_length=50)
    restore: bool = False

    @field_validator("ids", mode="before")
    @classmethod
    def job_identifiers(cls, value):
        from .index_routes import PublishRequest
        if not isinstance(value, list):
            raise ValueError("任务编号必须为列表。")
        # 严格请求模式显式转换 JSON 字符串，沿用已有 UUID 格式校验。
        return [PublishRequest.json_uuid(item) for item in value]


def register_workspace_routes(app, get_session, current, envelope):
    @app.get("/api/knowledge/catalog")
    def catalog(request: Request, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=100),
                search: str = Query("", max_length=200), category: str = Query("", max_length=40),
                state: str = Query("", max_length=30), archived: bool = False,
                principal=Depends(current), session=Depends(get_session)):
        return envelope(request, service.catalog(session, principal, offset, limit, search, category, state, archived))

    @app.patch("/api/knowledge/documents/{document_id}/directory")
    def directory(request: Request, document_id: UUID, body: DirectoryRequest, principal=Depends(current), session=Depends(get_session)):
        result = service.manage_directory(session, principal, document_id, body.category, body.archived, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.get("/api/knowledge/documents/{document_id}/versions/{version_id}")
    def version(request: Request, document_id: UUID, version_id: UUID, principal=Depends(current), session=Depends(get_session)):
        authorize(session, principal); row = session.get(DocumentVersion, version_id)
        if row is None or row.document_id != document_id:
            raise ServiceError(404, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在。")
        return envelope(request, version_view(session, row))

    @app.post("/api/conversations/{conversation_id}/delete")
    def delete(request: Request, conversation_id: UUID, principal=Depends(current), session=Depends(get_session)):
        result = service.conversation_lifecycle(session, principal, conversation_id, False, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.post("/api/conversations/{conversation_id}/restore")
    def restore(request: Request, conversation_id: UUID, principal=Depends(current), session=Depends(get_session)):
        result = service.conversation_lifecycle(session, principal, conversation_id, True, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.post("/api/job-records")
    def records(request: Request, body: JobRecordsRequest, principal=Depends(current), session=Depends(get_session)):
        principal.require("system:read")
        result = service.job_lifecycle(session, principal, body.ids, body.restore, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.get("/api/chat/{answer_id}/navigation")
    def navigation(request: Request, answer_id: UUID, principal=Depends(current), session=Depends(get_session)):
        return envelope(request, service.answer_navigation(session, principal, answer_id))
