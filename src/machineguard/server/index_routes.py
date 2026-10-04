"""管理员知识快照发布与双路检索预览。"""

from uuid import UUID
import re

from fastapi import Depends, Query, Request, Response
from pydantic import Field, field_validator

from . import index_service as service
from .job_config import load_worker_configuration
from .schemas import StrictRequest


class PublishRequest(StrictRequest):
    parse_id: UUID
    expected_revision: int = Field(ge=0)
    idempotency_key: UUID

    @field_validator("parse_id", "idempotency_key", mode="before")
    @classmethod
    def json_uuid(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value):
            raise ValueError("标识必须为标准 UUID 字符串。")
        return UUID(value)


class SearchRequest(StrictRequest):
    query: str = Field(min_length=1, max_length=1000)
    snapshot_id: UUID | None = None
    equipment_type: str | None = Field(default=None, min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def json_uuid(cls, value):
        return None if value is None else PublishRequest.json_uuid(value)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        if not value.strip() or any(ord(x) < 32 and x not in "\n\r\t" for x in value):
            raise ValueError("查询不能为空或包含非法控制字符。")
        return value.strip()


def register_index_routes(app, get_session, current, envelope):
    @app.post("/api/knowledge/versions/{version_id}/publish")
    def publish(request: Request, version_id: UUID, payload: PublishRequest, principal=Depends(current), session=Depends(get_session)):
        result = service.submit_publication(session, principal, version_id, payload, load_worker_configuration(), request.state.request_id)
        session.commit()
        return envelope(request, result)

    @app.get("/api/knowledge/snapshots")
    def snapshots(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50),
                  principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.list_snapshots(session, principal, offset, limit))

    @app.post("/api/knowledge/search-preview")
    def preview(request: Request, payload: SearchRequest, principal=Depends(current), session=Depends(get_session)):
        return envelope(request, service.search_preview(session, principal, payload))
