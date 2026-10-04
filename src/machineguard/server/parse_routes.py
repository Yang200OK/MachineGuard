"""管理员显式提交解析任务与读取有界结果，不在 API 内执行解析。"""

from typing import Literal
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy import select, func

from . import parse_service
from .job_config import load_worker_configuration
from .schemas import StrictRequest
from .document_models import DocumentVersion
from .parse_models import DocumentParse
from .document_service import authorize
from .auth_service import ServiceError


class ParseRequest(StrictRequest):
    mode: Literal["native", "vision"]
    idempotency_key: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def register_parse_routes(app, get_session, current, envelope):
    config = load_worker_configuration()

    @app.get("/api/knowledge/versions/{version_id}/parses")
    def runs(request: Request, response: Response, version_id: UUID, offset: int = Query(0, ge=0),
             limit: int = Query(20, ge=1, le=50), principal=Depends(current), session=Depends(get_session)):
        authorize(session, principal)
        if session.get(DocumentVersion, version_id) is None:
            raise ServiceError(404, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在。")
        rows = session.scalars(select(DocumentParse).where(DocumentParse.version_id == version_id)
            .order_by(DocumentParse.created_at.desc(), DocumentParse.id.desc()).offset(offset).limit(limit)).all()
        total = session.scalar(select(func.count()).select_from(DocumentParse).where(DocumentParse.version_id == version_id))
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, {"items": [parse_service.parse_view(session, row) for row in rows], "total": total,
                                  "offset": offset, "limit": limit})

    @app.post("/api/knowledge/versions/{version_id}/parse", status_code=202)
    def submit(request: Request, response: Response, version_id: UUID, payload: ParseRequest,
               principal=Depends(current), session=Depends(get_session)):
        result, existing = parse_service.submit_parse(session, principal, version_id, payload, config, request.state.request_id)
        session.commit(); response.status_code = 200 if existing else 202
        return envelope(request, result)

    @app.get("/api/knowledge/parses/{parse_id}")
    def detail(request: Request, response: Response, parse_id: UUID, principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, parse_service.parse_detail(session, principal, parse_id))

    @app.get("/api/knowledge/parses/{parse_id}/pages")
    def pages(request: Request, response: Response, parse_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=20),
              principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, parse_service.parse_content(session, principal, parse_id, "pages", offset, limit))

    @app.get("/api/knowledge/parses/{parse_id}/chunks")
    def chunks(request: Request, response: Response, parse_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
               principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, parse_service.parse_content(session, principal, parse_id, "chunks", offset, limit))
