"""审核队列、有限引用访问及正式报告入口。"""

from typing import Literal
from uuid import UUID
from fastapi import Depends, Query, Request, Response
from pydantic import Field, field_validator
from .schemas import StrictRequest
from .index_routes import PublishRequest
from . import report_service as service, agent_service


class ApprovalRequest(StrictRequest):
    decision: Literal["approved", "rejected"]
    comment: str = Field(min_length=1, max_length=2000)
    idempotency_key: UUID
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_id: str = Field(min_length=1, max_length=64)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def key_uuid(cls, value):
        return PublishRequest.json_uuid(value)

    @field_validator("comment")
    @classmethod
    def comment_text(cls, value):
        if not value.strip() or any(ord(char) < 32 and char not in "\n\t\r" for char in value):
            raise ValueError("审核意见不能为空或包含控制字符。")
        return value.strip()


class AssignmentRequest(StrictRequest):
    reviewer_id: UUID | None

    @field_validator("reviewer_id", mode="before")
    @classmethod
    def reviewer_uuid(cls, value):
        return None if value is None else PublishRequest.json_uuid(value)


def register_report_routes(app, get_session, current, envelope):
    @app.get("/api/reviews")
    def listing(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50),
                view: Literal["active", "history", "all"] = Query("active"),
                principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.list_reviews(session, principal, offset, limit, view))

    @app.get("/api/reviewers")
    def reviewers(request: Request, principal=Depends(current), session=Depends(get_session)):
        return envelope(request, service.reviewers(session, principal))

    @app.get("/api/reviews/{run_id}")
    def detail(request: Request, response: Response, run_id: UUID, principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        row, _ = service.review_row(session, principal, run_id)
        return envelope(request, service.review_view(session, row))

    @app.post("/api/reviews/{run_id}/assignment")
    def assign(request: Request, run_id: UUID, payload: AssignmentRequest, principal=Depends(current), session=Depends(get_session)):
        result = service.assign(session, principal, run_id, payload.reviewer_id, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.post("/api/agent/runs/{run_id}/approval")
    def approval(request: Request, run_id: UUID, payload: ApprovalRequest, principal=Depends(current), session=Depends(get_session)):
        result = service.decide(session, principal, run_id, payload, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.get("/api/reviews/{run_id}/citations/{claim_index}/{citation_index}")
    def review_source(request: Request, response: Response, run_id: UUID, claim_index: int, citation_index: int,
                      principal=Depends(current), session=Depends(get_session)):
        row, _ = service.review_row(session, principal, run_id)
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, agent_service.citation_source(session, row, claim_index, citation_index))

    @app.get("/api/reports")
    def reports(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50),
                principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.list_reports(session, principal, offset, limit))

    @app.get("/api/reports/{report_id}")
    def report(request: Request, response: Response, report_id: UUID, principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.report_view(service.authorized_report(session, principal, report_id), session))

    @app.get("/api/reports/{report_id}/citations/{claim_index}/{citation_index}")
    def report_source(request: Request, response: Response, report_id: UUID, claim_index: int, citation_index: int,
                      principal=Depends(current), session=Depends(get_session)):
        report = service.authorized_report(session, principal, report_id)
        # 正式报告引用只允许访问已保存条目，不扩大为知识管理权限。
        references = [item for item in report.content["sections"] if item.get("claim_index") == claim_index]
        if not references or not 0 <= citation_index < len(references[0]["citations"]):
            from .auth_service import ServiceError
            raise ServiceError(404, "QA_CITATION_NOT_FOUND", "正式报告引用不存在。")
        from .qa_source import checked_citation_source
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, checked_citation_source(session, references[0]["citations"][citation_index]))
