"""管理员发布前管理接口；审核通过不等同于建立检索索引。"""

from typing import Literal
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import Field, field_validator

from . import knowledge_service as service
from .document_schemas import SourceMetadata
from .parse_service import _parse
from .schemas import StrictRequest


class ManagementRequest(StrictRequest):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("必须填写有效的纠正、停用或审核理由。")
        return value


class MetadataRequest(ManagementRequest):
    metadata: SourceMetadata


class ReviewRequest(ManagementRequest):
    decision: Literal["approved", "rejected"]
    metadata_revision: int = Field(ge=0)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_checked: bool
    content_checked: bool
    location_checked: bool


def register_knowledge_routes(app, get_session, current, envelope):
    @app.get("/api/knowledge/versions/{version_id}/management")
    def history(request: Request, response: Response, version_id: UUID, offset: int = Query(0, ge=0),
                limit: int = Query(10, ge=1, le=50), principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.management_history(session, principal, version_id, offset, limit))

    @app.patch("/api/knowledge/versions/{version_id}/metadata")
    def metadata(request: Request, version_id: UUID, payload: MetadataRequest,
                 principal=Depends(current), session=Depends(get_session)):
        result = service.manage(session, principal, version_id, "metadata", payload, request.state.request_id)
        session.commit()
        return envelope(request, result)

    @app.post("/api/knowledge/versions/{version_id}/deactivate")
    def deactivate(request: Request, version_id: UUID, payload: ManagementRequest,
                   principal=Depends(current), session=Depends(get_session)):
        result = service.manage(session, principal, version_id, "deactivate", payload, request.state.request_id)
        session.commit()
        return envelope(request, result)

    @app.post("/api/knowledge/parses/{parse_id}/review")
    def review(request: Request, parse_id: UUID, payload: ReviewRequest,
               principal=Depends(current), session=Depends(get_session)):
        parsed = _parse(session, principal, parse_id)
        result = service.manage(session, principal, parsed.version_id, "review", payload, request.state.request_id, parse_id)
        session.commit()
        return envelope(request, result)
