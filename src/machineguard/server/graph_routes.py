"""有来源图谱的候选、审核、原文和有界路径接口。"""

from typing import Literal
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import Field, field_validator

from . import graph_service as service
from .schemas import StrictRequest
from .graph_path_service import PathArguments, search_paths
from .auth_service import audit

EntityKind = Literal["设备类型", "组件", "现象", "概念", "检查步骤", "规则", "单位", "文档", "章节"]
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class RelationRequest(StrictRequest):
    idempotency_key: str = Field(pattern=UUID_PATTERN)
    snapshot_id: str = Field(pattern=UUID_PATTERN)
    chunk_id: str = Field(pattern=UUID_PATTERN)
    chunk_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote_start: int = Field(ge=0)
    quote_end: int = Field(gt=0)
    subject: str = Field(min_length=1, max_length=120)
    subject_kind: EntityKind
    object: str = Field(min_length=1, max_length=120)
    object_kind: EntityKind
    predicate: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("subject", "object", "predicate", "note")
    @classmethod
    def meaningful(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("必须填写有效文字，不能包含控制字符。")
        return value


class GraphReviewRequest(StrictRequest):
    idempotency_key: str = Field(pattern=UUID_PATTERN)
    expected_revision: int = Field(ge=1)
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: str = Field(min_length=1, max_length=1000)
    evidence_checked: bool = False
    direction_checked: bool = False

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        return RelationRequest.meaningful(value)


def register_graph_routes(app, get_session, current, envelope):
    @app.post("/api/graph/search")
    def search(request: Request, response: Response, payload: PathArguments,
               principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        result = search_paths(session, principal, payload)
        audit(session, principal.user.id, "graph.path_searched", "knowledge_snapshot", UUID(result["snapshot_id"]),
            request.state.request_id, {"status": result["status"], "paths": len(result["paths"]),
                "max_hops": payload.max_hops, "query_sha256": service.digest(result["query"])})
        session.commit()
        return envelope(request, result)

    @app.get("/api/graph/sources")
    def sources(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50),
                principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.sources(session, principal, offset, limit))

    @app.get("/api/graph/entities")
    def entities(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
                 principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.entities(session, principal, offset, limit))

    @app.get("/api/graph/relations")
    def relations(request: Request, response: Response, status: Literal["all", "pending", "approved", "rejected", "withdrawn"] = "all",
                  available_only: bool = False, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
                  principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.relations(session, principal, status, available_only, offset, limit))

    @app.get("/api/graph/relations/{relation_id}")
    def detail(request: Request, response: Response, relation_id: UUID, principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.detail(session, principal, relation_id))

    @app.get("/api/graph/relations/{relation_id}/source")
    def source(request: Request, response: Response, relation_id: UUID, principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.source(session, principal, relation_id))

    @app.post("/api/graph/relations", status_code=201)
    def create(request: Request, payload: RelationRequest, principal=Depends(current), session=Depends(get_session)):
        result = service.create_relation(session, principal, payload, request.state.request_id)
        session.commit()
        return envelope(request, result)

    @app.post("/api/graph/relations/{relation_id}/review")
    def review(request: Request, relation_id: UUID, payload: GraphReviewRequest,
               principal=Depends(current), session=Depends(get_session)):
        result = service.review(session, principal, relation_id, payload, request.state.request_id)
        session.commit()
        return envelope(request, result)
